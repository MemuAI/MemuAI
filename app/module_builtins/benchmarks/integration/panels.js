/* Round 38: «Бенчмарки» panels on the MemuAI home screen — «Лидеры бенчмарков» (top models of the leaderboards the
   module already loaded: LMArena, Artificial Analysis, LiveBench, SimpleBench, EQ-Bench) and «Изменения» (what the
   background monitor noticed). Data: Memu.panel.data() → app/module_integration_data.py, a compact digest of the
   module's own 6-hour cache — the panel never downloads leaderboards itself. */
(function () {
  "use strict";
  var M = window.Memu, P = M && M.panel;
  if (!P) return;
  var root = document.getElementById("mk");
  var en = function () { return String(M.lang || document.documentElement.lang || "ru").indexOf("en") === 0; };
  var t = function (ru, enText) { return en() ? enText : ru; };
  var esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var svg = function (d) { return '<svg class="mk-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + "</svg>"; };
  var IC = {
    trophy: svg('<path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8 20h8"/>'),
    pulse: svg('<path d="M3 12h4l3-7 4 14 3-7h4"/>')
  };
  var S = { data: null, board: "", at: 0 };
  function ago(sec) {
    if (!sec) return "";
    sec = sec > 1e12 ? sec / 1000 : sec;
    var min = Math.max(0, Math.round((Date.now() / 1000 - sec) / 60));
    if (min < 60) return min < 1 ? t("сейчас", "now") : min + t(" мин", " min");
    if (min < 1440) return Math.round(min / 60) + t(" ч", " h");
    return Math.round(min / 1440) + t(" д", " d");
  }
  function head(icon, title, extra) {
    return '<div class="mk-glow"></div><div class="mk-head">' + icon + "<h1>" + esc(title) + "</h1>" + (extra || "") +
      '<button type="button" class="mk-link" data-open>' + esc(t("Открыть", "Open")) + "</button></div>";
  }
  function empty(title, text) {
    root.innerHTML = head(IC.trophy, t("Лидеры бенчмарков", "Benchmark leaders")) + '<div class="mk-empty">' + IC.trophy + "<b>" + esc(title) + "</b><span>" + esc(text) + "</span>" +
      '<button type="button" class="mk-cta" data-open>' + esc(t("Открыть «Бенчмарки»", "Open Benchmarks")) + "</button></div>";
  }
  function renderLeaders() {
    var boards = S.data.boards || [];
    if (!boards.length) return empty(t("Рейтинги ещё не загружены", "No leaderboards yet"), t("Откройте модуль один раз — панель покажет лидеров", "Open the module once — the panel will show the leaders"));
    var board = boards.find(function (b) { return b.id === S.board; }) || boards[0];
    var rows = board.rows.slice(0, P.params.size === "l" || P.params.size === "w" ? 5 : 3);
    var max = Math.max.apply(null, board.rows.map(function (r) { return Math.abs(r.score); })), min = Math.min.apply(null, board.rows.map(function (r) { return Math.abs(r.score); }));
    var chips = '<div class="mk-chips" role="toolbar">' + boards.map(function (b) {
      return '<button type="button" class="mk-chip" data-board="' + esc(b.id) + '" aria-pressed="' + (b.id === board.id) + '">' + esc(en() ? b.title_en : b.title) + "</button>";
    }).join("") + "</div>";
    root.innerHTML = head(IC.trophy, t("Лидеры бенчмарков", "Benchmark leaders"), board.at ? '<span class="mk-pill dim">' + esc(ago(board.at)) + "</span>" : "") + chips +
      '<ol class="mk-list bm-rank">' + rows.map(function (r, i) {
        var share = max > min ? 0.18 + 0.82 * (Math.abs(r.score) - min) / (max - min) : 1;
        return '<li><button type="button" class="mk-row" data-model="' + esc(r.model) + '"><span class="bm-place" data-place="' + (i + 1) + '">' + (i + 1) + "</span>" +
          '<span class="mk-main"><b>' + esc(r.model) + "</b>" + (r.org ? "<small>" + esc(r.org) + "</small>" : "") + "</span>" +
          '<span class="bm-score"><b>' + esc(r.score.toLocaleString(en() ? "en-US" : "ru-RU", { maximumFractionDigits: 1 })) + '</b><i style="--w:' + share.toFixed(3) + '"></i></span></button></li>';
      }).join("") + "</ol>";
    var on = root.querySelector('.mk-chip[aria-pressed="true"]');
    if (on && on.offsetLeft + on.offsetWidth > on.parentNode.clientWidth - 24) on.parentNode.scrollLeft = on.offsetLeft - 24;
  }
  function renderChanges() {
    var rows = (S.data.changes || []).slice(0, 4);
    if (!rows.length) {
      root.innerHTML = head(IC.pulse, t("Изменения бенчмарков", "Benchmark changes")) + '<div class="mk-empty">' + IC.pulse + "<b>" + esc(t("Изменений пока нет", "No changes yet")) + "</b><span>" +
        esc(t("Включите фоновое наблюдение в «Бенчмарках» — новые лидеры появятся здесь", "Turn on background monitoring in Benchmarks — new leaders show up here")) + "</span></div>";
      return;
    }
    root.innerHTML = head(IC.pulse, t("Изменения бенчмарков", "Benchmark changes")) + '<ul class="mk-list">' + rows.map(function (r) {
      var line = String(r.text || "").split("\n").filter(Boolean)[0] || "";
      return '<li><button type="button" class="mk-row" data-open><span class="bm-dot" aria-hidden="true"></span><span class="mk-main"><b>' + esc(r.title) + "</b><small>" + esc(line) + '</small></span><span class="mk-side">' + esc(ago(r.at)) + "</span></button></li>";
    }).join("") + "</ul>";
  }
  function render() { if (P.id === "changes") renderChanges(); else renderLeaders(); }
  function load() {
    S.at = Date.now();
    P.data({ top: 5 }).then(function (d) { S.data = d || {}; render(); }, function () { empty(t("«Бенчмарки» недоступны", "Benchmarks are unavailable"), ""); });
  }
  root.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.board) { S.board = b.dataset.board; if (M.store) M.store.set("panel:board", S.board).catch(function () {}); return renderLeaders(); }
    if (b.dataset.model) return P.open({ query: b.dataset.model, from: "panel" });
    if (b.hasAttribute("data-open")) return P.open({ from: "panel" });
  });
  document.addEventListener("memu-panel-visible", function (e) { if (e.detail && e.detail.visible && Date.now() - S.at > 300000) load(); });
  (M.store && P.id !== "changes" ? M.store.get("panel:board").then(function (v) { S.board = String(v || ""); }, function () {}) : Promise.resolve()).then(load);
})();
