(function () {
  "use strict";
  var M = window.Memu, P = M && M.panel;
  if (!P) return;
  var root = document.getElementById("mt");
  var en = function () {
    try { return String((M.lang && M.lang()) || document.documentElement.lang || "ru").indexOf("en") === 0; }
    catch (e) { return false; }
  };
  var t = function (ru, enText) { return en() ? enText : ru; };
  var esc = function (v) {
    return String(v == null ? "" : v).replace(/[&<>\"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var fmt = function (ms) {
    try {
      return new Date(+ms || 0).toLocaleString(en() ? "en" : "ru", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch (e) { return ""; }
  };
  var sourceLabel = function (source) {
    return { microphone: t("Микрофон", "Microphone"), system: t("Системный звук", "System audio"), "import": t("Импорт", "Import") }[source] || source || "";
  };
  function empty() {
    root.innerHTML = '<div class="mt-empty"><b>' + esc(t("Пока нет встреч", "No meetings yet")) + "</b><span>" +
      esc(t("Запишите встречу в MemuMeet — она появится здесь", "Record a meeting in MemuMeet — it will show up here")) + "</span>" +
      '<button type="button" class="mt-cta" data-open>' + esc(t("Открыть MemuMeet", "Open MemuMeet")) + "</button></div>";
  }
  function render(rows) {
    if (!rows.length) return empty();
    root.innerHTML = '<div class="mt-head"><h1>' + esc(t("Последние встречи", "Recent meetings")) + "</h1>" +
      '<button type="button" class="mt-link" data-open>' + esc(t("Открыть", "Open")) + "</button></div>" +
      rows.map(function (row) {
        return '<button type="button" class="mt-row" data-open>' +
          '<span class="mt-ico" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></svg></span>' +
          '<span class="mt-main"><b>' + esc(row.title || t("Встреча без названия", "Untitled meeting")) + "</b>" +
          "<span>" + esc(fmt(row.created_at)) + " · " + esc(sourceLabel(row.source)) + "</span></span>" +
          ((row.summary || "").trim() ? '<span class="mt-tag">' + esc(t("конспект", "summary")) + "</span>" : "") +
          "</button>";
      }).join("");
  }
  function load() {
    if (!M.store || !M.store.list) return empty();
    M.store.list("meetings/").then(function (rows) {
      var keys = (rows || []).map(function (row) { return row.key; })
        .filter(function (key) { return /^meetings\/[^/]+$/.test(String(key || "")); });
      if (!keys.length) return empty();
      return M.store.getMany(keys).then(function (values) {
        var found = keys.map(function (key) { return values[key]; })
          .filter(function (row) { return row && row.id; })
          .sort(function (a, b) { return (b.created_at || 0) - (a.created_at || 0); })
          .slice(0, 3);
        render(found);
      });
    }).catch(empty);
  }
  root.addEventListener("click", function (event) {
    if (event.target.closest("[data-open]")) P.open({ command: "list" });
  });
  document.addEventListener("memu-panel-visible", function (event) {
    if (event.detail && event.detail.visible) load();
  });
  load();
})();
