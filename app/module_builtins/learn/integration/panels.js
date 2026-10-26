

(function () {
  "use strict";
  var M = window.Memu, P = M && M.panel;
  if (!P) return;
  var root = document.getElementById("mk");
  var en = function () { return String(M.lang || document.documentElement.lang || "ru").indexOf("en") === 0; };
  var t = function (ru, enText) { return en() ? enText : ru; };
  var esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var nf = function (n) { return Number(n || 0).toLocaleString(en() ? "en-US" : "ru-RU"); };
  var CAP = '<svg class="mk-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4 2.5 8.5 12 13l9.5-4.5z"/><path d="M6 10.6V15c0 1.7 2.7 3 6 3s6-1.3 6-3v-4.4"/></svg>';
  var PLAY = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z"/></svg>';
  var S = { at: 0 };
  function cards(n) {
    if (en()) return n === 1 ? "card" : "cards";
    var m10 = n % 10, m100 = n % 100;
    return m10 === 1 && m100 !== 11 ? "карточка" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "карточки" : "карточек";
  }
  function days(n) {
    if (en()) return n === 1 ? "day" : "days";
    var m10 = n % 10, m100 = n % 100;
    return m10 === 1 && m100 !== 11 ? "день" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "дня" : "дней";
  }
  function render(d) {
    var head = '<div class="mk-glow"></div><div class="mk-head">' + CAP + "<h1>" + esc(t("Повторить сегодня", "Review today")) + "</h1>" +
      (d.streak ? '<span class="mk-pill" title="' + esc(t("Серия дней подряд", "Days in a row")) + '">🔥 ' + nf(d.streak) + " " + esc(days(d.streak)) + "</span>" : "") +
      '<button type="button" class="mk-link" data-open>' + esc(t("Открыть", "Open")) + "</button></div>";
    var courses = d.courses || [];
    if (!courses.length) {
      root.innerHTML = head + '<div class="mk-empty">' + CAP + "<b>" + esc(t("Пока нет курсов", "No courses yet")) + "</b><span>" +
        esc(t("Тема, книга или заметка — ИИ соберёт курс и карточки", "A topic, a book or a note — the AI builds a course and cards")) + '</span><button type="button" class="mk-cta" data-open>' +
        esc(t("Создать курс", "Create a course")) + "</button></div>";
      return;
    }
    var c = d.counts || {}, total = d.due || 0;
    var sub = total ? nf(d.minutes || 1) + t(" мин", " min") + (c.new ? " · " + nf(c.new) + t(" новых", " new") : "") : t("На сегодня всё — отличная работа", "All done for today — great work");
    var week = (d.week || []).slice(-7), wmax = Math.max.apply(null, [1].concat(week));
    var hero = '<div class="lp-hero"><span class="lp-num">' + nf(total) + '</span><span class="lp-text"><b>' + esc(total ? cards(total) + t(" к повторению", " to review") : t("Ничего не ждёт", "Nothing due")) +
      "</b><small>" + esc(sub) + "</small></span>" +
      (total ? '<button type="button" class="lp-go" data-review>' + PLAY + esc(t("Начать", "Start")) + "</button>"
        : '<span class="lp-week" aria-hidden="true">' + week.map(function (v) { return '<i style="height:' + Math.max(3, Math.round(v / wmax * 28)) + 'px"></i>'; }).join("") + "</span>") + "</div>";
    var split = total ? '<div class="lp-split" aria-hidden="true">' + [["n", c.new], ["l", c.learning], ["r", c.review]].map(function (x) { return x[1] ? '<i class="' + x[0] + '" style="flex:' + x[1] + '"></i>' : ""; }).join("") + "</div>" : "";
    var list = '<div class="lp-courses">' + courses.slice(0, P.params.size === "l" ? 5 : 3).map(function (x) {
      var n = (x.due || 0);
      return '<button type="button" class="lp-course" data-course="' + esc(x.id) + '" style="--h:' + (+x.hue || 210) + '"><i class="lp-dot"></i><span>' + esc(x.title) + "</span>" +
        (n ? "<em>" + nf(n) + "</em>" : '<em class="ok">' + (x.new ? nf(x.new) + t(" нов.", " new") : "✓") + "</em>") + "</button>";
    }).join("") + "</div>";
    root.innerHTML = head + hero + split + list;
  }
  function load() {
    S.at = Date.now();
    P.data({}).then(function (d) { render(d || {}); }, function () {
      root.innerHTML = '<div class="mk-empty">' + CAP + "<b>" + esc(t("MemuLearn недоступен", "MemuLearn is unavailable")) + "</b></div>";
    });
  }
  root.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.hasAttribute("data-review")) return P.open({ route: "review", from: "panel" });
    if (b.dataset.course) return P.open({ course: b.dataset.course, from: "panel" });
    if (b.hasAttribute("data-open")) return P.open({ from: "panel" });
  });
  document.addEventListener("memu-panel-visible", function (e) { if (e.detail && e.detail.visible && Date.now() - S.at > 120000) load(); });
  load();
})();
