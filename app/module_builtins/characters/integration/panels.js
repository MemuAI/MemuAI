/* Round 38: «Персонажи» panels on the MemuAI home screen — «Недавние чаты» and «Персонаж дня».
   Runs in the panel sandbox (app/module_integration.py). Data: Memu.panel.data() → module_integration_data.py
   (the module's own index + chat lists, read on the server). Read once on load and again when the card comes back
   on screen after a while — no timers. A click opens the character's latest chat in the module window. */
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
    users: svg('<path d="M16 20v-1.5A3.5 3.5 0 0 0 12.5 15h-5A3.5 3.5 0 0 0 4 18.5V20"/><circle cx="10" cy="8" r="3.5"/><path d="M20 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.6a3.5 3.5 0 0 1 0 6.8"/>'),
    star: svg('<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>'),
    chat: svg('<path d="M4 5h16v11H9l-5 4z"/>')
  };
  var S = { data: null, at: 0 };
  function hue(text) { var h = 0; String(text || "").split("").forEach(function (ch) { h = (h * 31 + ch.charCodeAt(0)) >>> 0; }); return h % 360; }
  function avatar(c, size) {
    var st = size ? ' style="width:' + size + "px;height:" + size + 'px"' : "";
    return c.thumb ? '<img class="mk-mono round" src="' + esc(c.thumb) + '" alt=""' + st + ">"
      : '<span class="mk-mono round" aria-hidden="true" style="--h:' + hue(c.name) + (size ? ";width:" + size + "px;height:" + size + "px;font-size:" + Math.round(size / 2.6) + "px" : "") + '">' + esc(String(c.name || "?").trim().charAt(0).toUpperCase()) + "</span>";
  }
  function ago(ms) {
    if (!ms) return "";
    var min = Math.max(0, Math.round((Date.now() - ms) / 60000));
    if (min < 1) return t("сейчас", "now");
    if (min < 60) return min + t(" мин", " min");
    if (min < 1440) return Math.round(min / 60) + t(" ч", " h");
    var d = Math.round(min / 1440);
    return d < 30 ? d + t(" д", " d") : new Date(ms).toLocaleDateString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short" });
  }
  function head(icon, title) {
    return '<div class="mk-glow"></div><div class="mk-head">' + icon + "<h1>" + esc(title) + '</h1><button type="button" class="mk-link" data-open>' + esc(t("Все персонажи", "All characters")) + "</button></div>";
  }
  function empty() {
    root.innerHTML = '<div class="mk-glow"></div><div class="mk-empty">' + IC.users + "<b>" + esc(t("Персонажей пока нет", "No characters yet")) + "</b><span>" +
      esc(t("Импортируйте карточку или создайте персонажа с ИИ", "Import a card or create a character with AI")) + '</span><button type="button" class="mk-cta" data-open>' + esc(t("Открыть «Персонажи»", "Open Characters")) + "</button></div>";
  }

  function renderRecent() {
    var rows = (S.data.recent || []).slice(0, P.params.size === "s" ? 3 : 4);
    if (!rows.length) return empty();
    root.innerHTML = head(IC.chat, t("Недавние чаты", "Recent chats")) + '<ul class="mk-list">' + rows.map(function (c) {
      return '<li><button type="button" class="mk-row" data-char="' + esc(c.id) + '">' + avatar(c) +
        '<span class="mk-main"><b>' + esc(c.name) + (c.fav ? ' <span class="ch-fav" aria-label="' + esc(t("в избранном", "favourite")) + '">★</span>' : "") + "</b><small>" +
        esc(c.preview || (c.chats ? t("Чатов: ", "Chats: ") + c.chats : t("Ещё не общались", "No chats yet"))) + '</small></span><span class="mk-side">' + esc(ago(c.updated)) + "</span></button></li>";
    }).join("") + "</ul>";
  }
  function renderDay() {
    var c = S.data.day;
    if (!c) return empty();
    root.style.setProperty("--h", hue(c.name));
    root.innerHTML = head(IC.star, t("Персонаж дня", "Character of the day")) +
      '<div class="ch-day">' + avatar(c, 76) + '<div class="mk-main"><b class="ch-name">' + esc(c.name) + "</b>" +
      (c.tags.length ? '<span class="ch-tags">' + c.tags.map(function (x) { return '<span class="mk-pill dim">' + esc(x) + "</span>"; }).join("") + "</span>" : "") +
      (c.preview ? '<q class="ch-quote">' + esc(c.preview) + "</q>" : '<small class="mk-sub">' + esc(t("Начните первый разговор", "Start the first conversation")) + "</small>") + "</div></div>" +
      '<div class="ch-actions"><button type="button" class="mk-cta" data-char="' + esc(c.id) + '">' + esc(c.chats ? t("Продолжить", "Continue") : t("Начать чат", "Start chatting")) + "</button>" +
      (c.chats ? '<span class="mk-sub">' + esc(t("Сообщений: ", "Messages: ") + (c.messages || 0) + " · " + ago(c.updated)) + "</span>" : "") + "</div>";
  }
  function render() { if (P.id === "today") renderDay(); else renderRecent(); }
  function load() {
    S.at = Date.now();
    P.data({ limit: 5 }).then(function (d) { S.data = d || {}; render(); }, function () {
      root.innerHTML = '<div class="mk-empty">' + IC.users + "<b>" + esc(t("«Персонажи» недоступны", "Characters are unavailable")) + "</b></div>";
    });
  }

  root.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.char) return P.open({ query: b.dataset.char, from: "panel" });
    if (b.hasAttribute("data-open")) return P.open({ from: "panel" });
  });
  // Back on screen after a minute or more → the chats may have moved on.
  document.addEventListener("memu-panel-visible", function (e) { if (e.detail && e.detail.visible && Date.now() - S.at > 60000) load(); });
  load();
})();
