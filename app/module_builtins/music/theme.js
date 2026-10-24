/* MemuMusic · theme.js — система оформления: режим (светлая/тёмная/как в системе/как в MemuAI),
 * темы-палитры, дизайны (раскладка и стиль), акцент, плотность, шрифт, скругления, анимации,
 * визуализатор и стиль «Сейчас играет», фон, секции Главной — и раздел «Оформление» в Настройках.
 * Всё — CSS-переменными и атрибутами на <html>: переключение мгновенное, без перерисовки разделов
 * (кроме плотности списков: высота строки виртуального списка живёт в JS). */
const Theme = (() => {
  /* h/s — оттенок и насыщенность фона; acc — три акцента; dark/light — яркость фона и поверхности (L, %). */
  const THEMES = [
    { id: "cover", h: 252, s: 30, acc: ["#7c5cff", "#ff5c8a", "#20c7b5"], dyn: true },
    { id: "midnight", h: 245, s: 34, acc: ["#7c5cff", "#ff5c8a", "#20c7b5"] },
    { id: "aurora", h: 172, s: 40, acc: ["#22d39b", "#4f8cff", "#b26bff"] },
    { id: "sunset", h: 14, s: 46, acc: ["#ff7a45", "#ff3d77", "#ffbe3b"] },
    { id: "ocean", h: 206, s: 50, acc: ["#1fb2ff", "#3a6bff", "#2fd8c8"] },
    { id: "neon", h: 284, s: 60, acc: ["#ff2bd6", "#00d9ff", "#a6ff2e"], dark: [4, 8] },
    { id: "sakura", h: 338, s: 44, acc: ["#ff7aa8", "#b77bff", "#ffb38a"], light: [97, 100] },
    { id: "moss", h: 104, s: 26, acc: ["#7fb54a", "#d0b44c", "#3fa481"] },
    { id: "sand", h: 36, s: 42, acc: ["#c27a34", "#9f4f2c", "#6f8a46"], light: [94.5, 98.5], dark: [7, 11] },
    { id: "graphite", h: 220, s: 0, acc: ["#e4e4e7", "#a1a1aa", "#71717a"], mono: true },
    { id: "vinyl", h: 24, s: 38, acc: ["#e2523b", "#efa13a", "#2f8a83"], light: [93.5, 97.5], dark: [6.5, 10.5] },
    { id: "memu", h: 230, s: 10, acc: ["#d97757", "#e8a33d", "#5a9e8f"], host: true },
  ];
  const DESIGNS = ["glass", "minimal", "retro", "neon", "compact"];
  const ACCENTS = ["#7c5cff", "#4f8cff", "#1fb2ff", "#22d39b", "#7fb54a", "#f2b233", "#ff7a45", "#ff3d77", "#e2523b", "#b26bff", "#d97757"];
  const HOME_SECTIONS = ["greet", "quick", "spotlight", "continue", "mixes", "recent", "liked", "artists"];
  const DEFAULTS = { mode: "memu", theme: "cover", design: "glass", accent: "", coverTint: true, cover: "m", density: "normal", font: "system",
    radius: "normal", motion: "full", viz: "ring", npStyle: "card", npTheme: "dark", bg: "cover", homeHidden: [] };
  const DESIGN_PRESETS = { compact: { density: "compact", cover: "s" }, retro: { font: "serif", radius: "sharp" }, minimal: { bg: "solid" }, neon: { font: "grotesk" }, glass: {} };
  const FONTS = {
    system: 'var(--sans, "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif)',
    grotesk: '"Inter", "Manrope", "Bahnschrift", "Segoe UI Variable Display", "Helvetica Neue", Arial, sans-serif',
    serif: '"Iowan Old Style", "Charter", "Georgia", "Cambria", "Times New Roman", serif',
    mono: '"Cascadia Code", "JetBrains Mono", "Consolas", "SF Mono", monospace',
  };
  const RADIUS = { sharp: .35, normal: 1, round: 1.45 };
  const TILE = { s: 148, m: 176, l: 220 };
  const ROWS = { compact: 44, normal: 56, comfy: 64 };
  const mql = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
  let dynHue = null;   // оттенок фона темы «Из обложки» (от текущего трека)

  /* миграция настроек раунда 37: lite → анимации, viz:false → визуализатор выкл, npStyle, coverAccent */
  const old = S.prefs;
  const ui = Object.assign({}, DEFAULTS, {
    motion: old.lite ? "lite" : "full", viz: old.viz === false ? "off" : "ring", npStyle: old.npStyle === "vinyl" ? "vinyl" : "card",
    coverTint: old.coverAccent !== false,
  }, old.ui || {});
  if (!Array.isArray(ui.homeHidden)) ui.homeHidden = [];
  S.prefs.ui = ui;

  const theme = () => THEMES.find((x) => x.id === ui.theme) || THEMES[0];
  function scheme() {
    if (ui.mode === "light" || ui.mode === "dark") return ui.mode;
    if (ui.mode === "system") return mql && !mql.matches ? "light" : "dark";
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }
  function lum(hex) {
    const c = (hexToRgb(hex) || [0, 0, 0]).map((v) => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); });
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2];
  }
  function contrast(a, b) { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); }
  function rgba(hex, a) { const c = hexToRgb(hex) || [0, 0, 0]; return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
  /** Цвет текста-акцента, читаемый на фоне (WCAG ≥ 4.5): темнеет на светлом, светлеет на тёмном. */
  function ink(hex, bg) {
    const rgb = hexToRgb(hex); if (!rgb) return hex;
    const [h, s, l0] = rgbToHsl(rgb), light = lum(bg) > .4;
    let l = l0, out = hex;
    for (let i = 0; i < 40 && contrast(out, bg) < 4.6; i++) { l += light ? -2 : 2; out = hsl(h, Math.min(s, light ? 80 : 100), Math.max(4, Math.min(96, l))); }
    return out;
  }
  /** Сплошной акцент под текст кнопки: белый или почти чёрный с контрастом ≥ 4.5; иначе акцент темнеет под белый. */
  function solid(hex) {
    if (contrast("#ffffff", hex) >= 4.5 || contrast("#111114", hex) >= 4.5) return hex;
    const [h, s, l0] = rgbToHsl(hexToRgb(hex) || [124, 92, 255]);
    let out = hex;
    for (let l = l0; l > 5 && contrast("#ffffff", out) < 4.5; l -= 2) out = hsl(h, s, l);
    return out;
  }
  function onColor(hex) { return contrast("#ffffff", hex) >= 4.5 ? "#ffffff" : contrast("#111114", hex) >= 4.5 ? "#111114" : "#ffffff"; }

  /** Токены темы для схемы: фон, поверхности, линии, текст. «Как в MemuAI» берёт цвета хоста. */
  function tokensFor(th, sch, hue) {
    const h = hue != null ? hue : th.h, s = hue != null ? 34 : th.s;
    const hostScheme = root.getAttribute("data-theme") === "light" ? "light" : "dark";
    if (th.host && hostScheme !== sch) {   // MemuAI в другой схеме — та же гамма, но в выбранной схеме
      const a = hexToRgb(getComputedStyle(root).getPropertyValue("--accent").trim());
      return tokensFor(Object.assign({}, th, { host: false, h: a ? rgbToHsl(a)[0] : th.h, s: 14 }), sch);
    }
    if (th.host) {
      const cs = getComputedStyle(root), v = (n, d) => (cs.getPropertyValue(n) || "").trim() || d;
      const bg = /^#[0-9a-f]{6}$/i.test(v("--bg", "")) ? v("--bg", "") : sch === "light" ? "#f7f6f3" : "#191b1e";
      const text = /^#[0-9a-f]{6}$/i.test(v("--text", "")) ? v("--text", "") : sch === "light" ? "#1d1d1f" : "#f0f1f3";
      return build(bg, /^#[0-9a-f]{6}$/i.test(v("--panel", "")) ? v("--panel", "") : bg, text, sch);
    }
    if (sch === "light") {
      const [bl, sl] = th.light || [96.5, 100];
      return build(hsl(h, s * .55, bl), hsl(h, s * .4, sl), hsl(h, Math.min(30, s * .5), 9), "light");
    }
    const [bl, sl] = th.dark || [5.5, 9.5];
    return build(hsl(h, s * .42, bl), hsl(h, s * .34, sl), hsl(h, Math.min(20, s * .3), 96), "dark");
  }
  function build(bg, surface, text, sch) {
    const dark = sch === "dark", T = hexToRgb(text) || [255, 255, 255], tx = (a) => `rgba(${T[0]},${T[1]},${T[2]},${a})`;
    return {
      "--mm-bg": bg, "--mm-surface": surface, "--mm-text": text,
      "--mm-panel": dark ? tx(.045) : rgba(surface, .78), "--mm-panel-2": tx(dark ? .08 : .055), "--mm-line": tx(dark ? .09 : .1),
      "--mm-hover": tx(dark ? .06 : .045), "--mm-muted": tx(dark ? .68 : .72), "--mm-faint": tx(dark ? .52 : .62),
      "--mm-shadow": dark ? "0 30px 80px -20px rgba(0,0,0,.65), 0 8px 24px -8px rgba(0,0,0,.5)" : "0 30px 70px -28px rgba(30,24,60,.28), 0 8px 20px -12px rgba(30,24,60,.2)",
    };
  }
  /** Три акцента: свой цвет → из него; иначе — цвета темы (для «Из обложки» — палитра трека, её даёт applyPalette). */
  function accents() {
    const own = hexToRgb(ui.accent) ? ui.accent : "";
    if (own) { const [h, s, l] = rgbToHsl(hexToRgb(own)); return [own, hsl(h + 34, Math.max(50, s), Math.min(70, l + 4)), hsl(h - 40, Math.max(40, s * .9), Math.max(38, l - 8))]; }
    const th = theme();
    if (th.host) { const a = getComputedStyle(root).getPropertyValue("--accent").trim(); if (hexToRgb(a)) { const [h, s, l] = rgbToHsl(hexToRgb(a)); return [a, hsl(h + 30, s, l), hsl(h - 140, s * .7, l)]; } }
    if (th.mono && scheme() === "light") return ["#27272a", "#52525b", "#71717a"];
    return th.acc;
  }
  /** Нужен ли акцент из обложки: тема «Из обложки» или включённый «Цвет из обложки», и нет своего цвета. */
  function coverAccent() { return !hexToRgb(ui.accent) && (theme().dyn || (ui.coverTint && !theme().mono)); }

  function setVars(map) { Object.keys(map).forEach((k) => root.style.setProperty(k, map[k])); }
  /** Применить всё. Вызывается при старте, при смене настроек, темы хоста и (для «Из обложки») трека. */
  function apply() {
    const sch = scheme(), th = theme();
    root.setAttribute("data-mm-scheme", sch);
    root.setAttribute("data-mm-theme", th.id);
    root.setAttribute("data-mm-design", DESIGNS.includes(ui.design) ? ui.design : "glass");
    root.setAttribute("data-mm-density", ROWS[ui.density] ? ui.density : "normal");
    root.setAttribute("data-mm-font", FONTS[ui.font] ? ui.font : "system");
    root.setAttribute("data-mm-motion", ui.motion);
    root.setAttribute("data-mm-bg", ui.bg);
    root.setAttribute("data-mm-cover", TILE[ui.cover] ? ui.cover : "m");
    const tok = tokensFor(th, sch, th.dyn ? dynHue : null);
    setVars(tok);
    setVars({ "--mm-font": FONTS[ui.font] || FONTS.system, "--mm-rk": String(RADIUS[ui.radius] || 1), "--mm-tile": (TILE[ui.cover] || 176) + "px",
      "--mm-row": (ROWS[ui.density] || 56) + "px" });
    appbar();
    return tok;
  }
  /** Акцент → переменные (вызывает applyPalette в main.js с тремя цветами). */
  function paint(colors) {
    const bg = getComputedStyle(root).getPropertyValue("--mm-bg").trim() || (scheme() === "light" ? "#f5f4f8" : "#08080b");
    const sol = solid(colors[0]);
    setVars({ "--tint": colors[0], "--tint-2": colors[1], "--tint-3": colors[2], "--tint-ink": ink(colors[0], bg), "--tint-solid": sol, "--mm-on-tint": onColor(sol) });
    if (theme().dyn) {
      const rgb = hexToRgb(colors[0]); const h = rgb ? Math.round(rgbToHsl(rgb)[0]) : null;
      if (h !== dynHue) { dynHue = h; setVars(tokensFor(theme(), scheme(), dynHue)); }
    }
    return bg;
  }
  /** Фон окна: размытая обложка (неподвижный слой, без backdrop-filter), живой градиент (canvas) или однотонный. */
  function coverBg(track) {
    const img = $("#coverbg");
    if (img) {
      const src = track ? (track.cover || coverArt(track.id, "", track.title)) : "";
      if (!src) img.removeAttribute("src"); else if (img.getAttribute("src") !== src) { img.dataset.fb = coverArt(track.id, "", track.title); img.src = src; }
    }
    if (ambient) ui.bg === "gradient" && !NP.isOpen() ? ambient.start() : ambient.stop();
  }
  function row() { return ROWS[ui.density] || 56; }
  function hidden(section) { return ui.homeHidden.includes(section); }
  if (mql && mql.addEventListener) mql.addEventListener("change", () => { if (ui.mode === "system") changed("mode"); });

  /** Изменение настройки: сохранить, применить, оповестить тех, кому важно. */
  function set(key, value) {
    ui[key] = value;
    if (key === "design") Object.assign(ui, DESIGN_PRESETS[value] || {});
    changed(key);
  }
  function changed(key) {
    S.prefs.lite = ui.motion !== "full"; S.prefs.viz = ui.viz !== "off"; S.prefs.npStyle = ui.npStyle; S.prefs.coverAccent = coverAccent();
    savePrefs();
    root.classList.add("mm-theming");
    apply();
    applyPalette(Player.current());
    applyCalm();
    NP.restyle && NP.restyle();
    if (["density", "design", "homeHidden", "cover", "font", "radius"].includes(key) && S.view !== "settings") rerender();
    if (["density", "design", "cover"].includes(key) && Fx.relayout) Fx.relayout();
    clearTimeout(changed.t); changed.t = setTimeout(() => root.classList.remove("mm-theming"), 450);
    syncControls();
  }

  /* ------------------------------------------------------------ раздел «Оформление» */
  const L = (k, v) => t("ui." + k, v);
  /** Превью темы: мини-окно в текущей схеме и по диагонали — та же тема в другой схеме (видно и фон, и оттенок, и акценты). */
  function mini(th) {
    const one = (sch) => {
      const tok = tokensFor(th, sch, th.dyn ? 330 : null);
      const acc = th.host ? accents() : th.mono && sch === "light" ? ["#27272a", "#52525b", "#71717a"] : th.dyn ? ["#ff6a8a", "#ffb35c", "#6c7bff"] : th.acc;
      const style = `--p-bg:${tok["--mm-bg"]};--p-sf:${tok["--mm-surface"]};--p-tx:${tok["--mm-text"]};--p-ln:${tok["--mm-line"]};--p-mu:${tok["--mm-faint"]};--p-a:${acc[0]};--p-b:${acc[1]};--p-c:${acc[2]}`;
      return `<span class="mm-tp" data-s="${sch}" style="${style}"><i class="tp-side"><u></u><u></u><u></u></i><i class="tp-main"><b></b><em><s></s><s></s><s></s></em><small></small></i><i class="tp-bar"><s></s><u></u></i></span>`;
    };
    const sch = scheme();
    return `<span class="mm-tp2" aria-hidden="true">${one(sch)}${one(sch === "light" ? "dark" : "light")}</span>`;
  }
  function designMini(id) {
    return `<span class="mm-dp" data-d="${id}" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></span>`;
  }
  const seg = (key, values, labelOf) => `<div class="mm-seg2" role="radiogroup" data-ui="${key}">${values.map((v) => `<button type="button" role="radio" data-v="${v}" aria-checked="${ui[key] === v}">${esc(labelOf ? labelOf(v) : L(key + "." + v))}</button>`).join("")}</div>`;
  const group = (title, hint, body, cls) => `<section class="mm-ap-group${cls ? " " + cls : ""}"><header><h3>${esc(title)}</h3>${hint ? `<p>${esc(hint)}</p>` : ""}</header>${body}</section>`;
  const line = (title, hint, control) => `<div class="mm-ap-line"><div><b>${esc(title)}</b>${hint ? `<p>${esc(hint)}</p>` : ""}</div>${control}</div>`;
  function render(host) {
    host.innerHTML = `<div class="mm-ap">
      ${group(L("mode.title"), L("mode.hint"), seg("mode", ["light", "dark", "system", "memu"]))}
      ${group(L("theme.title"), L("theme.hint"), `<div class="mm-ap-themes" role="radiogroup" data-ui="theme">${THEMES.map((th) => `<button type="button" class="mm-ap-card" role="radio" data-v="${th.id}" aria-checked="${ui.theme === th.id}">${mini(th)}<span class="mm-ap-name"><b>${esc(L("theme." + th.id))}</b><span>${esc(L("theme." + th.id + "_hint"))}</span></span><i class="mm-ap-check">${icon("check")}</i></button>`).join("")}</div>`)}
      ${group(L("design.title"), L("design.hint"), `<div class="mm-ap-designs" role="radiogroup" data-ui="design">${DESIGNS.map((d) => `<button type="button" class="mm-ap-card" role="radio" data-v="${d}" aria-checked="${ui.design === d}">${designMini(d)}<span class="mm-ap-name"><b>${esc(L("design." + d))}</b><span>${esc(L("design." + d + "_hint"))}</span></span><i class="mm-ap-check">${icon("check")}</i></button>`).join("")}</div>`)}
      ${group(L("accent.title"), L("accent.hint"), `<div class="mm-ap-accents" role="radiogroup" data-ui="accent"><button type="button" class="mm-sw auto" role="radio" data-v="" aria-checked="${!ui.accent}" title="${esc(L("accent.theme"))}" aria-label="${esc(L("accent.theme"))}"></button>${ACCENTS.map((c) => `<button type="button" class="mm-sw" role="radio" data-v="${c}" aria-checked="${ui.accent === c}" style="--sw:${c}" aria-label="${c}"></button>`).join("")}<label class="mm-sw custom" title="${esc(L("accent.custom"))}"><input type="color" id="ap-color" value="${hexToRgb(ui.accent) ? ui.accent : "#7c5cff"}" aria-label="${esc(L("accent.custom"))}">${icon("plus")}</label></div>
        ${line(L("cover_tint"), L("cover_tint_hint"), `<label class="mm-switch"><input type="checkbox" data-ui-bool="coverTint"${ui.coverTint ? " checked" : ""}><i></i></label>`)}`)}
      ${group(L("layout.title"), "", [
        line(L("cover.title"), L("cover.hint"), seg("cover", ["s", "m", "l"])),
        line(L("density.title"), L("density.hint"), seg("density", ["compact", "normal", "comfy"])),
        line(L("radius.title"), "", seg("radius", ["sharp", "normal", "round"])),
        line(L("font.title"), "", `<div class="mm-seg2 fonts" role="radiogroup" data-ui="font">${Object.keys(FONTS).map((f) => `<button type="button" role="radio" data-v="${f}" aria-checked="${ui.font === f}" style="font-family:${esc(FONTS[f])}">${esc(L("font." + f))}</button>`).join("")}</div>`),
        line(L("motion.title"), L("motion.hint"), seg("motion", ["full", "lite", "off"])),
      ].join(""), "lines")}
      ${group(L("np.title"), L("np.hint"), `<div class="mm-ap-np" role="radiogroup" data-ui="npStyle">${["card", "vinyl", "full", "minimal"].map((v) => `<button type="button" class="mm-ap-card" role="radio" data-v="${v}" aria-checked="${ui.npStyle === v}"><span class="mm-npp" data-s="${v}" aria-hidden="true"><i></i><s></s><u></u></span><span class="mm-ap-name"><b>${esc(L("np." + v))}</b></span><i class="mm-ap-check">${icon("check")}</i></button>`).join("")}</div>
        ${line(L("npTheme.title"), L("npTheme.hint"), seg("npTheme", ["dark", "auto"]))}
        ${line(L("viz.title"), L("viz.hint"), seg("viz", ["ring", "wave", "bars", "off"]))}
        ${line(L("bg.title"), L("bg.hint"), seg("bg", ["cover", "gradient", "solid"]))}`)}
      ${group(L("home.title"), L("home.hint"), `<div class="mm-ap-home">${HOME_SECTIONS.map((k) => `<label class="mm-ap-toggle"><span>${esc(L("home." + k))}</span><span class="mm-switch"><input type="checkbox" data-home="${k}"${hidden(k) ? "" : " checked"}><i></i></span></label>`).join("")}</div>`)}
      <div class="mm-ap-foot"><button type="button" class="mm-btn ghost sm" id="ap-reset">${icon("refresh")}${esc(L("reset"))}</button></div>
    </div>`;
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-ui] [data-v]"); if (!b) return;
      const key = b.closest("[data-ui]").dataset.ui;
      set(key, b.dataset.v);
    });
    host.addEventListener("change", (e) => {
      const tgt = e.target;
      if (tgt.dataset.uiBool) set(tgt.dataset.uiBool, tgt.checked);
      else if (tgt.dataset.home) { const k = tgt.dataset.home; ui.homeHidden = tgt.checked ? ui.homeHidden.filter((x) => x !== k) : ui.homeHidden.concat(k); changed("homeHidden"); }
    });
    host.querySelector("#ap-color").addEventListener("input", debounce((e) => set("accent", e.target.value), 60));
    host.querySelector("#ap-reset").addEventListener("click", () => { Object.assign(ui, DEFAULTS, { homeHidden: [] }); changed("design"); render(host); });
  }
  /** Отметки выбранного в открытом разделе — без перерисовки (живой отклик, фокус не теряется). */
  function syncControls() {
    $$("[data-ui]").forEach((g) => { const key = g.dataset.ui; $$("[data-v]", g).forEach((b) => b.setAttribute("aria-checked", String(String(ui[key] || "") === b.dataset.v))); });
    $$(".mm-ap-themes .mm-ap-card").forEach((b) => { const th = THEMES.find((x) => x.id === b.dataset.v); const old = b.querySelector(".mm-tp2"); if (th && old) old.outerHTML = mini(th); });
    $$("[data-ui-bool]").forEach((i) => { i.checked = !!ui[i.dataset.uiBool]; });
  }
  /* design-3: прозрачный аппбар окна-приложения — без своего названия (логотип уже в боковой панели), цвет кнопок ✦ ⋯ и окна —
   * по фону под ними: «Сейчас играет» (тёмный или светлый плеер) или схема раздела. Шлём, только когда цвет сменился. */
  let barInk = "";
  function appbar() {
    const bar = window.Memu && Memu.appbar;
    // Native frame colours are needed even when the custom appbar is off.
    // A regular embedded module host safely ignores this appearance metadata.
    if (!bar || !bar.state) { barInk = ""; return; }
    const np = document.getElementById("np"), npOpen = np && !np.hidden && !np.classList.contains("out");
    const light = npOpen ? np.hasAttribute("data-light") : scheme() === "light";
    const ink = light ? "#1d1b24" : "#f4f1f6";
    const actual = getComputedStyle(npOpen ? np : document.body).backgroundColor;
    const rgb = /^rgb\(\s*(\d+),\s*(\d+),\s*(\d+)\s*\)$/.exec(actual);
    const paper = rgb ? "#" + rgb.slice(1).map(value => Number(value).toString(16).padStart(2, "0")).join("")
      : (npOpen ? (light ? "#faf9fc" : "#050507") : getComputedStyle(root).getPropertyValue("--mm-bg").trim());
    const key = ink + ":" + paper;
    if (key === barInk) return;
    barInk = key;
    bar.set({ text: ink, background: paper, dark: !light, caption: false }).catch(() => { barInk = ""; });
  }
  let barOn = false;   // полоса появилась/пропала — цвет отправляем заново (иначе — только при смене)
  if (window.Memu && Memu.appbar && Memu.appbar.on) Memu.appbar.on((st) => { const on = !!(st && st.active); if (on !== barOn) { barOn = on; barInk = ""; } appbar(); });
  if (typeof SETTINGS_TABS !== "undefined") SETTINGS_TABS.push({ id: "appearance", order: 10, icon: "sparkle", title: () => t("ui.tab"), render });
  apply();
  return { apply, paint, coverBg, accents, coverAccent, scheme, row, hidden, set, ui, render, contrast, appbar, THEMES, DESIGNS };
})();
