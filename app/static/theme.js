

"use strict";
(function () {
  const root = document.documentElement;
  const query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  function mode() {
    try { return localStorage.getItem("oh_theme") || "dark"; } catch (e) { return "dark"; }
  }
  function apply(value) {
    const chosen = value || mode();
    const light = chosen === "light" || (chosen === "system" && !!query && query.matches);
    if (light) root.dataset.theme = "light";
    else delete root.dataset.theme;
    root.dataset.themeMode = chosen;
  }
  apply();
  if (query) {
    const onChange = () => { if (mode() === "system") apply("system"); };
    if (query.addEventListener) query.addEventListener("change", onChange);
    else if (query.addListener) query.addListener(onChange);
  }
  window.MemuTheme = {
    get: mode,
    set(value) {
      const next = ["dark", "light", "system"].includes(value) ? value : "dark";
      try { localStorage.setItem("oh_theme", next); } catch (e) {  }
      apply(next);
    },
  };
})();
