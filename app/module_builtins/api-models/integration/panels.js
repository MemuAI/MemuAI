

(function () {
  "use strict";
  var M = window.Memu, P = M && M.panel;
  if (!P) return;
  var root = document.getElementById("mk");
  var en = function () { return String(M.lang || document.documentElement.lang || "ru").indexOf("en") === 0; };
  var t = function (ru, enText) { return en() ? enText : ru; };
  var esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var BELL = '<svg class="mk-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>';
  var LABS = { openai: "OpenAI", anthropic: "Anthropic", google: "Google", "meta-llama": "Meta", mistralai: "Mistral", deepseek: "DeepSeek",
    qwen: "Qwen", "x-ai": "xAI", moonshotai: "Moonshot", "z-ai": "Z.ai", minimax: "MiniMax", cohere: "Cohere", nvidia: "NVIDIA", microsoft: "Microsoft" };
  var S = { at: 0 };
  function hue(text) { var h = 0; String(text || "").split("").forEach(function (ch) { h = (h * 31 + ch.charCodeAt(0)) >>> 0; }); return h % 360; }
  function lab(m) { return LABS[m.family] || (m.family ? m.family.charAt(0).toUpperCase() + m.family.slice(1) : m.source || "API"); }
  function money(v) {
    if (v === null || v === undefined || !isFinite(v)) return "";
    if (+v === 0) return t("бесплатно", "free");
    return "$" + (+v).toLocaleString(en() ? "en-US" : "ru-RU", { maximumFractionDigits: v < 1 ? 3 : 2 });
  }
  function ctx(n) { n = +n || 0; return n >= 1e6 ? (n / 1e6).toFixed(n % 1e6 ? 1 : 0) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : ""; }
  function ago(sec) {
    if (!sec) return "";
    var min = Math.max(0, Math.round((Date.now() / 1000 - sec) / 60));
    if (min < 60) return min < 1 ? t("сейчас", "now") : min + t(" мин", " min");
    if (min < 1440) return Math.round(min / 60) + t(" ч", " h");
    return Math.round(min / 1440) + t(" д", " d");
  }
  function price(m) {
    if (m.input === undefined && m.output === undefined) return "";
    if (+m.input === 0 && +m.output === 0) return '<span class="mk-pill">' + esc(t("бесплатно", "free")) + "</span>";
    return '<span class="am-price" title="' + esc(t("за 1M токенов: вход → выход", "per 1M tokens: input → output")) + '">' + esc(money(m.input) || "—") + "<i>→</i>" + esc(money(m.output) || "—") + "</span>";
  }
  function render(d) {
    var models = (d.models || []).slice(0, P.params.size === "s" ? 3 : 4);
    var status = d.enabled ? (d.checked ? t("проверено ", "checked ") + ago(d.checked) + t(" назад", " ago") : t("наблюдение включено", "monitoring on")) : t("наблюдение на паузе", "monitoring paused");
    var pill = d.enabled ? (d.checked ? ago(d.checked) : t("вкл", "on")) : t("пауза", "paused");
    var headHtml = '<div class="mk-glow"></div><div class="mk-head">' + BELL + "<h1>" + esc(t("Новые модели API", "New API models")) + '</h1><span class="mk-pill' + (d.enabled ? " am-live" : " dim") + '" title="' + esc(status) + '">' + esc(pill) + "</span>" +
      '<button type="button" class="mk-link" data-open>' + esc(t("Лента", "Feed")) + "</button></div>";
    if (!models.length) {
      root.innerHTML = headHtml + '<div class="mk-empty">' + BELL + "<b>" + esc(d.sources ? t("Пока тихо", "All quiet so far") : t("Выберите, за чем следить", "Choose what to watch")) + "</b><span>" +
        esc(d.sources ? t("Новые модели появятся здесь, как только их добавят в API", "New models show up here as soon as an API lists them") : t("OpenRouter или API нужных лабораторий — в модуле", "OpenRouter or the labs' APIs — in the module")) + "</span>" +
        (d.sources ? "" : '<button type="button" class="mk-cta" data-open>' + esc(t("Настроить", "Set up")) + "</button>") + "</div>";
      return;
    }
    root.innerHTML = headHtml + '<ul class="mk-list">' + models.map(function (m) {
      var meta = [lab(m), ctx(m.context) && ctx(m.context) + t(" контекст", " context"), ago(m.at)].filter(Boolean).join(" · ");
      return '<li><button type="button" class="mk-row" data-key="' + esc(m.key) + '"><span class="mk-mono" style="--h:' + hue(lab(m)) + '" aria-hidden="true">' + esc(lab(m).charAt(0)) + "</span>" +
        '<span class="mk-main"><b title="' + esc(m.key) + '">' + esc(m.name) + (m.seen ? "" : ' <span class="am-dot" aria-label="' + esc(t("новая", "new")) + '"></span>') + "</b><small>" + esc(meta) + "</small></span>" +
        '<span class="mk-side">' + price(m) + "</span></button></li>";
    }).join("") + "</ul>";
  }
  function load() {
    S.at = Date.now();
    P.data({ limit: 6 }).then(function (d) { render(d || {}); }, function () {
      root.innerHTML = '<div class="mk-empty">' + BELL + "<b>" + esc(t("Лента недоступна", "The feed is unavailable")) + "</b></div>";
    });
  }
  root.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.key) return P.open({ query: b.dataset.key, from: "panel" });
    if (b.hasAttribute("data-open")) return P.open({ from: "panel" });
  });
  document.addEventListener("memu-panel-visible", function (e) { if (e.detail && e.detail.visible && Date.now() - S.at > 300000) load(); });
  load();
})();
