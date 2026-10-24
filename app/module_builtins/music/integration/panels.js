/* Round 38: MemuMusic panels for the MemuAI home screen — «Сейчас играет», «Плейлист», «Статистика прослушиваний».
   Runs in the panel sandbox (app/module_integration.py: the module's bridge + Memu.panel). Data only through
   Memu.music.call; the player state is a long-poll (player.wait, 25 s) that runs ONLY while the card is on screen
   and the window is visible (Memu.panel.visible / document.hidden). Progress is a CSS animation, no timers per frame. */
(function () {
  "use strict";
  var M = window.Memu, P = M && M.panel;
  if (!P) return;
  var root = document.getElementById("mx");
  var en = function () { try { return String((M.lang && M.lang()) || document.documentElement.lang || "ru").indexOf("en") === 0; } catch (e) { return false; } };
  var t = function (ru, enText) { return en() ? enText : ru; };
  var esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var call = function (op, args) { return M.music.call(op, args || {}); };
  var svg = function (d, fill) { return '<svg viewBox="0 0 24 24" ' + (fill ? 'fill="currentColor"' : 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"') + ">" + d + "</svg>"; };
  var IC = {
    note: svg('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),
    play: svg('<path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z"/>', true),
    pause: svg('<rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/>', true),
    next: svg('<path d="M6 5.5v13l9-6.5zM17 5v14"/>'),
    prev: svg('<path d="M18 5.5v13L9 12zM7 5v14"/>'),
    shuffle: svg('<path d="M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>'),
    chart: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    list: svg('<path d="M4 6h12M4 12h12M4 18h8M19 15v6M16 18h6"/>')
  };
  var S = { state: null, version: 0, polling: false, alive: true, playlists: null, chosen: "", stats: null };
  var fmt = function (sec) { sec = Math.max(0, Math.round(+sec || 0)); return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0"); };
  var initial = function (s) { return esc(String(s || "♪").trim().charAt(0).toUpperCase() || "♪"); };
  function art(src, title, big) {
    return src ? '<img class="mx-art' + (big ? " big" : "") + '" src="' + esc(src) + '" alt="" loading="lazy" decoding="async">'
      : '<div class="mx-art mx-gen' + (big ? " big" : "") + '" aria-hidden="true">' + initial(title) + "</div>";
  }
  function setAccent(color) { if (/^#[0-9a-f]{3,8}$/i.test(color || "")) root.style.setProperty("--c", color); else root.style.removeProperty("--c"); }
  function command(cmd, args) {
    return call("player.command", Object.assign({ cmd: cmd, target: "pc", by: "panel" }, args || {})).catch(function (e) { M.log && M.log(String(e && e.message || e), "warn"); });
  }
  function empty(title, text, cta) {
    root.innerHTML = '<div class="mx-glow"></div><div class="mx-empty">' + IC.note.replace("<svg", '<svg class="mx-ico" style="width:28px;height:28px"') + "<b>" + esc(title) + "</b><span>" + esc(text) + "</span>" +
      (cta ? '<button type="button" class="mx-cta" data-open>' + esc(cta) + "</button>" : "") + "</div>";
  }
  function head(icon, title, extra) {
    return '<div class="mx-head">' + icon.replace("<svg", '<svg class="mx-ico"') + "<h1>" + esc(title) + "</h1>" + (extra || "") +
      '<button type="button" class="mx-link" data-open title="' + esc(t("Открыть MemuMusic", "Open MemuMusic")) + '">' + esc(t("Открыть", "Open")) + "</button></div>";
  }

  /* ------------------------------------------------------------------ Сейчас играет */
  function renderNow() {
    var s = S.state || {}, tr = s.track;
    if (!tr) return empty(t("Тишина", "Silence"), t("Включите музыку — здесь будет то, что играет", "Play something — it will show up here"), t("Открыть MemuMusic", "Open MemuMusic"));
    var playing = !!s.playing && !s.stale;
    var dur = +tr.duration || +s.duration || 0;
    var pos = Math.min(dur || 1e9, +s.position_now || +s.position || 0);
    setAccent(tr.accent);
    root.dataset.playing = playing ? "1" : "0";
    root.innerHTML = '<div class="mx-glow"></div>' +
      head(IC.note, t("Сейчас играет", "Now playing"), playing ? '<span class="mx-eq" aria-hidden="true"><i></i><i></i><i></i></span>' : "") +
      '<div class="mx-np">' + art(tr.cover, tr.title, true) + '<div class="mx-meta"><div class="mx-title" title="' + esc(tr.title) + '">' + esc(tr.title || "—") + "</div>" +
      '<div class="mx-sub">' + esc([tr.artist, tr.album].filter(Boolean).join(" · ") || "—") + "</div>" +
      (dur ? '<div class="mx-bar"><i style="--p:' + (pos / dur).toFixed(4) + ";--left:" + Math.max(0.1, dur - pos).toFixed(1) + 's"></i></div><div class="mx-time"><span>' + fmt(pos) + "</span><span>" + fmt(dur) + "</span></div>" : "") +
      "</div></div>" +
      '<div class="mx-ctl"><button type="button" class="mx-btn" data-cmd="prev" aria-label="' + esc(t("Назад", "Previous")) + '">' + IC.prev + "</button>" +
      '<button type="button" class="mx-btn main" data-cmd="toggle" aria-label="' + esc(playing ? t("Пауза", "Pause") : t("Играть", "Play")) + '">' + (playing ? IC.pause : IC.play) + "</button>" +
      '<button type="button" class="mx-btn" data-cmd="next" aria-label="' + esc(t("Дальше", "Next")) + '">' + IC.next + "</button></div>";
  }
  function poll() {
    if (S.polling || !S.alive || !P.visible || document.hidden) return;
    S.polling = true;
    call("player.wait", { since: S.version, timeout: 25 }).then(function (r) {
      S.polling = false;
      if (r && r.state) { S.version = r.state.version || S.version; if (r.changed) { S.state = r.state; withTrack().then(renderNow); } }
      setTimeout(poll, 50);
    }, function () { S.polling = false; setTimeout(poll, 5000); });
  }
  function withTrack() {
    var s = S.state || {};
    if (!s.track_id) { s.track = null; return Promise.resolve(); }
    if (s.track && s.track.id === s.track_id) return Promise.resolve();
    return call("library.get", { id: s.track_id }).then(function (tr) { s.track = tr; }, function () { s.track = null; });
  }
  function startNow() {
    call("player.state").then(function (s) { S.state = s; S.version = s.version || 0; renderNow(); poll(); }, function () { empty(t("MemuMusic недоступен", "MemuMusic is unavailable"), t("Проверьте, что модуль включён", "Check that the module is on")); });
  }

  /* ------------------------------------------------------------------------- Плейлист */
  function renderPlaylist(full) {
    var lists = S.playlists || [];
    if (!lists.length) return empty(t("Плейлистов пока нет", "No playlists yet"), t("Создайте плейлист в MemuMusic", "Create one in MemuMusic"), t("Открыть MemuMusic", "Open MemuMusic"));
    var chips = '<div class="mx-chips" role="toolbar">' + lists.slice(0, 8).map(function (p) {
      return '<button type="button" class="mx-chip" data-pl="' + esc(p.id) + '" aria-pressed="' + (p.id === S.chosen) + '">' + esc(p.name) + "</button>";
    }).join("") + "</div>";
    var pl = full || lists.find(function (p) { return p.id === S.chosen; }) || lists[0];
    setAccent(pl.accent);
    var covers = (pl.covers || []).slice(0, 4);
    var mosaic = '<div class="mx-mosaic' + (covers.length < 4 ? " one" : "") + '">' + (covers.length ? (covers.length < 4 ? covers.slice(0, 1) : covers).map(function (c) { return '<img src="' + esc(c) + '" alt="" loading="lazy">'; }).join("") : '<div class="mx-art mx-gen big">' + initial(pl.name) + "</div>") + "</div>";
    var rows = full && full.tracks ? full.tracks.slice(0, 3).map(function (tr, i) {
      return '<li class="mx-row" data-track="' + esc(tr.id) + '" tabindex="0"><span class="mx-n">' + (i + 1) + "</span><span>" + esc(tr.title) + (tr.artist ? ' <small>· ' + esc(tr.artist) + "</small>" : "") + "</span><small>" + fmt(tr.duration) + "</small></li>";
    }).join("") : "";
    root.innerHTML = '<div class="mx-glow"></div>' + head(IC.list, t("Плейлист", "Playlist")) + chips +
      '<div class="mx-pl">' + mosaic + '<div class="mx-meta"><div class="mx-title">' + esc(pl.name) + '</div><div class="mx-sub">' +
      esc(t(pl.count + " трек." , pl.count + " tracks") + (pl.duration ? " · " + Math.round(pl.duration / 60) + t(" мин", " min") : "")) + "</div>" +
      (rows ? '<ul class="mx-rows">' + rows + "</ul>" : "") + "</div></div>" +
      '<div class="mx-ctl"><button type="button" class="mx-btn" data-pl-play="shuffle" aria-label="' + esc(t("Вперемешку", "Shuffle")) + '">' + IC.shuffle + "</button>" +
      '<button type="button" class="mx-btn main" data-pl-play="1" aria-label="' + esc(t("Играть плейлист", "Play the playlist")) + '">' + IC.play + "</button></div>";
  }
  function choose(id) {
    S.chosen = id;
    if (M.store) M.store.set("panel:playlist", id).catch(function () {});
    renderPlaylist();
    call("playlist.get", { id: id }).then(function (full) { if (S.chosen === id) { S.full = full; renderPlaylist(full); } }, function () {});
  }
  function playPlaylist(shuffle) {
    var full = S.full; if (!full || !(full.tracks || []).length) return;
    var ids = full.tracks.map(function (x) { return x.id; });
    if (shuffle) for (var i = ids.length - 1; i > 0; i -= 1) { var j = Math.floor(Math.random() * (i + 1)); var k = ids[i]; ids[i] = ids[j]; ids[j] = k; }
    command("play_track", { track_id: ids[0], queue: ids, context: { name: full.name } });
  }
  function startPlaylist() {
    Promise.all([call("playlists.list"), M.store ? M.store.get("panel:playlist").then(function (v) { return v; }, function () { return ""; }) : ""]).then(function (r) {
      S.playlists = (r[0] || []).filter(function (p) { return p.count > 0; });
      var saved = String(r[1] || "");
      choose(S.playlists.some(function (p) { return p.id === saved; }) ? saved : ((S.playlists[0] || {}).id || ""));
    }, function () { empty(t("MemuMusic недоступен", "MemuMusic is unavailable"), ""); });
  }

  /* --------------------------------------------------------------- Статистика */
  function renderStats() {
    var s = S.stats || {};
    if (!s.tracks) return empty(t("Статистики пока нет", "No stats yet"), t("Она появится, когда вы что-нибудь послушаете", "It appears once you listen to something"));
    var hours = (+s.listened_minutes || 0) / 60;
    var days = (s.by_day || []).slice(-14);
    var max = Math.max.apply(null, days.map(function (d) { return +d.minutes || 0; }).concat([1]));
    var top = (s.top_artists || []).slice(0, 3);
    root.innerHTML = '<div class="mx-glow"></div>' + head(IC.chart, t("Статистика прослушиваний", "Listening stats")) +
      '<div class="mx-stats"><div class="mx-stat"><b>' + (hours >= 10 || !hours ? Math.round(hours) : hours.toFixed(1)) + "</b><small>" + esc(t("часов музыки", "hours played")) + '</small></div>' +
      '<div class="mx-stat"><b>' + (+s.listens || 0) + "</b><small>" + esc(t("прослушиваний", "plays")) + '</small></div>' +
      '<div class="mx-stat"><b>' + (+s.tracks || 0) + "</b><small>" + esc(t("треков в библиотеке", "tracks in library")) + "</small></div></div>" +
      (days.length ? '<div class="mx-days" role="img" aria-label="' + esc(t("По дням за 2 недели", "By day, 2 weeks")) + '">' + days.map(function (d) {
        var v = +d.minutes || 0; return "<i" + (v ? "" : " data-zero") + ' style="height:' + Math.max(4, Math.round(v / max * 100)) + '%" title="' + esc(d.day || "") + ": " + Math.round(v) + t(" мин", " min") + '"></i>';
      }).join("") + "</div>" : "") +
      (top.length ? '<div class="mx-top">' + top.map(function (a, i) { return '<div class="mx-row" style="cursor:default"><span class="mx-n">' + (i + 1) + "</span><span>" + esc(a.name) + "</span><small>" + (a.plays || 0) + "</small></div>"; }).join("") + "</div>" : "");
  }
  function startStats() {
    call("library.stats", { days: 30 }).then(function (s) { S.stats = s; renderStats(); }, function () { empty(t("MemuMusic недоступен", "MemuMusic is unavailable"), ""); });
  }

  root.addEventListener("click", function (e) {
    var b = e.target.closest("button, [data-track]");
    if (!b) return;
    if (b.hasAttribute("data-open")) return P.open({ from: "panel" });
    if (b.dataset.cmd) {
      if (b.dataset.cmd === "toggle" && S.state) { S.state.playing = !S.state.playing; renderNow(); }
      return command(b.dataset.cmd);
    }
    if (b.dataset.pl) return choose(b.dataset.pl);
    if (b.dataset.plPlay) return playPlaylist(b.dataset.plPlay === "shuffle");
    if (b.dataset.track && S.full) { var ids = S.full.tracks.map(function (x) { return x.id; }); command("play_track", { track_id: b.dataset.track, queue: ids, context: { name: S.full.name } }); }
  });
  root.addEventListener("keydown", function (e) { if (e.key === "Enter" && e.target.dataset && e.target.dataset.track) e.target.click(); });
  document.addEventListener("memu-panel-visible", function () { if (P.id === "now-playing") poll(); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden && P.id === "now-playing") poll(); });

  if (P.id === "now-playing") startNow();
  else if (P.id === "playlist") startPlaylist();
  else if (P.id === "stats") startStats();
})();
