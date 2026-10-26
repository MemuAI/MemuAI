

const ILL = {
  music: '<svg class="ill" viewBox="0 0 180 140"><defs><linearGradient id="ill-a" x1="0" x2="1" y1="0" y2="1"><stop offset="0" style="stop-color:var(--tint)"/><stop offset="1" style="stop-color:var(--tint-2)"/></linearGradient></defs><circle cx="96" cy="72" r="54" style="fill:var(--mm-panel-2)"/><circle cx="96" cy="72" r="40" fill="url(#ill-a)" opacity=".9"/><circle cx="96" cy="72" r="28" fill="none" stroke="#fff" stroke-opacity=".25"/><circle cx="96" cy="72" r="8" fill="#fff"/><path d="M36 40v40" stroke="url(#ill-a)" stroke-width="5" stroke-linecap="round"/><circle cx="30" cy="82" r="8" fill="url(#ill-a)"/><path d="M36 40l14-6" stroke="url(#ill-a)" stroke-width="5" stroke-linecap="round"/><circle cx="152" cy="28" r="5" style="fill:var(--tint-3)"/><circle cx="160" cy="112" r="3" style="fill:var(--tint)"/></svg>',
  search: '<svg class="ill" viewBox="0 0 180 140"><circle cx="82" cy="64" r="40" style="fill:var(--mm-panel-2)"/><circle cx="82" cy="64" r="26" fill="none" style="stroke:var(--tint)" stroke-width="8"/><path d="m102 84 26 26" style="stroke:var(--tint-2)" stroke-width="10" stroke-linecap="round"/><circle cx="146" cy="34" r="5" style="fill:var(--tint-3)"/></svg>',
  download: '<svg class="ill" viewBox="0 0 180 140"><rect x="40" y="22" width="100" height="96" rx="26" style="fill:var(--mm-panel-2)"/><path d="M90 40v44M72 68l18 18 18-18" fill="none" style="stroke:var(--tint)" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/><path d="M64 102h52" style="stroke:var(--tint-2)" stroke-width="8" stroke-linecap="round"/></svg>',
  error: '<svg class="ill" viewBox="0 0 180 140"><circle cx="90" cy="70" r="46" style="fill:var(--mm-panel-2)"/><path d="M90 46v30" stroke="#f87171" stroke-width="9" stroke-linecap="round"/><circle cx="90" cy="94" r="6" fill="#f87171"/></svg>',
};
function emptyState(kind, title, text, action) {
  return `<div class="mm-empty">${ILL[kind] || ILL.music}<b>${esc(title)}</b>${text ? `<p>${esc(text)}</p>` : ""}${action || ""}</div>`;
}
function collage(p) {
  const ids = (p.track_ids || []).slice(0, 4);
  const items = ids.map((id) => S.byId.get(id)).filter(Boolean);
  if (p.cover) return `<img alt="" src="${esc(p.cover)}" data-fb="${esc(coverArt(p.id || p.name, p.name))}">`;
  const covers = (p.covers || []).filter(Boolean);
  if (!items.length && covers.length >= 4) return `<span class="mm-collage" style="width:100%;height:100%">${covers.slice(0, 4).map((c, i) => `<img alt="" src="${esc(c)}" data-fb="${esc(coverArt((p.id || p.name) + i))}">`).join("")}</span>`;
  if (!items.length && covers.length) return `<img alt="" src="${esc(covers[0])}" data-fb="${esc(coverArt(p.id || p.name, p.name))}">`;
  if (items.length >= 4) return `<span class="mm-collage" style="width:100%;height:100%">${items.map((x) => artImg(x)).join("")}</span>`;
  if (items.length) return artImg(items[0]);

  if (p.smart || p.rules) return `<span class="mm-smart-art" style="--a:${p.id === "smart:top" ? "#ff8a3d" : "#2fb8a6"};--b:${p.id === "smart:top" ? "#ff3d77" : "#4f6bff"}">${icon(p.id === "smart:top" ? "stats" : p.id === "smart:recent" ? "plus" : "sparkle")}</span>`;
  return `<img alt="" src="${esc(coverArt("pl:" + (p.id || p.name), p.name))}">`;
}
function albumsOf(tracks) {
  const map = new Map();
  tracks.forEach((x) => {
    const key = x.artist + "|" + (x.album || x.title);
    if (!map.has(key)) map.set(key, { key, name: x.album || x.title, artist: x.artist, tracks: [], cover: x.cover || null, id: key, plays: 0, added: x.added_at || "", year: x.year || "" });
    const a = map.get(key); a.tracks.push(x); a.plays += x.plays || 0; if (!a.cover && x.cover) a.cover = x.cover; if ((x.added_at || "") > a.added) a.added = x.added_at;
  });
  return [...map.values()];
}
function artistsOf(tracks) {
  const map = new Map();
  tracks.forEach((x) => {
    if (!map.has(x.artist)) map.set(x.artist, { key: x.artist, name: x.artist, id: "artist:" + x.artist, tracks: [], plays: 0, cover: x.cover || null, added: x.added_at || "" });
    const a = map.get(x.artist); a.tracks.push(x); a.plays += x.plays || 0; if (!a.cover && x.cover) a.cover = x.cover; if ((x.added_at || "") > a.added) a.added = x.added_at;
  });
  return [...map.values()];
}
function tile(item, { sub, round, data } = {}) {
  const c = paletteFor(item)[0];
  return `<div class="mm-tile" role="button" tabindex="0" ${data || ""} style="--c:${c}"><div class="mm-tile-art${round ? " round" : ""}">${item.html || artImg(item, "album")}<button type="button" class="mm-fab" data-play-tile aria-label="${esc(t("play"))}">${icon("play")}</button></div><b>${esc(item.name || item.title)}</b><span>${esc(sub || "")}</span></div>`;
}
function headBlock({ kind, title, sub, art, extra }) {
  return `<header class="mm-head"><span class="mm-art">${art}</span><div style="min-width:0;flex:1"><span class="mm-kind">${esc(kind)}</span>${title}<p class="mm-sub">${sub}</p>
    <div class="mm-head-actions"><button type="button" class="mm-bigplay" data-act="play-all" aria-label="${esc(t("play"))}" title="${esc(t("play"))}">${icon("play")}</button>
    <button type="button" class="mm-icon lg" data-act="shuffle-all" title="${esc(t("shuffle"))}" aria-label="${esc(t("shuffle"))}">${icon("shuffle")}</button>${extra || ""}</div></div></header>`;
}
function bindHeadPlay(el, getTracks, from) {
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const shown = (el.querySelector("#list") || {})._tracks;
    const list = shown && shown.length ? shown.slice() : getTracks(); if (!list.length) return;
    if (b.dataset.act === "play-all") { Player.setShuffle(false); Player.playList(list, null, from); }
    if (b.dataset.act === "shuffle-all") { Player.setShuffle(true); Player.playList(list, list[Math.floor(Math.random() * list.length)].id, from); }
  });
}
function totalDuration(list) { return list.reduce((s, x) => s + (Number(x.duration) || 0), 0); }

let ROW = 56;
function rowHtml(x, i, opts) {
  const playing = Player.current() && Player.current().id === x.id;
  return `<div class="mm-row${opts.virtual ? "" : " static"}${playing ? " playing" : ""}" role="row" data-id="${esc(x.id)}" data-i="${i}"${Sel.has(x.id) ? ' aria-selected="true"' : ""}${opts.virtual ? ` style="top:${i * ROW}px"` : ""}${opts.reorder ? ' draggable="true"' : ""}>
    <div class="n"><span>${opts.reorder ? "" : i + 1}</span>${opts.reorder ? `<span class="grip" style="position:absolute;right:0;top:50%;transform:translateY(-50%)">${icon("grip")}</span>` : ""}<button type="button" class="p-btn" data-play-row aria-label="${esc(t("play"))}"><svg class="p"><use href="#i-play"/></svg></button><i class="mm-eqmini eqm"><i></i><i></i><i></i></i></div>
    <div class="t"><span class="mm-art">${artImg(x)}</span><div><b>${esc(x.title)}</b><span>${esc(x.artist)}</span></div></div>
    <div class="al">${esc(x.album || "")}</div>
    <div class="src">${sourceIcon(x.source || "local")}</div>
    <div class="d">${fmtTime(x.duration)}</div>
    <div class="act"><button type="button" class="mm-icon sm mm-like" data-like="${esc(x.id)}" aria-pressed="${!!x.liked}" aria-label="${esc(t("like"))}">${icon("heart")}</button><button type="button" class="mm-icon sm" data-more aria-label="${esc(t("more"))}">${icon("more")}</button></div>
  </div>`;
}
function trackList(host, tracks, opts) {
  opts = Object.assign({ virtual: tracks.length > 120, from: "", reorder: false }, opts || {});
  if (opts.sortView) tracks = sortedForList(host, tracks, opts);
  host._tracks = tracks;
  ROW = Theme.row();
  host.innerHTML = theadHtml(opts) + `<div class="mm-tracks" role="grid"></div>`;
  bindThead(host, opts);
  const box = host.querySelector(".mm-tracks");
  let first = -1, last = -1;
  if (!opts.virtual) box.innerHTML = tracks.map((x, i) => rowHtml(x, i, opts)).join("");
  else {
    box.style.height = tracks.length * ROW + "px";
    const scroller = $("#main");
    let pending = false;
    const paint = () => {
      pending = false;
      if (!box.isConnected) { scroller.removeEventListener("scroll", onScroll); return; }
      const top = box.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
      const a = Math.max(0, Math.floor(-top / ROW) - 10), b = Math.min(tracks.length, Math.ceil((scroller.clientHeight - top) / ROW) + 10);
      if (a === first && b === last) return;
      first = a; last = b;
      let html = ""; for (let i = a; i < b; i++) html += rowHtml(tracks[i], i, opts);
      box.innerHTML = html;
    };
    const onScroll = () => { if (!pending) { pending = true; requestAnimationFrame(paint); } };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    new ResizeObserver(() => { first = -1; onScroll(); }).observe(box);
    paint();
  }
  const trackAt = (row) => tracks[Number(row.dataset.i)];
  box.addEventListener("click", (e) => {
    const row = e.target.closest(".mm-row"); if (!row) return;
    const x = trackAt(row);
    if (e.target.closest("[data-like]")) { toggleLike(x); return; }
    if (e.target.closest("[data-more]")) { trackMenu(x, e.target.closest("[data-more]"), opts); return; }
    if (e.target.closest("[data-play-row]")) { playFrom(tracks, x, opts.from); return; }
    Sel.click(e, row, tracks, opts);
  });
  Sel.lasso(box, tracks, opts);
  box.addEventListener("dblclick", (e) => { const row = e.target.closest(".mm-row"); if (row && !e.target.closest("button")) playFrom(tracks, trackAt(row), opts.from); });
  box.addEventListener("contextmenu", (e) => { const row = e.target.closest(".mm-row"); if (!row) return; e.preventDefault(); trackMenu(trackAt(row), { x: e.clientX, y: e.clientY }, opts); });
  if (opts.reorder) {
    const applyOrder = (ids) => {
      const map = new Map(tracks.map((x) => [x.id, x]));
      flipRows(box, ".mm-row", () => { tracks.splice(0, tracks.length, ...ids.map((id) => map.get(id)).filter(Boolean)); box.innerHTML = tracks.map((x, i) => rowHtml(x, i, opts)).join(""); });
      if (opts._base) opts._base = tracks.slice();
      opts.onReorder && opts.onReorder(tracks.map((x) => x.id));
    };
    bindReorder(box, ".mm-row", (from, to) => {
      const ids = tracks.map((x) => x.id); const [m] = ids.splice(from, 1); ids.splice(to > from ? to - 1 : to, 0, m);
      applyOrder(ids);
    });
    opts.moveTo = (ids, where) => applyOrder(movedOrder(tracks.map((x) => x.id), ids, where));
    reorderList = { alive: () => box.isConnected, move: opts.moveTo };
  }
  return { refresh() { first = -1; if (!opts.virtual) box.innerHTML = tracks.map((x, i) => rowHtml(x, i, opts)).join(""); else box.dispatchEvent(new Event("scroll")); } };
}
function playFrom(list, track, from) {
  const cur = Player.current();
  if (cur && cur.id === track.id) { Player.toggle(); return; }
  Player.playList(list, track.id, from);
}
function markPlaying() {
  const cur = Player.current();
  $$(".mm-row").forEach((r) => r.classList.toggle("playing", !!cur && r.dataset.id === cur.id));
}

function bindReorder(box, selector, onMove) {
  let from = -1;
  box.addEventListener("dragstart", (e) => {
    const row = e.target.closest(selector); if (!row) return;
    from = Number(row.dataset.i ?? row.dataset.pos); row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("application/x-memu-music", String(from));
  });
  box.addEventListener("dragend", () => { $$(".dragging, .drop-before", box).forEach((n) => n.classList.remove("dragging", "drop-before")); from = -1; });
  box.addEventListener("dragover", (e) => {
    if (from < 0) return; e.preventDefault();
    const row = e.target.closest(selector);
    $$(".drop-before", box).forEach((n) => n.classList.remove("drop-before"));
    if (row) row.classList.add("drop-before");
  });
  box.addEventListener("drop", (e) => {
    if (from < 0) return; e.preventDefault(); e.stopPropagation();
    const row = e.target.closest(selector);
    const rows = $$(selector, box);
    const to = row ? Number(row.dataset.i ?? row.dataset.pos) : Number(rows[rows.length - 1].dataset.i ?? rows[rows.length - 1].dataset.pos) + 1;
    const f = from; from = -1;
    $$(".dragging, .drop-before", box).forEach((n) => n.classList.remove("dragging", "drop-before"));
    if (to !== f && to !== f + 1) { if (selector.startsWith(".mm-qrow")) flipRows(box, ".mm-qrow[data-id]", () => onMove(f, to)); else onMove(f, to); }
  });
}

let menuCleanup = null;
function openMenu(items, anchor) {
  closeOverlays();
  const menu = $("#menu");
  menu.innerHTML = items.map((it, i) => it === "-" ? "<hr>" : `<button type="button" role="menuitem" data-i="${i}" class="${it.danger ? "danger" : ""}">${icon(it.icon)}<span>${esc(it.label)}</span></button>`).join("");
  menu.hidden = false;
  const r = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y };
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  menu.style.left = Math.max(8, Math.min(innerWidth - mw - 8, r.right - mw)) + "px";
  menu.style.top = (r.bottom + mh + 8 > innerHeight ? Math.max(8, r.top - mh - 6) : r.bottom + 6) + "px";
  const onClick = (e) => {
    const b = e.target.closest("button[data-i]");
    if (b) { const it = items[Number(b.dataset.i)]; closeOverlays(); it.run(); }
  };
  const outside = (e) => { if (!menu.contains(e.target)) closeOverlays(); };
  menu.addEventListener("click", onClick);
  setTimeout(() => document.addEventListener("pointerdown", outside, true));
  menu.querySelector("button") && menu.querySelector("button").focus();
  menuCleanup = () => { menu.removeEventListener("click", onClick); document.removeEventListener("pointerdown", outside, true); menu.hidden = true; };
}
function closeOverlays() {
  let closed = false;

  if (menuCleanup) { closed = !$("#menu").hidden; menuCleanup(); menuCleanup = null; }
  if (!$("#pop").hidden) { closePop(); closed = true; }
  if (!$("#modal").hidden) { $("#modal").hidden = true; closed = true; }
  return closed;
}
function modal(html, bind) {
  const m = $("#modal"), card = $("#modal-card");
  card.innerHTML = html; m.hidden = false;
  const close = () => { m.hidden = true; };
  m.onclick = (e) => { if (e.target === m || e.target.closest("[data-cancel]")) close(); };
  bind && bind(card, close);
  const focus = card.querySelector("input, button.mm-btn.tint"); focus && focus.focus();
}
function confirmDialog(title, text, onYes) {
  modal(`<h3>${esc(title)}</h3><p class="mm-sub" style="margin:0">${esc(text)}</p><div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" data-yes>${esc(t("delete.yes"))}</button></div>`,
    (card, close) => { card.querySelector("[data-yes]").onclick = async () => { close(); try { await onYes(); } catch (e) { toast(String(e.message || e)); } }; });
}
function createPlaylistDialog(trackIds) {
  modal(`<h3>${esc(t("playlist.new"))}</h3><input class="mm-input" id="pl-name" maxlength="80" placeholder="${esc(t("playlist.name_ph"))}"><div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" data-ok>${esc(t("playlist.create"))}</button></div>`,
    (card, close) => {
      const input = card.querySelector("#pl-name");
      const ok = async () => {
        const name = input.value.trim() || t("playlist.default_name", { n: S.playlists.length + 1 });
        close();
        const p = await API.call("playlist.create", { name, track_ids: trackIds || [] });
        await loadPlaylists();
        toast(t("toast.created", { name }));
        if (p && p.id && !trackIds) go("playlist", { id: p.id });
      };
      card.querySelector("[data-ok]").onclick = ok;
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") ok(); });
    });
}

function today() {
  try { return new Date().toLocaleDateString(Memu.lang === "en" ? "en-US" : "ru-RU", { weekday: "long", day: "numeric", month: "long" }); } catch (e) { return ""; }
}
function greeting() {
  const h = new Date().getHours();
  return t(h < 5 ? "greet.night" : h < 12 ? "greet.morning" : h < 18 ? "greet.day" : "greet.evening");
}
function smartMixes() {
  const tr = S.tracks;
  const day = Math.floor(Date.now() / 86400000);
  const rnd = seeded(day);
  const shuffled = (list) => list.map((x) => [rnd(), x]).sort((a, b) => a[0] - b[0]).map((p) => p[1]);
  const topArtists = artistsOf(tr).sort((a, b) => b.plays - a.plays).slice(0, 4).map((a) => a.name);
  const twoWeeks = new Date(Date.now() - 14 * 86400000).toISOString();
  return [
    { id: "daily", name: t("mix.daily"), sub: topArtists.slice(0, 3).join(", "), tracks: shuffled(tr.filter((x) => topArtists.includes(x.artist))).slice(0, 40) },
    { id: "repeat", name: t("mix.repeat"), sub: t("mix.repeat_sub"), tracks: tr.slice().sort((a, b) => (b.plays || 0) - (a.plays || 0)).slice(0, 30) },
    { id: "fresh", name: t("mix.fresh"), sub: t("mix.fresh_sub"), tracks: tr.filter((x) => (x.added_at || "") > twoWeeks).slice(0, 50) },
    { id: "forgotten", name: t("mix.forgotten"), sub: t("mix.forgotten_sub"), tracks: tr.filter((x) => x.liked).sort((a, b) => (a.plays || 0) - (b.plays || 0)).slice(0, 30) },
    { id: "discover", name: t("mix.discover"), sub: t("mix.discover_sub"), tracks: shuffled(tr.filter((x) => !x.liked && (x.plays || 0) < 3)).slice(0, 30) },
  ].filter((m) => m.tracks.length);
}
VIEWS.home = async (el) => {
  const tr = S.tracks;
  if (!S.loaded) {
    el.innerHTML = `<section class="mm-hero"><div><span class="mm-skel" style="display:block;height:14px;width:160px;margin:6px 0 10px"></span><div class="mm-skel" style="height:62px;width:min(420px,70%)"></div><div class="mm-skel" style="height:16px;width:220px;margin-top:10px"></div></div></section><div class="mm-quick">${'<div class="mm-skel mm-quick-skel"></div>'.repeat(6)}</div>`;
    return;
  }
  if (!tr.length) {
    el.innerHTML = `<div class="mm-hero"><h1 class="mm-greet">${esc(greeting())}</h1></div>` + emptyState("music", t("empty.library"), t("empty.library_hint"),
      `<div class="mm-head-actions"><button type="button" class="mm-btn tint" data-go-dl>${icon("download")}${esc(t("nav.download"))}</button><button type="button" class="mm-btn ghost" data-go-set>${icon("folder")}${esc(t("import.folder"))}</button></div>`);
    el.querySelector("[data-go-dl]").onclick = () => go("download");
    el.querySelector("[data-go-set]").onclick = () => go("settings");
    return;
  }
  const albums = albumsOf(tr).sort((a, b) => (b.added || "").localeCompare(a.added || ""));
  const flow = albums.slice(0, 14);
  const artists = artistsOf(tr).sort((a, b) => b.plays - a.plays).slice(0, 12);
  const liked = tr.filter((x) => x.liked);
  const mixes = smartMixes();

  const quick = [{ kind: "liked", name: t("liked.title"), sub: plural("count.tracks", liked.length), html: `<span style="width:100%;height:100%;display:grid;place-items:center;color:#fff;background:linear-gradient(135deg,#6d4aff,#ff5c8a)">${icon("heart")}</span>` }]
    .concat(S.playlists.slice(0, 3).map((p) => ({ kind: "playlist", id: p.id, name: p.name, sub: t("kind.playlist") + " · " + plural("count.tracks", p.count || 0), html: collage(p) })))
    .concat(albums.slice(0, 2).map((a) => ({ kind: "album", id: a.key, name: a.name, sub: t("kind.album") + (a.artist ? " · " + a.artist : ""), html: artImg(a.tracks[0]) })));
  const off = (k) => Theme.hidden(k) ? " hidden" : "";
  el.innerHTML = `
    <section class="mm-hero"${off("greet")}><div><span class="mm-eyebrow">${esc(today())}</span><h1 class="mm-greet">${esc(greeting())}</h1><p class="mm-sub">${esc(t("home.summary", { tracks: plural("count.tracks", tr.length), time: fmtLong(totalDuration(tr)) }))}</p></div></section>
    <div class="mm-quick"${off("quick")}>${quick.map((q, i) => `<div class="mm-quick-item" role="button" tabindex="0" data-q="${i}"><span class="mm-art">${q.html}</span><span class="mm-quick-txt"><b>${esc(q.name)}</b><small>${esc(q.sub)}</small></span><button type="button" class="mm-fab" data-play-q="${i}" aria-label="${esc(t("play"))}">${icon("play")}</button></div>`).join("")}</div>
    <section class="mm-section"${off("spotlight")}><div class="mm-section-head"><h2 class="mm-h2">${esc(t("home.spotlight"))}</h2></div>
      <div class="mm-flow" id="flow" tabindex="0" aria-roledescription="carousel"><div class="mm-flow-nav l"><button type="button" class="mm-icon" data-flow="-1" aria-label="${esc(t("prev"))}" style="transform:scaleX(-1)">${icon("next")}</button></div><div class="mm-flow-nav r"><button type="button" class="mm-icon" data-flow="1" aria-label="${esc(t("next"))}">${icon("next")}</button></div>
        <div class="mm-flow-stage">${flow.map((a) => `<div class="mm-flow-item">${artImg(a.tracks[0], "album")}</div>`).join("")}</div>
        <div class="mm-flow-caption"><b id="flow-title"></b><span id="flow-sub"></span></div></div></section>
    <section class="mm-section" id="continue-sec" hidden><div class="mm-section-head"><h2 class="mm-h2">${esc(t("home.continue"))}</h2></div><div class="mm-shelf" id="continue"></div></section>
    <section class="mm-section"${off("mixes")}><div class="mm-section-head"><h2 class="mm-h2">${esc(t("home.mixes"))}</h2><span class="mm-sub" style="margin:0">${esc(t("home.mixes_sub"))}</span></div>
      <div class="mm-mixes">${mixes.map((m, i) => { const c = paletteFor({ id: "mix" + m.id }); return `<button type="button" class="mm-mix" data-mix="${i}" style="--m1:${c[0]};--m2:${c[1]};--m3:${c[2]}">${icon("sparkle")}<b>${esc(m.name)}</b><span>${esc(m.sub)} · ${esc(plural("count.tracks", m.tracks.length))}</span></button>`; }).join("")}</div></section>
    <section class="mm-section"${off("recent")}><div class="mm-section-head"><h2 class="mm-h2">${esc(t("home.recent_added"))}</h2><button type="button" class="mm-link" data-all="library">${esc(t("show_all"))}</button></div>
      <div class="mm-shelf">${albums.slice(0, 12).map((a, i) => tile(Object.assign({}, a.tracks[0], { name: a.name, id: a.key, html: artImg(a.tracks[0], "album") }), { sub: a.artist, data: `data-album="${i}"` })).join("")}</div></section>
    ${liked.length ? `<section class="mm-section"${off("liked")}><div class="mm-section-head"><h2 class="mm-h2">${esc(t("liked.title"))}</h2><button type="button" class="mm-link" data-all="liked">${esc(t("show_all"))}</button></div>
      <div class="mm-shelf">${liked.slice(0, 12).map((x, i) => tile(Object.assign({}, x, { name: x.title }), { sub: x.artist, data: `data-liked="${i}"` })).join("")}</div></section>` : ""}
    <section class="mm-section"${off("artists")}><div class="mm-section-head"><h2 class="mm-h2">${esc(t("home.artists"))}</h2></div>
      <div class="mm-shelf">${artists.map((a, i) => tile(Object.assign({}, a.tracks[0], { name: a.name, html: artImg(a.tracks[0]) }), { sub: t("kind.artist"), round: true, data: `data-artist="${i}"` })).join("")}</div></section>`;

  const flowEl = el.querySelector("#flow");
  const car = Fx.Carousel(flowEl, flow, {
    onPlay: (a) => Player.playList(a.tracks, null, a.name),
    onFocus: (a) => { el.querySelector("#flow-title").textContent = a.name; el.querySelector("#flow-sub").textContent = a.artist + " · " + plural("count.tracks", a.tracks.length); },
  });
  el.querySelectorAll("[data-flow]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); Number(b.dataset.flow) > 0 ? car.next() : car.prev(); }));
  el.querySelectorAll("[data-flow]").forEach((b) => b.addEventListener("pointerdown", (e) => e.stopPropagation()));

  const qTracks = (q) => q.kind === "liked" ? liked : q.kind === "album" ? (albums.find((a) => a.key === q.id) || { tracks: [] }).tracks : null;
  el.addEventListener("click", async (e) => {
    const fabQ = e.target.closest("[data-play-q]");
    if (fabQ) { e.stopPropagation(); const q = quick[Number(fabQ.dataset.playQ)]; const list = qTracks(q) || ((await API.soft("playlist.get", { id: q.id }, {})).tracks || []); if (list.length) Player.playList(list, null, q.name); return; }
    const qi = e.target.closest("[data-q]");
    if (qi) { const q = quick[Number(qi.dataset.q)]; if (q.kind === "liked") go("liked"); else if (q.kind === "album") go("album", { key: q.id }); else go("playlist", { id: q.id }); return; }
    const mix = e.target.closest("[data-mix]"); if (mix) { const m = mixes[Number(mix.dataset.mix)]; go("mix", { id: m.id }); return; }
    const all = e.target.closest("[data-all]"); if (all) { go(all.dataset.all); return; }
    const tileEl = e.target.closest(".mm-tile"); if (!tileEl) return;
    const fab = e.target.closest("[data-play-tile]");
    if (tileEl.dataset.album) { const a = albums[Number(tileEl.dataset.album)]; fab ? Player.playList(a.tracks, null, a.name) : go("album", { key: a.key }); }
    else if (tileEl.dataset.liked) Player.playList(liked, liked[Number(tileEl.dataset.liked)].id, t("liked.title"));
    else if (tileEl.dataset.artist) { const a = artists[Number(tileEl.dataset.artist)]; fab ? Player.playList(a.tracks, null, a.name) : go("artist", { name: a.name }); }
    else if (tileEl.dataset.recent) { const list = el._recent || []; Player.playList(list, list[Number(tileEl.dataset.recent)].id, t("home.continue")); }
  });

  const showRecent = (recent) => {
    if (!el.isConnected || Theme.hidden("continue")) return;
    el._recent = recent;
    el.querySelector("#continue").innerHTML = recent.map((x, i) => tile(Object.assign({}, x, { name: x.title }), { sub: x.artist, data: `data-recent="${i}"` })).join("");
    el.querySelector("#continue-sec").hidden = !recent.length;
  };
  if (S.recentCache) showRecent(S.recentCache);
  const stats = await API.soft("library.stats", {}, null);
  const recent = ((stats && stats.recent) || []).map((x) => S.byId.get(x.id) || normTrack(x)).filter((x, i, arr) => arr.findIndex((y) => y.id === x.id) === i).slice(0, 12);
  const sig = (list) => (list || []).map((x) => x.id).join();
  if (sig(recent) !== sig(S.recentCache)) showRecent(recent);
  S.recentCache = recent;
};
VIEWS.mix = async (el, p) => {
  const m = smartMixes().find((x) => x.id === p.id);
  if (!m) { go("home"); return; }
  const c = paletteFor({ id: "mix" + m.id });
  el.innerHTML = headBlock({ kind: t("kind.mix"), title: `<h1 class="mm-h1">${esc(m.name)}</h1>`, sub: esc(m.sub + " · " + plural("count.tracks", m.tracks.length) + " · " + fmtLong(totalDuration(m.tracks))),
    art: `<span class="mm-mix" style="width:100%;height:100%;border-radius:0;--m1:${c[0]};--m2:${c[1]};--m3:${c[2]}">${icon("sparkle")}<b>${esc(m.name)}</b></span>` }) + `<div id="list"></div>`;
  bindHeadPlay(el, () => m.tracks, m.name);
  trackList(el.querySelector("#list"), m.tracks, { from: m.name, sortView: "mix", sortDefault: "pos:asc" });
};

VIEWS.library = async (el) => {
  const lib = S.prefs.lib;
  el.innerHTML = `<h1 class="mm-h1">${esc(t("nav.library"))}</h1><p class="mm-sub">${esc(plural("count.tracks", S.tracks.length))} · ${esc(fmtLong(totalDuration(S.tracks)))}</p>
    <div class="mm-toolbar">
      <div class="mm-tabs" role="tablist">${["tracks", "albums", "artists"].concat(libTabs()).map((k) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${lib.tab === k}">${esc(t("lib." + k))}</button>`).join("")}</div>
      <span class="mm-grow"></span>
      <div class="mm-search">${icon("search")}<input class="mm-input" id="lib-q" type="search" placeholder="${esc(t("lib.filter"))}" autocomplete="off"></div>
      <span id="lib-sort-slot"></span>
    </div>
    <div class="mm-toolbar" style="margin-top:-6px" id="lib-filters">${["all", "liked", "downloaded", "local"].map((k) => `<button type="button" class="mm-chip" data-filter="${k}" aria-pressed="${lib.filter === k}">${esc(t("filter." + k))}</button>`).join("")}</div>
    <div id="lib-body"></div>`;
  const body = el.querySelector("#lib-body"), q = el.querySelector("#lib-q");
  const filtered = () => {
    const query = q.value.trim().toLowerCase();
    let list = S.tracks;
    if (lib.filter === "liked") list = list.filter((x) => x.liked);
    else if (lib.filter === "downloaded") list = list.filter((x) => x.source && x.source !== "local");
    else if (lib.filter === "local") list = list.filter((x) => !x.source || x.source === "local");
    if (query) list = list.filter((x) => (x.title + " " + x.artist + " " + (x.album || "")).toLowerCase().includes(query));
    return list;
  };
  const GRID_SORT = { tracks: ["library", "added:desc", TRACK_SORTS], albums: ["albums", "added:desc", ["added", "name", "artist", "year", "tracks", "plays"]], artists: ["artists", "added:desc", ["added", "name", "tracks", "plays"]] };
  const sortSlot = () => { const g = GRID_SORT[lib.tab]; el.querySelector("#lib-sort-slot").innerHTML = g ? sortButton(g[0], g[1]) : ""; };
  const draw = () => {
    if (FEAT_LIB_TABS[lib.tab]) { if (!libTabs().includes(lib.tab)) lib.tab = "tracks"; else { FEAT_LIB_TABS[lib.tab](body); return; } }
    const list = filtered();
    if (!list.length) { body.innerHTML = S.tracks.length ? emptyState("search", t("empty.nothing"), t("empty.nothing_hint")) : emptyState("music", t("empty.library"), t("empty.library_hint")); return; }
    if (lib.tab === "tracks") { trackList(body, list, { from: t("nav.library"), virtual: list.length > 80, sortView: "library", sortDefault: "added:desc" }); return; }
    const g = GRID_SORT[lib.tab];
    const groups = Sorter.apply(lib.tab === "albums" ? albumsOf(list) : artistsOf(list), Sorter.get(g[0], g[1]));
    body.innerHTML = `<div class="mm-grid">${groups.map((g, i) => tile(Object.assign({}, g.tracks[0], { name: g.name, html: lib.tab === "albums" ? artImg(g.tracks[0], "album") : artistArt(g.name, g.tracks[0]) }),
      { sub: lib.tab === "albums" ? g.artist : plural("count.tracks", g.tracks.length), round: lib.tab === "artists", data: `data-g="${i}"` })).join("")}</div>`;
    body.onclick = (e) => {
      const tl = e.target.closest("[data-g]"); if (!tl) return;
      const g = groups[Number(tl.dataset.g)];
      if (e.target.closest("[data-play-tile]")) { Player.playList(g.tracks, null, g.name); return; }
      lib.tab === "albums" ? go("album", { key: g.key }) : go("artist", { name: g.name });
    };
  };
  el.querySelector(".mm-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (!b) return; lib.tab = b.dataset.tab; $$("[data-tab]", el).forEach((x) => x.setAttribute("aria-selected", String(x === b))); savePrefs(); sortSlot(); draw(); });
  el.querySelector("#lib-filters").addEventListener("click", (e) => { const b = e.target.closest("[data-filter]"); if (!b) return; lib.filter = b.dataset.filter; $$("[data-filter]", el).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); savePrefs(); draw(); });
  bindSortButton(el.querySelector("#lib-sort-slot"), [], () => { sortSlot(); draw(); });
  el.querySelector("#lib-sort-slot").addEventListener("click", (e) => { const b = e.target.closest("[data-grid-sort]"); if (b) { e.stopImmediatePropagation(); sortMenu(b, b.dataset.gridSort, GRID_SORT[lib.tab][2], b.dataset.def, () => { sortSlot(); draw(); }); } }, true);
  q.addEventListener("input", debounce(draw, 120));
  sortSlot(); draw();
};
VIEWS.liked = async (el) => {
  const list = S.tracks.filter((x) => x.liked);
  el.innerHTML = headBlock({ kind: t("kind.playlist"), title: `<h1 class="mm-h1">${esc(t("liked.title"))}</h1>`, sub: esc(plural("count.tracks", list.length) + " · " + fmtLong(totalDuration(list))),
    art: `<span style="width:100%;height:100%;display:grid;place-items:center;color:#fff;background:linear-gradient(135deg,#6d4aff,#ff5c8a)"><svg style="width:72px;height:72px"><use href="#i-heart"/></svg></span>` }) + `<div id="list"></div>`;
  bindHeadPlay(el, () => list, t("liked.title"));
  if (!list.length) el.querySelector("#list").innerHTML = emptyState("music", t("empty.liked"), t("empty.liked_hint"));
  else trackList(el.querySelector("#list"), list, { from: t("liked.title"), sortView: "liked", sortDefault: "liked:desc" });
};
VIEWS.album = async (el, p) => {
  const list = S.tracks.filter((x) => x.artist + "|" + (x.album || x.title) === p.key).sort((a, b) => (Number(a.track_no) || 999) - (Number(b.track_no) || 999) || a.title.localeCompare(b.title));
  if (!list.length) { el.innerHTML = emptyState("search", t("empty.nothing"), ""); return; }
  const a = list[0];
  el.innerHTML = headBlock({ kind: t("kind.album"), title: `<h1 class="mm-h1">${esc(a.album || a.title)}</h1>`, sub: `<button type="button" class="mm-link" data-artist style="font-weight:700;color:var(--mm-text)">${esc(a.artist)}</button> · ${esc(plural("count.tracks", list.length))} · ${esc(fmtLong(totalDuration(list)))}`,
    art: artImg(a, "album") }) + `<div id="list"></div>`;
  el.querySelector("[data-artist]").onclick = () => go("artist", { name: a.artist });
  bindHeadPlay(el, () => list, a.album);
  trackList(el.querySelector("#list"), list, { from: a.album, sortView: "album", sortDefault: "pos:asc" });
  decorateAlbum(el, list);
};
VIEWS.artist = async (el, p) => {
  const list = S.tracks.filter((x) => x.artist === p.name).sort((a, b) => (b.plays || 0) - (a.plays || 0));
  const albums = albumsOf(list);
  el.innerHTML = headBlock({ kind: t("kind.artist"), title: `<h1 class="mm-h1">${esc(p.name)}</h1>`, sub: esc(plural("count.tracks", list.length) + " · " + plural("count.albums", albums.length)),
    art: artistArt(p.name, list[0]) }) + `<div class="mm-section-head" style="margin-top:8px"><h2 class="mm-h2">${esc(t("artist.popular"))}</h2></div><div id="list"></div>
    ${albums.length > 1 ? `<section class="mm-section"><div class="mm-section-head"><h2 class="mm-h2">${esc(t("lib.albums"))}</h2></div><div class="mm-shelf">${albums.map((g, i) => tile(Object.assign({}, g.tracks[0], { name: g.name, html: artImg(g.tracks[0], "album") }), { sub: plural("count.tracks", g.tracks.length), data: `data-g="${i}"` })).join("")}</div></section>` : ""}`;
  el.querySelector(".mm-head .mm-art").style.borderRadius = "50%";
  bindHeadPlay(el, () => list, p.name);
  trackList(el.querySelector("#list"), list, { from: p.name, sortView: "artist", sortDefault: "plays:desc" });
  decorateArtist(el, p.name);
  el.addEventListener("click", (e) => { const tl = e.target.closest("[data-g]"); if (tl) { const g = albums[Number(tl.dataset.g)]; e.target.closest("[data-play-tile]") ? Player.playList(g.tracks, null, g.name) : go("album", { key: g.key }); } });
};

VIEWS.playlists = async (el) => {
  const liked = S.tracks.filter((x) => x.liked);
  const own = Sorter.apply(S.playlists.filter((p) => !p.smart && !p.rules), Sorter.get("playlists", "pos:asc"));
  const sortRank = (p) => (own.includes(p) ? 1000 + own.indexOf(p) : S.playlists.indexOf(p));
  el.innerHTML = `<div class="mm-hero"><div><h1 class="mm-h1">${esc(t("nav.playlists"))}</h1><p class="mm-sub">${esc(plural("count.playlists", S.playlists.length))}</p></div><div class="mm-hero-acts"><button type="button" class="mm-btn tint" id="pl-new">${icon("plus")}${esc(t("playlist.new"))}</button>${featPlaylistActions()}${sortButton("playlists", "pos:asc")}</div></div>
    <div class="mm-grid" style="margin-top:28px">
      ${likedHidden() ? "" : `<div class="mm-tile" role="button" tabindex="0" data-liked style="--c:#6d4aff"><div class="mm-tile-art"><span style="width:100%;height:100%;display:grid;place-items:center;color:#fff;background:linear-gradient(135deg,#6d4aff,#ff5c8a)"><svg style="width:56px;height:56px"><use href="#i-heart"/></svg></span><button type="button" class="mm-fab" data-play-tile aria-label="${esc(t("play"))}">${icon("play")}</button></div><b>${esc(t("liked.title"))}</b><span>${esc(plural("count.tracks", liked.length))}</span></div>`}
      ${S.playlists.map((p, i) => [p, i]).sort((a, b) => sortRank(a[0]) - sortRank(b[0])).map(([p, i]) => tile({ id: p.id, name: p.name, html: collage(p) }, { sub: plural("count.tracks", p.count || 0) + (p.duration ? " · " + fmtLong(p.duration) : ""), data: `data-p="${i}" data-id="${esc(p.id)}"` })).join("")}
      <button type="button" class="mm-tile" data-create style="text-align:center"><div class="mm-tile-art" style="display:grid;place-items:center;border:2px dashed var(--mm-line);box-shadow:none;background:none;color:var(--mm-muted)"><svg style="width:40px;height:40px"><use href="#i-plus"/></svg></div><b>${esc(t("playlist.new"))}</b><span>${esc(t("playlist.new_hint"))}</span></button>
    </div>`;
  el.querySelector("#pl-new").onclick = () => createPlaylistDialog();
  bindFeatPlaylists(el);
  bindSortButton(el, ["pos", "name", "updated", "count"], () => rerender());
  if (Sorter.get("playlists", "pos:asc").field === "pos") bindPlaylistDrag(el.querySelector(".mm-grid"), ".mm-tile[data-id]");
  el.addEventListener("click", async (e) => {
    if (e.target.closest("[data-create]")) { createPlaylistDialog(); return; }
    const fab = e.target.closest("[data-play-tile]");
    if (e.target.closest("[data-liked]")) { fab ? liked.length && Player.playList(liked, null, t("liked.title")) : go("liked"); return; }
    const tl = e.target.closest("[data-p]"); if (!tl) return;
    const p = S.playlists[Number(tl.dataset.p)];
    if (fab) { const full = await API.soft("playlist.get", { id: p.id }, null); if (full && full.tracks && full.tracks.length) Player.playList(full.tracks, null, p.name); }
    else go("playlist", { id: p.id });
  });
};
VIEWS.playlist = async (el, p) => {
  el.innerHTML = `<div class="mm-head"><div class="mm-skel" style="width:212px;height:212px;border-radius:18px"></div><div style="flex:1"><div class="mm-skel" style="height:44px;width:50%"></div><div class="mm-skel" style="height:16px;width:30%;margin-top:14px"></div></div></div>`;
  const pl = await API.soft("playlist.get", { id: p.id }, null);
  if (!el.isConnected) return;
  if (!pl) { el.innerHTML = emptyState("error", t("error.title"), t("playlist.missing")); return; }
  const list = (pl.tracks || []).map((x) => S.byId.get(x.id) || normTrack(x));
  const builtin = !!pl.smart || String(pl.id).startsWith("smart:");
  const smart = builtin || !!pl.rules;
  if (builtin) pl.name = (Memu.lang === "en" ? pl.name_en : pl.name_ru) || pl.name;
  el.innerHTML = headBlock({ kind: t(smart ? "kind.mix" : "kind.playlist"), title: smart ? `<h1 class="mm-h1">${esc(pl.name)}</h1>` : `<h1 class="mm-h1"><input class="mm-name-edit" id="pl-title" value="${esc(pl.name)}" maxlength="80" aria-label="${esc(t("playlist.rename"))}"></h1>`,
    sub: (pl.description ? `<span class="mm-pl-desc">${esc(pl.description)}</span>` : "") + esc(plural("count.tracks", list.length) + " · " + fmtLong(totalDuration(list)) + (smart ? "" : " · " + t("playlist.drag_hint"))), art: collage(Object.assign({}, pl, { track_ids: list.map((x) => x.id) })),
    extra: playlistHeadExtra(pl, builtin) + (smart ? "" : `<button type="button" class="mm-icon lg" id="pl-del" title="${esc(t("playlist.delete"))}" aria-label="${esc(t("playlist.delete"))}">${icon("trash")}</button>`) }) + `<div id="list"></div>`;
  bindHeadPlay(el, () => list, pl.name);
  bindPlaylistHead(el, pl, builtin);
  if (smart) {
    if (!list.length) el.querySelector("#list").innerHTML = emptyState("music", t("empty.nothing"), t(builtin ? "stats.no_history_hint" : "smart.empty_hint"));
    else trackList(el.querySelector("#list"), list, { from: pl.name, sortView: "pl:" + pl.id, sortDefault: "pos:asc" });
    return;
  }
  const title = el.querySelector("#pl-title");
  const rename = async () => { const name = title.value.trim(); if (name && name !== pl.name) { pl.name = name; await API.soft("playlist.update", { id: pl.id, name }); loadPlaylists(); } };
  title.addEventListener("change", rename); title.addEventListener("keydown", (e) => { if (e.key === "Enter") title.blur(); });
  el.querySelector("#pl-del").onclick = () => confirmDialog(t("playlist.delete"), t("playlist.delete_text", { name: pl.name }), async () => { await API.call("playlist.delete", { id: pl.id }); await loadPlaylists(); go("playlists"); });
  if (!list.length) { el.querySelector("#list").innerHTML = emptyState("music", t("empty.playlist"), t("empty.playlist_hint")); return; }
  trackList(el.querySelector("#list"), list, { from: pl.name, reorder: true, virtual: false, playlistId: pl.id, sortView: "pl:" + pl.id, sortDefault: "pos:asc",
    onReorder: async (ids) => { await API.soft("playlist.reorder", { id: pl.id, order: ids }); loadPlaylists(); } });
};

let onJobsUpdate = null;
function jobStatus(j) {
  const pct = Math.round((Number(j.progress) || 0) * 100);
  if (j.status === "cancelled" || (j.status === "error" && j.error === "cancelled")) return t("job.cancelled");
  if (j.status === "error") return t("job.error", { error: j.error || "" });
  if (j.status === "running" && j.total > 1) return t("job.items", { done: j.done || 0, total: j.total, pct });
  if (j.status === "done" && j.failed > 0) return t("job.done_failed", { n: j.failed });
  return t("job." + (j.status || "queued"), { pct });
}
const jobFailed = (j) => j.status === "error" && j.error !== "cancelled" || (j.status === "done" && j.failed > 0);
const jobBot = (j) => j.error_code === "youtube_bot" || (j.items || []).some((x) => x.error_code === "youtube_bot");
const itemsSig = (j) => (j.items || []).map((x) => x.status).join(",");

function jobItemsHtml(j) {
  const items = j.items || [];
  if (items.length < 2) return "";
  return `<details class="mm-job-items"><summary>${esc(t("job.tracklist", { total: items.length }))}</summary><ol>${items.map((x) => {
    const st = { pending: "queued", cancelled: "skipped" }[x.status] || (["queued", "running", "done", "exists", "error", "skipped"].includes(x.status) ? x.status : "queued");
    return `<li data-st="${st}"${x.error ? ` title="${esc(x.error)}"` : ""}><i></i><span><b>${esc(x.title || "—")}</b>${x.artist ? ` · ${esc(x.artist)}` : ""}</span><em>${esc(x.status === "error" && x.error ? x.error : t("job.item." + st))}</em></li>`;
  }).join("")}</ol></details>`;
}
function jobHtml(j) {
  const src = j.source || (detectLocal(j.url) || {}).source || "web";
  const pct = Math.round((Number(j.progress) || 0) * 100);
  const status = jobStatus(j);
  return `<div class="mm-job" data-job="${esc(j.job_id)}" data-status="${esc(j.status)}" data-sig="${esc(itemsSig(j))}" style="--p:${(Number(j.progress) || 0).toFixed(3)}">
    <span class="mm-art">${j.thumb ? `<img alt="" src="${esc(j.thumb)}" data-fb="${esc(coverArt(j.url || j.job_id))}">` : `<img alt="" src="${esc(coverArt(j.url || j.job_id))}">`}</span>
    <div style="min-width:0"><b>${esc(j.title || (j.kind === "import" ? t("import.folder") : j.url))}</b><div class="meta">${sourceIcon(src)}<span>${esc(sourceName(src))}</span>·<span class="st">${esc(status)}</span></div></div>
    <div class="mm-job-acts">${jobFailed(j) && jobBot(j) ? `<button type="button" class="mm-btn ghost sm" data-job-cookies>${esc(t("job.cookies"))}</button><button type="button" class="mm-btn ghost sm" data-job-tool>${esc(t("job.update_tool"))}</button>` : ""}${jobFailed(j) ? `<button type="button" class="mm-btn ghost sm" data-job-retry title="${esc(t("job.retry"))}">${icon("refresh")}${esc(t(j.status === "error" ? "job.retry_one" : "job.retry"))}</button>` : ""}${j.status === "done" && (j.track_ids || []).length ? `<button type="button" class="mm-btn ghost sm" data-job-play>${icon("play")}${esc(t("play"))}</button>` : j.status === "queued" || j.status === "running" ? `<button type="button" class="mm-icon sm" data-job-cancel aria-label="${esc(t("cancel"))}" title="${esc(t("cancel"))}">${icon("close")}</button>` : ""}</div>
    ${jobItemsHtml(j)}<span class="bar"><i></i></span></div>`;
}
VIEWS.download = async (el, p) => {
  const prefs = S.prefs;
  prefs.quality = prefs.quality || "best";
  el.innerHTML = `<section class="mm-dl-hero"><h1 class="mm-h1">${esc(t("download.title"))}</h1><p class="mm-sub">${esc(t("download.sub"))}</p>
      <div class="mm-dl-field"><span class="mm-dl-logo" id="dl-logo">${icon("link")}</span><input id="dl-url" type="url" autocomplete="off" spellcheck="false" placeholder="${esc(t("download.placeholder"))}" aria-label="${esc(t("download.placeholder"))}">
        <button type="button" class="mm-btn tint" id="dl-go" disabled>${icon("download")}${esc(t("download.go"))}</button></div>
      <div class="mm-dl-detect" id="dl-detect"><span>${esc(t("download.hint_keys"))}</span></div>
      <div class="mm-toolbar" style="margin:14px 0 0"><div class="mm-tabs" id="dl-quality">${["best", "normal", "small"].map((k) => `<button type="button" data-q="${k}" aria-selected="${prefs.quality === k}">${esc(t("quality." + k))}</button>`).join("")}</div><span class="mm-grow"></span>
        <div class="mm-sources">${["youtube", "ytmusic", "spotify", "apple", "soundcloud", "yandex", "vk", "deezer", "bandcamp", "tidal", "mixcloud", "vimeo"].map((s) => sourceIcon(s)).join("")}</div></div>
    </section>
    <section class="mm-section"><div class="mm-section-head"><h2 class="mm-h2">${esc(t("download.jobs"))}</h2><span class="mm-grow"></span><button type="button" class="mm-link" id="dl-clear" hidden>${esc(t("download.clear_done"))}</button></div><div class="mm-jobs" id="dl-jobs"></div></section>
    <section class="mm-section"><div class="mm-section-head"><h2 class="mm-h2">${esc(t("search.title"))}</h2></div>
      <div class="mm-search" style="max-width:520px">${icon("search")}<input class="mm-input" id="dl-q" type="search" style="width:100%;height:44px" placeholder="${esc(t("search.placeholder"))}" autocomplete="off"></div>
      <div id="dl-results" style="margin-top:14px"></div></section>`;
  const input = el.querySelector("#dl-url"), logo = el.querySelector("#dl-logo"), det = el.querySelector("#dl-detect"), goBtn = el.querySelector("#dl-go");
  let hidden = new Set();
  const showDetect = (d, extra) => {
    if (!d) {
      logo.className = "mm-dl-logo"; logo.style.background = ""; logo.innerHTML = icon("link"); goBtn.disabled = true;
      det.innerHTML = input.value.trim() ? `<span>${esc(t("download.not_link"))}</span>` : `<span>${esc(t("download.hint_keys"))}</span>`; return;
    }
    const s = sourceInfo(d.source);
    logo.className = "mm-dl-logo on"; logo.style.background = s.color; logo.style.color = s.ink || "#fff"; logo.innerHTML = `<svg viewBox="0 0 24 24">${s.icon}</svg>`;
    goBtn.disabled = false;
    det.innerHTML = `<b>${esc(sourceName(d.source))}</b><span class="mm-pill">${esc(t("kind." + (d.kind || "track")))}</span>${extra && extra.title ? `<span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(extra.title)}</span>` : ""}${extra && extra.items_estimate > 1 ? `<span class="mm-pill">${esc(plural("count.tracks", extra.items_estimate))}</span>` : ""}${extra ? "" : '<span class="mm-skel" style="width:120px;height:14px"></span>'}`;
  };
  const askBackend = debounce(async (url) => {
    const d = await API.soft("detect", { url }, null);
    if (input.value.trim() === url && d && d.source) showDetect(d, d);
    else if (input.value.trim() === url) showDetect(detectLocal(url), {});
  }, 350);
  const onInput = () => { const url = input.value.trim(); const d = detectLocal(url); showDetect(d, null); if (d) askBackend(url); };
  input.addEventListener("input", onInput);
  const start = async () => {
    const url = input.value.trim(); if (!detectLocal(url)) return;
    goBtn.disabled = true;
    try { await API.call("download.start", { url, quality: prefs.quality }); input.value = ""; showDetect(null); toast(t("download.started")); pollJobs(); }
    catch (error) { toast(t("error.download", { error: error.message || error })); goBtn.disabled = false; }
  };
  goBtn.onclick = start;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });
  el.querySelector("#dl-quality").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (!b) return; prefs.quality = b.dataset.q; $$("#dl-quality [data-q]", el).forEach((x) => x.setAttribute("aria-selected", String(x === b))); savePrefs(); });
  const jobsBox = el.querySelector("#dl-jobs"), clearBtn = el.querySelector("#dl-clear");
  onJobsUpdate = () => {
    if (!jobsBox.isConnected) { onJobsUpdate = null; return; }
    const jobs = S.jobs.filter((j) => !hidden.has(j.job_id));
    clearBtn.hidden = !jobs.some((j) => ["done", "error", "cancelled"].includes(j.status));
    if (!jobs.length) { jobsBox.innerHTML = emptyState("download", t("empty.jobs"), t("empty.jobs_hint")); return; }
    if (jobsBox.querySelector(".mm-empty")) jobsBox.innerHTML = "";
    const existing = new Map($$(".mm-job", jobsBox).map((n) => [n.dataset.job, n]));
    jobs.forEach((j, i) => {
      const node = existing.get(j.job_id);
      if (node && node.dataset.status === j.status && node.dataset.sig === itemsSig(j)) {
        node.style.setProperty("--p", (Number(j.progress) || 0).toFixed(3));
        node.querySelector(".st").textContent = jobStatus(j);
      } else {
        const tmp = document.createElement("div"); tmp.innerHTML = jobHtml(j);
        const fresh = tmp.firstElementChild;
        const wasOpen = node && node.querySelector(".mm-job-items[open]"), list = fresh.querySelector(".mm-job-items");
        if (wasOpen && list) list.open = true;
        if (node) { fresh.style.animation = "none"; node.replaceWith(fresh); } else jobsBox.insertBefore(fresh, jobsBox.children[i] || null);
      }
      existing.delete(j.job_id);
    });
    existing.forEach((n) => n.remove());
  };
  jobsBox.addEventListener("click", async (e) => {
    const card = e.target.closest("[data-job]"); if (!card) return;
    const j = S.jobs.find((x) => x.job_id === card.dataset.job); if (!j) return;
    if (e.target.closest("[data-job-cancel]")) { await API.soft("download.cancel", { job_id: j.job_id }); pollJobs(); }
    if (e.target.closest("[data-job-cookies]")) { go("settings"); return; }
    if (e.target.closest("[data-job-tool]")) {
      toast(t("job.updating"));
      try { await API.call("tools.update", {}); toast(t("job.updated")); } catch (error) { toast(String(error && error.message || error)); }
      return;
    }
    if (e.target.closest("[data-job-retry]")) {
      try { await API.call("download.retry", { job_id: j.job_id }); toast(t("job.retried")); } catch (error) { toast(String(error && error.message || error)); }
      pollJobs();
    }
    if (e.target.closest("[data-job-play]")) { const list = (j.track_ids || []).map((id) => S.byId.get(id)).filter(Boolean); if (list.length) Player.playList(list, null, j.title); }
  });
  clearBtn.onclick = async () => { await API.soft("download.clear", {}, null); S.jobs.forEach((j) => { if (["done", "error", "cancelled"].includes(j.status)) hidden.add(j.job_id); }); onJobsUpdate(); };
  onJobsUpdate();
  pollJobs();
  const q = el.querySelector("#dl-q"), results = el.querySelector("#dl-results");

  const search = debounce(() => { const text = q.value.trim(); if (!text) { results.innerHTML = ""; return; } onlineSearch(results, text, { quality: prefs.quality }); }, 450);
  q.addEventListener("input", search);
  if (p && p.url) { input.value = p.url; onInput(); }
  setTimeout(() => input.isConnected && !p.url && input.focus(), 50);
};

function countUp(node, value, format) {
  if (root.hasAttribute("data-calm")) { node.textContent = format(value); return; }
  const t0 = performance.now();
  const step = (ts) => { const k = Math.min(1, (ts - t0) / 900); node.textContent = format(value * (1 - Math.pow(1 - k, 3))); if (k >= 1) Fx.remove(step); };
  Fx.add(step);
}
function lineChart(days) {
  const W = 640, H = 200, pad = 26;
  const max = Math.max(10, ...days.map((d) => d.minutes));
  const pts = days.map((d, i) => [pad + i * (W - pad * 2) / Math.max(1, days.length - 1), H - pad - d.minutes / max * (H - pad * 2)]);
  let path = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) { const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], cx = (x0 + x1) / 2; path += ` C${cx},${y0} ${cx},${y1} ${x1},${y1}`; }
  const lines = [0, .5, 1].map((k) => `<line x1="${pad}" x2="${W - pad}" y1="${H - pad - k * (H - pad * 2)}" y2="${H - pad - k * (H - pad * 2)}"/>`).join("");
  const labels = days.filter((_, i) => (i % 7 === 0 && days.length - 1 - i >= 3) || i === days.length - 1).map((d) => { const i = days.indexOf(d); return `<text x="${pts[i][0]}" y="${H - 4}" text-anchor="middle">${esc(d.date.slice(5).replace("-", "."))}</text>`; }).join("");
  const last = pts[pts.length - 1];
  return `<svg class="mm-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="mm-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0" style="stop-color:var(--tint);stop-opacity:.45"/><stop offset="1" style="stop-color:var(--tint);stop-opacity:0"/></linearGradient></defs>
    <g class="grid">${lines}</g><text x="${pad}" y="${pad - 8}">${max} ${esc(t("stats.min"))}</text><path class="area" d="${path} L${last[0]},${H - pad} L${pad},${H - pad}Z"/><path class="line" d="${path}"/>${labels}<circle cx="${last[0]}" cy="${last[1]}" r="5" style="fill:var(--tint)"/></svg>`;
}
VIEWS.stats = async (el) => {
  el.innerHTML = `<h1 class="mm-h1">${esc(t("nav.stats"))}</h1><p class="mm-sub">${esc(t("stats.sub"))}</p><div class="mm-kpis">${'<div class="mm-skel" style="height:96px;border-radius:18px"></div>'.repeat(4)}</div>`;
  const st = await API.soft("library.stats", {}, null);
  if (!el.isConnected) return;
  if (!st) { el.innerHTML += emptyState("error", t("error.title"), t("error.stats")); return; }
  const liked = S.tracks.filter((x) => x.liked).length;
  const bySource = {};
  S.tracks.forEach((x) => { const s = x.source || "local"; bySource[s] = (bySource[s] || 0) + 1; });
  const srcs = Object.entries(bySource).sort((a, b) => b[1] - a[1]);
  const total = srcs.reduce((s, x) => s + x[1], 0) || 1;
  let acc = 0;
  const C = 2 * Math.PI * 52;
  const donut = srcs.map(([s, n]) => { const len = n / total * C; const seg = `<circle cx="75" cy="75" r="52" stroke="${sourceInfo(s).color}" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-acc}"/>`; acc += len; return seg; }).join("");
  const topA = st.top_artists || [];
  const maxA = Math.max(1, ...topA.map((a) => a.plays || a.minutes || 0));
  const hours = Array.isArray(st.by_hour) && st.by_hour.length === 24 ? st.by_hour.map((h) => Number(h && h.minutes) || 0)
    : Array.isArray(st.hours) && st.hours.length === 24 ? st.hours : null;
  const days = Array.isArray(st.by_day) && st.by_day.length ? st.by_day.map((d) => ({ date: String(d.day || ""), minutes: Number(d.minutes) || 0 }))
    : Array.isArray(st.days) ? st.days : Array.isArray(st.week) ? st.week.map((d) => ({ date: String(d.day || d.date || ""), minutes: Number(d.minutes) || 0 })) : [];
  const maxH = hours ? Math.max(1, ...hours) : 1;
  el.innerHTML = `<h1 class="mm-h1">${esc(t("nav.stats"))}</h1><p class="mm-sub">${esc(t("stats.sub"))}</p>
    <div class="mm-kpis">
      ${(() => { const lm = (st.listened_minutes != null ? st.listened_minutes : st.minutes || 0);  return lm < 60 ? `<div class="mm-kpi" style="--k:var(--tint)"><i></i><b data-n="${Math.round(lm)}">0</b><span>${esc(t("stats.mins_label"))}</span></div>` : `<div class="mm-kpi" style="--k:var(--tint)"><i></i><b data-n="${Math.round(lm / 60)}">0</b><span>${esc(t("stats.hours"))}</span></div>`; })()}
      <div class="mm-kpi" style="--k:var(--tint-2)"><i></i><b data-n="${st.tracks || S.tracks.length}">0</b><span>${esc(t("stats.tracks"))}</span></div>
      <div class="mm-kpi" style="--k:var(--tint-3)"><i></i><b data-n="${st.artists || 0}">0</b><span>${esc(t("stats.artists"))}</span></div>
      <div class="mm-kpi" style="--k:#ff4f7b"><i></i><b data-n="${st.liked != null ? st.liked : liked}">0</b><span>${esc(t("stats.liked"))}</span></div>
    </div>
    <div class="mm-charts">
      <div class="mm-card"><h3>${esc(t("stats.per_day"))}</h3>${days.length > 1 ? lineChart(days) : emptyState("music", t("stats.no_history"), "")}</div>
      <div class="mm-card"><h3>${esc(t("stats.sources"))}</h3><div class="mm-donut-wrap"><svg class="mm-donut" viewBox="0 0 150 150">${donut}<text x="75" y="72" text-anchor="middle" style="fill:var(--mm-text);font-size:26px;font-weight:780">${S.tracks.length}</text><text x="75" y="94" text-anchor="middle" style="fill:var(--mm-muted);font-size:11px;font-weight:550">${esc(t("stats.tracks"))}</text></svg>
        <div class="mm-legend">${srcs.slice(0, 6).map(([s, n]) => `<div><i style="background:${sourceInfo(s).color}"></i>${esc(sourceName(s))}<span class="mm-sub" style="margin:0 0 0 auto;padding-left:10px">${Math.round(n / total * 100)}%</span></div>`).join("")}</div></div></div>
      <div class="mm-card"><h3>${esc(t("stats.top_artists"))}</h3><div class="mm-bars">${topA.length ? topA.slice(0, 6).map((a) => `<div class="mm-hbar" style="--w:${((a.plays || a.minutes || 0) / maxA).toFixed(3)}"><b>${esc(a.name)}</b><span>${esc(plural("count.plays", a.plays || 0))}</span><i></i></div>`).join("") : `<p class="mm-sub">${esc(t("stats.no_history"))}</p>`}</div></div>
      <div class="mm-card"><h3>${esc(t("stats.by_hour"))}</h3>${hours ? `<div class="mm-hours" aria-hidden="true">${hours.map((v, h) => `<i title="${h}:00" style="--h:${(v / maxH).toFixed(3)};--o:${(.3 + v / maxH * .7).toFixed(2)}"></i>`).join("")}</div><div class="mm-axis">${[0, 6, 12, 18, 23].map((h) => `<span>${h}:00</span>`).join("")}</div>` : `<p class="mm-sub">${esc(t("stats.no_history"))}</p>`}</div>
    </div>
    <section class="mm-section"><div class="mm-section-head"><h2 class="mm-h2">${esc(t("stats.top_tracks"))}</h2></div><div id="top"></div></section>
    <section class="mm-section"><div class="mm-section-head"><h2 class="mm-h2">${esc(t("stats.history"))}</h2></div><div class="mm-history" id="history">${'<div class="mm-skel" style="height:52px;border-radius:12px"></div>'.repeat(3)}</div></section>`;
  const fmt = (v) => Math.round(v).toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU");
  $$("[data-n]", el).forEach((n) => countUp(n, Number(n.dataset.n), fmt));
  const top = (st.top_tracks || []).map((x) => S.byId.get(x.id) || normTrack(x));
  if (top.length) trackList(el.querySelector("#top"), top, { from: t("stats.top_tracks"), virtual: false });
  else el.querySelector("#top").innerHTML = emptyState("music", t("stats.no_history"), t("stats.no_history_hint"));
  renderHistory(el.querySelector("#history"));
};

async function renderHistory(box) {
  const rows = await API.soft("library.history", { limit: 60 }, null);
  if (!box.isConnected) return;
  const list = (Array.isArray(rows) ? rows : []).filter((r) => r && r.track && r.track.id);
  if (!list.length) { box.innerHTML = emptyState("music", t("stats.history_empty"), ""); return; }
  const loc = Memu.lang === "en" ? "en-US" : "ru-RU";
  const dayKey = (d) => d.toDateString();
  const now = new Date(), yest = new Date(now.getTime() - 864e5);
  const label = (d) => dayKey(d) === dayKey(now) ? t("stats.today") : dayKey(d) === dayKey(yest) ? t("stats.yesterday")
    : d.toLocaleDateString(loc, { weekday: "long", day: "numeric", month: "long" });
  let last = "", html = "";
  box._tracks = list.map((r) => S.byId.get(r.track.id) || normTrack(r.track));
  list.forEach((r, i) => {
    const at = Number(r.at) || 0, d = new Date(at > 1e12 ? at : at * 1000), tr = box._tracks[i];
    if (dayKey(d) !== last) { last = dayKey(d); html += `<div class="mm-history-day">${esc(label(d))}</div>`; }
    const dev = r.device === "phone" ? "phone" : "pc";
    html += `<button type="button" class="mm-history-row" data-h="${i}"><span class="mm-art">${artImg(tr)}</span><span class="mm-history-txt"><b>${esc(tr.title)}</b><span>${esc(tr.artist)}</span></span>
      <span class="mm-history-meta"><time>${esc(d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" }))}</time><em>${esc(t("stats.device." + dev))} · ${esc(t("stats.minutes_short", { m: Math.max(1, Math.round((Number(r.seconds) || 0) / 60)) }))}</em></span></button>`;
  });
  box.innerHTML = html;
  box.onclick = (e) => { const b = e.target.closest("[data-h]"); if (b) Player.playList(box._tracks, box._tracks[Number(b.dataset.h)].id, t("stats.history")); };
}
