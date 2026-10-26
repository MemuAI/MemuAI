

(function () {
  "use strict";
  if (!window.MemuQuick || typeof MemuQuick.settingsSection !== "function") return;
  MemuQuick.settingsSection("companion", (body, ctx) => {
    if (!window.MemuCompanionSettings) {
      body.textContent = ctx.en ? "Companion module is not loaded." : "Модуль компаньона не загружен.";
      return;
    }
    const c = {
      lang: ctx.en ? "en" : "ru",
      pref: (key, fallback) => ctx.pref(key, fallback),
      allPrefs: () => (typeof S !== "undefined" && S.preferences) || {},
      save: (patch) => ctx.savePreferences(patch),
      toast: (text, kind) => ctx.toast(text, kind || "ok"),
    };
    body.innerHTML = MemuCompanionSettings.html("quick", c);
    MemuCompanionSettings.bind(body.firstElementChild, "quick", c);
  }, { title: (ctx) => (ctx.en ? "Companion" : "Компаньон"), order: 50 });
})();
