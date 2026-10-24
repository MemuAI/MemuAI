/* MemuAI — Round 30 (r30-design): more «Внешний вид» settings.

   Settings → Appearance → «Форма и текст», «Прозрачность», «Движение» (rendered by appearance.js through
   MemuAppearancePlus.html / bind; applied on every appearance change through MemuAppearancePlus.apply):
     ui_radius        "" | sharp | round              → html[data-radius]
     ui_msg_size      "" | sm | lg | xl                → html[data-msg-size]
     ui_contrast      "" | more                        → html[data-contrast]
     ui_glass         "" | light | strong              → html[data-glass]   (frosted menus, dialogs, sidebar)
     ui_backdrop      none | mica | acrylic | tabbed   → Windows 11 material (pywebview bridge → app/win_backdrop.py);
                                                         html[data-backdrop] only while the window really has it
     ui_motion_speed  "" | slow | fast                 → html[data-motion-speed]
     ui_motion_feel   "" | smooth | springy            → html[data-motion-feel]
     ui_msg_anim      "" | fade | pop | slide | none   → html[data-msg-anim]
   The values are cached in localStorage («memu_ui_plus») and put on <html> as soon as this file runs, so the
   first frame already has the right corners and motion. Server side: app/appearance_extra.py. */
(function () {
  "use strict";
  if (window.MemuAppearancePlus) return;
  const KEYS = {
    ui_radius: ["radius", ["", "sharp", "round"]],
    ui_msg_size: ["msgSize", ["", "sm", "lg", "xl"]],
    ui_contrast: ["contrast", ["", "more"]],
    ui_glass: ["glass", ["", "light", "strong"]],
    ui_motion_speed: ["motionSpeed", ["", "slow", "fast"]],
    ui_motion_feel: ["motionFeel", ["", "smooth", "springy"]],
    ui_msg_anim: ["msgAnim", ["", "fade", "pop", "slide", "none"]],
  };
  const BACKDROPS = ["none", "mica", "acrylic", "tabbed"];
  const CACHE = "memu_ui_plus";
  const root = () => document.documentElement;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let backdropState = null, lastBackdrop = "", prefRef = null;

  function setAttrs(values) {
    const r = root();
    for (const [key, [attr, allowed]] of Object.entries(KEYS)) {
      const v = allowed.includes(values[key]) ? values[key] : "";
      if (v) r.dataset[attr] = v; else delete r.dataset[attr];
    }
  }
  // first paint: the cached values
  try { setAttrs(JSON.parse(localStorage.getItem(CACHE) || "{}")); } catch (e) { /* no cache yet */ }

  function apply(pref) {
    prefRef = pref;
    const values = {};
    for (const key of Object.keys(KEYS)) values[key] = String(pref(key, "") || "");
    setAttrs(values);
    try { localStorage.setItem(CACHE, JSON.stringify(values)); } catch (e) { /* private mode */ }
    applyBackdrop(String(pref("ui_backdrop", "none") || "none"));
  }

  /* -------------------------------------------------------------- window material */
  const bridge = () => (window.pywebview && window.pywebview.api && typeof window.pywebview.api.window_backdrop === "function") ? window.pywebview.api : null;
  async function loadBackdropState() {
    const api = bridge();
    if (!api) return null;
    try { backdropState = await api.window_backdrop_state(); } catch (e) { backdropState = null; }
    return backdropState;
  }
  async function applyBackdrop(kind) {
    const want = BACKDROPS.includes(kind) ? kind : "none";
    const dark = (root().dataset.theme || "dark") !== "light";
    const background = getComputedStyle(root()).getPropertyValue("--bg").trim();
    const key = `${want}:${dark}:${background}`;
    if (key === lastBackdrop) return;
    const api = bridge();
    if (!api) { delete root().dataset.backdrop; return; }
    lastBackdrop = key;
    try { backdropState = await api.window_backdrop(want, dark, background); } catch (e) { backdropState = null; }
    const active = backdropState && backdropState.active && backdropState.active !== "none" ? backdropState.active : "";
    if (active) root().dataset.backdrop = active; else delete root().dataset.backdrop;
    refreshBackdropNote();
  }
  window.addEventListener("pywebviewready", () => { if (prefRef) { lastBackdrop = ""; applyBackdrop(String(prefRef("ui_backdrop", "none") || "none")); } });
  // A normal opaque window also needs its native frame and WebView backing
  // colours updated. CSS alone cannot change those when the theme switches.
  new MutationObserver(() => { if (prefRef && lastBackdrop) applyBackdrop(String(prefRef("ui_backdrop", "none"))); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  /* ------------------------------------------------------------------ settings UI */
  function html({ L, row, segment, sxSection, pref }) {
    const v = (key) => String(pref(key, "") || "");
    const backdrop = BACKDROPS.includes(v("ui_backdrop")) ? v("ui_backdrop") : "none";
    return sxSection("appearance-shape-section", L("Форма и текст", "Shape and text"), "", `
        ${row(L("Скругления", "Corners"), L("Радиус кнопок, карточек и окон", "The radius of buttons, cards and windows"),
          segment("ap-radius", [["sharp", L("Острые", "Sharp")], ["", L("Обычные", "Default")], ["round", L("Мягкие", "Round")]], v("ui_radius")), ' data-sx-k="radius corners скругления углы радиус"')}
        ${row(L("Текст сообщений", "Message text"), L("Только размер текста в диалоге, интерфейс не меняется", "Only the text in the conversation; the interface stays the same"),
          segment("ap-msg-size", [["sm", L("Мельче", "Smaller")], ["", L("Обычный", "Default")], ["lg", L("Крупнее", "Larger")], ["xl", L("Крупный", "Large")]], v("ui_msg_size")), ' data-sx-k="font size message text размер шрифта текста сообщений"')}
        ${row(L("Контрастность", "Contrast"), L("Чётче второстепенный текст и рамки", "Crisper secondary text and borders"),
          segment("ap-contrast", [["", L("Обычная", "Default")], ["more", L("Повышенная", "Higher")]], v("ui_contrast")), ' data-sx-k="contrast контраст accessibility доступность"')}`, true)
      + sxSection("appearance-glass-section", L("Прозрачность", "Transparency"), "", `
        ${row(L("Матовое стекло", "Frosted glass"), L("Меню, диалоги и боковая панель просвечивают и размывают то, что под ними", "Menus, dialogs and the sidebar let what is behind them show through, blurred"),
          segment("ap-glass", [["", L("Нет", "Off")], ["light", L("Лёгкое", "Light")], ["strong", L("Сильное", "Strong")]], v("ui_glass")), ' data-sx-k="transparency glass blur прозрачность стекло размытие acrylic"')}
        ${row(L("Материал окна", "Window material"), `<span id="ap-backdrop-note">${esc(backdropNote(L))}</span>`,
          segment("ap-backdrop", [["none", L("Нет", "None")], ["mica", "Mica"], ["acrylic", "Acrylic"], ["tabbed", L("Вкладки", "Tabbed")]], backdrop), ' data-sx-k="mica acrylic windows 11 материал окна прозрачность фон"')}`, true)
      + sxSection("appearance-motion-section", L("Движение", "Motion"), L("Работает вместе с настройкой «Анимации» ниже; системное «уменьшить движение» учитывается всегда.", "Works together with “Animations” below; the system “reduce motion” setting is always respected."), `
        ${row(L("Скорость", "Speed"), "", segment("ap-motion-speed", [["slow", L("Медленнее", "Slower")], ["", L("Обычная", "Default")], ["fast", L("Быстрее", "Faster")]], v("ui_motion_speed")), ' data-sx-k="animation speed скорость анимаций"')}
        ${row(L("Характер", "Character"), L("«Пружинный» — с лёгким отскоком", "Springy adds a light bounce"), segment("ap-motion-feel", [["smooth", L("Плавный", "Smooth")], ["", L("Обычный", "Default")], ["springy", L("Пружинный", "Springy")]], v("ui_motion_feel")), ' data-sx-k="spring пружинные анимации плавные easing"')}
        ${row(L("Появление сообщений", "New messages"), "", segment("ap-msg-anim", [["", L("Подъём", "Rise")], ["fade", L("Проявление", "Fade")], ["pop", L("Пружина", "Pop")], ["slide", L("Сдвиг", "Slide")], ["none", L("Без анимации", "None")]], v("ui_msg_anim")), ' data-sx-k="message animation появление сообщений анимация"')}
        <div class="ap-demo" aria-hidden="true"><div class="ap-demo-msg user"><i></i><i></i></div><div class="ap-demo-msg ai"><i></i><i></i><i></i></div>
          <button type="button" class="pbtn ghost sx-btn-sm ap-demo-play" data-ap-demo>${L("Повторить", "Replay")}</button></div>`, true);
  }
  function backdropNote(L) {
    const st = backdropState;
    if (!bridge()) return L("В приложении MemuAI для Windows 11: фон окна — рабочий стол, слегка размытый.", "In the MemuAI app on Windows 11: the window background is your desktop, softly blurred.");
    if (st && !st.available) return L("Нужна Windows 11 — на этой системе окно остаётся обычным.", "Needs Windows 11 — the window stays opaque on this system.");
    if (st && st.kinds && st.kinds.length === 2) return L("На этой версии Windows 11 доступна только Mica.", "Only Mica is available on this Windows 11 build.");
    if (st && st.error && st.active === "none" && st.error !== "windows11") return L("Не удалось включить материал — окно осталось обычным.", "Could not turn the material on — the window stayed opaque.");
    return L("Фон окна — рабочий стол, слегка размытый (Windows 11).", "The window background is your desktop, softly blurred (Windows 11).");
  }
  function refreshBackdropNote() {
    const note = document.getElementById("ap-backdrop-note");
    if (!note) return;
    const en = (typeof PG_LANG !== "undefined" && PG_LANG === "en");
    note.textContent = backdropNote((ru, e) => (en ? e : ru));
    const kinds = backdropState && backdropState.kinds;
    document.querySelectorAll('input[name="ap-backdrop"]').forEach((input) => {
      input.disabled = !!(kinds && input.value !== "none" && !kinds.includes(input.value)) || (!bridge() && input.value !== "none");
    });
  }
  function replayDemo(pane) {
    const demo = pane.querySelector(".ap-demo");
    if (!demo) return;
    demo.classList.remove("play"); void demo.offsetWidth; demo.classList.add("play");
  }
  const FIELDS = { "ap-radius": "ui_radius", "ap-msg-size": "ui_msg_size", "ap-contrast": "ui_contrast", "ap-glass": "ui_glass",
    "ap-backdrop": "ui_backdrop", "ap-motion-speed": "ui_motion_speed", "ap-motion-feel": "ui_motion_feel", "ap-msg-anim": "ui_msg_anim" };
  function bind(pane, { save, apply: applyAll }) {
    if (!pane) return;
    loadBackdropState().then(refreshBackdropNote);
    refreshBackdropNote();
    pane.addEventListener("change", (event) => {
      const key = FIELDS[event.target.name];
      if (!key) return;
      save({ [key]: event.target.value });
      applyAll();
      if (/^ui_(motion|msg_anim)/.test(key)) replayDemo(pane);
    });
    pane.addEventListener("click", (event) => { if (event.target.closest("[data-ap-demo]")) replayDemo(pane); });
  }

  window.MemuAppearancePlus = Object.freeze({ apply, html, bind, KEYS });
})();
