

const Theme = (() => {

  const THEMES = [
    { id: "cover", h: 252, s: 30, acc: ["#7c5cff", "#ff5c8a", "#20c7b5"], dyn: true, dark: [5.5, 9.5, .42] },
    { id: "midnight", h: 245, s: 34, acc: ["#7c5cff", "#ff5c8a", "#20c7b5"], dark: [4.5, 8, .5] },
    { id: "aurora", h: 172, s: 40, acc: ["#22d39b", "#4f8cff", "#b26bff"], dark: [5, 9, .55] },
    { id: "sunset", h: 14, s: 46, acc: ["#ff7a45", "#ff3d77", "#ffbe3b"], dark: [6, 10, .5] },
    { id: "ocean", h: 206, s: 50, acc: ["#1fb2ff", "#3a6bff", "#2fd8c8"], dark: [5, 9, .55] },
    { id: "neon", h: 284, s: 60, acc: ["#ff2bd6", "#00d9ff", "#a6ff2e"], dark: [4, 8, .65] },
    { id: "sakura", h: 338, s: 44, acc: ["#ff7aa8", "#b77bff", "#ffb38a"], light: [97, 100], dark: [7, 11, .5] },
    { id: "moss", h: 104, s: 26, acc: ["#7fb54a", "#d0b44c", "#3fa481"], dark: [6, 10, .45] },
    { id: "sand", h: 36, s: 42, acc: ["#c27a34", "#9f4f2c", "#6f8a46"], light: [94.5, 98.5], dark: [7, 11, .45] },
    { id: "graphite", h: 220, s: 0, acc: ["#e4e4e7", "#a1a1aa", "#71717a"], mono: true, dark: [6, 10, .3] },
    { id: "vinyl", h: 24, s: 38, acc: ["#e2523b", "#efa13a", "#2f8a83"], light: [93.5, 97.5], dark: [6.5, 10.5, .5] },
    { id: "memu", h: 230, s: 10, acc: ["#d97757", "#e8a33d", "#5a9e8f"], host: true, dark: [6, 10, .4] },
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
  let dynHue = null;
  let customThemes = CustomThemes.loadList(S.prefs.customThemes);
  let previewTheme = null, ctDraft = null, ctHost = null, ctScheme = "dark";
  let ctHistory = [], ctHistoryAt = -1, ctOriginal = null, ctHistLast = 0, ctHistField = "", ctHistQuiet = false;
  let ctEscBound = false;
  const PICKER_SWATCHES = ACCENTS.concat(["#ffffff", "#111114"]);
  let recentColors = [];
  try { if (Array.isArray(S.prefs.recentThemeColors)) recentColors = S.prefs.recentThemeColors.filter((c) => CustomThemes.isHex(c)).slice(0, 12); } catch (e) { recentColors = []; }
  function rememberColor(hex) {
    const c = CustomThemes.isHex(hex) ? CustomThemes.normHex(hex) : "";
    if (!c) return;
    recentColors = [c].concat(recentColors.filter((x) => x !== c)).slice(0, 12);
    try { S.prefs.recentThemeColors = recentColors; savePrefs(); } catch (e) { /* Recent colours are a nicety, never a blocker. */ }
  }

  const old = S.prefs;
  const ui = Object.assign({}, DEFAULTS, {
    motion: old.lite ? "lite" : "full", viz: old.viz === false ? "off" : "ring", npStyle: old.npStyle === "vinyl" ? "vinyl" : "card",
    coverTint: old.coverAccent !== false,
  }, old.ui || {});
  if (!Array.isArray(ui.homeHidden)) ui.homeHidden = [];
  S.prefs.ui = ui;

  const theme = () => {
    if (previewTheme) return previewTheme;
    if (CustomThemes.isCustomId(ui.theme)) {
      const found = customThemes.find((x) => x.id === ui.theme);
      if (found) return CustomThemes.toBuiltin(found);
    }
    return THEMES.find((x) => x.id === ui.theme) || THEMES[0];
  };
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

  function ink(hex, bg) {
    const rgb = hexToRgb(hex); if (!rgb) return hex;
    const [h, s, l0] = rgbToHsl(rgb), light = lum(bg) > .4;
    let l = l0, out = hex;
    for (let i = 0; i < 40 && contrast(out, bg) < 4.6; i++) { l += light ? -2 : 2; out = hsl(h, Math.min(s, light ? 80 : 100), Math.max(4, Math.min(96, l))); }
    return out;
  }

  function solid(hex) {
    if (contrast("#ffffff", hex) >= 4.5 || contrast("#111114", hex) >= 4.5) return hex;
    const [h, s, l0] = rgbToHsl(hexToRgb(hex) || [124, 92, 255]);
    let out = hex;
    for (let l = l0; l > 5 && contrast("#ffffff", out) < 4.5; l -= 2) out = hsl(h, s, l);
    return out;
  }
  function onColor(hex) { return contrast("#ffffff", hex) >= 4.5 ? "#ffffff" : contrast("#111114", hex) >= 4.5 ? "#111114" : "#ffffff"; }

  function tokensForBase(th, sch, hue) {
    const h = hue != null ? hue : th.h, s = hue != null ? 34 : th.s;
    const hostScheme = root.getAttribute("data-theme") === "light" ? "light" : "dark";
    if (th.host && hostScheme !== sch) {
      const a = hexToRgb(getComputedStyle(root).getPropertyValue("--accent").trim());
      return tokensForBase(Object.assign({}, th, { host: false, h: a ? rgbToHsl(a)[0] : th.h, s: 14 }), sch);
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
    const [bl, sl, sat] = th.dark || [5.5, 9.5, .42];
    const k = sat == null ? .42 : sat;
    return build(hsl(h, s * k, bl), hsl(h, s * k * .8, sl), hsl(h, Math.min(20, s * .3), 96), "dark");
  }
  function tokensFor(th, sch, hue) { return CustomThemes.applyTokens(tokensForBase(th, sch, hue), th && th.tokens, sch); }
  function build(bg, surface, text, sch) {
    const dark = sch === "dark", T = hexToRgb(text) || [255, 255, 255], tx = (a) => `rgba(${T[0]},${T[1]},${T[2]},${a})`;
    return {
      "--mm-bg": bg, "--mm-surface": surface, "--mm-text": text,
      "--mm-panel": dark ? tx(.045) : rgba(surface, .78), "--mm-panel-2": tx(dark ? .08 : .055), "--mm-line": tx(dark ? .09 : .1),
      "--mm-hover": tx(dark ? .06 : .045), "--mm-muted": tx(dark ? .68 : .72), "--mm-faint": tx(dark ? .52 : .62),
      "--mm-shadow": dark ? "0 30px 80px -20px rgba(0,0,0,.65), 0 8px 24px -8px rgba(0,0,0,.5)" : "0 30px 70px -28px rgba(30,24,60,.28), 0 8px 20px -12px rgba(30,24,60,.2)",
    };
  }

  function themeAccents(th, sch) {
    if (th.host) { const a = getComputedStyle(root).getPropertyValue("--accent").trim(); if (hexToRgb(a)) { const [h, s, l] = rgbToHsl(hexToRgb(a)); return [a, hsl(h + 30, s, l), hsl(h - 140, s * .7, l)]; } }
    if (th.mono && sch === "light") return ["#27272a", "#52525b", "#71717a"];
    return th.acc;
  }
  function accents() {
    const own = hexToRgb(ui.accent) ? ui.accent : "";
    if (own) { const [h, s, l] = rgbToHsl(hexToRgb(own)); return [own, hsl(h + 34, Math.max(50, s), Math.min(70, l + 4)), hsl(h - 40, Math.max(40, s * .9), Math.max(38, l - 8))]; }
    return themeAccents(theme(), scheme());
  }
  function coverAccent() { return !hexToRgb(ui.accent) && !!theme().dyn; }
  function coverSoft() { return ui.coverTint && !theme().dyn && !hexToRgb(ui.accent); }

  function setVars(map) { Object.keys(map).forEach((k) => root.style.setProperty(k, map[k])); }

  /* Theme-owned CSS variables. The four geometry/font variables also have
     a UI-preference default set by apply(), so they are only overridden
     here; every other variable is removed again when the active theme
     does not provide it, otherwise a previous theme's value would leak
     into the next one through the inline style. */
  const THEME_VAR_UI = ["--mm-font", "--mm-rk", "--mm-tile", "--mm-row"];
  let customCssEl = null;
  function applyThemeVars(th) {
    const vars = (th && th.vars) || {};
    CustomThemes.VAR_NAMES.forEach((name) => {
      if (vars[name]) root.style.setProperty(name, vars[name]);
      else if (THEME_VAR_UI.indexOf(name) < 0 && root.style.removeProperty) root.style.removeProperty(name);
    });
  }
  function applyCustomCss(css, id) {
    if (typeof document === "undefined" || !document.head || !document.createElement || !document.getElementById) return;
    const text = typeof css === "string" ? css.trim() : "";
    if (!text || CustomThemes.validateCss(text)) {
      if (customCssEl) { customCssEl.remove(); customCssEl = null; }
      const old = document.getElementById("mm-custom-theme-css"); if (old) old.remove();
      return;
    }
    if (!customCssEl || !customCssEl.isConnected) {
      customCssEl = document.getElementById("mm-custom-theme-css") || document.createElement("style");
      customCssEl.id = "mm-custom-theme-css";
      document.head.appendChild(customCssEl);
    }
    customCssEl.setAttribute("data-mm-theme", id || "");
    if (customCssEl.textContent !== text) customCssEl.textContent = text;
  }
  function resetTint() {
    dynHue = null;
    paint(accents());
    applyThemeVars(theme());
    applyCustomCss(theme() && theme().css, theme() && theme().id);
  }

  function apply() {
    const sch = scheme(), th = theme();
    root.setAttribute("data-mm-scheme", sch);
    root.setAttribute("data-mm-theme", th.id);
    root.setAttribute("data-mm-covertint", ui.coverTint ? "on" : "off");
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
    applyThemeVars(th);
    applyCustomCss(th.css, th.id);
    /* The theme owns the tint: apply() alone must never leave a previous
       track's cover colours behind (the pink/purple wash that survived a
       theme or accent change). Cover colours are re-applied right after
       this by applyPalette() whenever a track is playing. */
    paint(accents());
    appbar();
    return tok;
  }

  function paint(colors) {
    const list = Array.isArray(colors) && colors.length >= 3 ? colors : accents();
    const bg = getComputedStyle(root).getPropertyValue("--mm-bg").trim() || (scheme() === "light" ? "#f5f4f8" : "#08080b");
    const sol = solid(list[0]);
    setVars({ "--tint": list[0], "--tint-2": list[1], "--tint-3": list[2], "--tint-ink": ink(list[0], bg), "--tint-solid": sol, "--mm-on-tint": onColor(sol) });
    if (theme().dyn) {
      const rgb = hexToRgb(list[0]); const h = rgb ? Math.round(rgbToHsl(rgb)[0]) : null;
      if (h !== dynHue) { dynHue = h; setVars(tokensFor(theme(), scheme(), dynHue)); }
    } else if (dynHue !== null) {
      /* A non-dynamic theme must not keep the previous cover's hue in the
         background tokens: drop it and rebuild from the theme itself. */
      dynHue = null;
      setVars(tokensFor(theme(), scheme(), null));
    }
    return bg;
  }

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


  function saveCustomThemes() { S.prefs.customThemes = customThemes.map((x) => CustomThemes.toExport(x)); savePrefs(); }
  function ctApply() { apply(); applyPalette(Player.current()); }
  function ctCancel() {
    if (!ctDraft && !previewTheme) return;
    ctDraft = null; previewTheme = null;
    ctHistory = []; ctHistoryAt = -1; ctOriginal = null;
    if (ctHost) { const box = ctHost.querySelector("#ct-editor"); if (box) box.innerHTML = ""; }
    ctApply();
  }
  function ctDirty() {
    return !!(ctDraft && ctOriginal && JSON.stringify(ctDraft) !== JSON.stringify(ctOriginal));
  }
  function ctRequestClose() {
    if (!ctDraft) return;
    if (!ctDirty() || typeof modal !== "function") { ctCancel(); return; }
    modal(`<h3>${esc(L("custom.unsaved_title"))}</h3><p class="mm-sub" style="margin:0">${esc(L("custom.unsaved_text"))}</p><div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" data-discard>${esc(L("custom.unsaved_discard"))}</button></div>`,
      (card, close) => { const discard = card.querySelector("[data-discard]"); if (discard) discard.onclick = () => { close(); ctCancel(); }; });
  }
  /* Keep the filled part of every editor slider in sync with its value:
     the custom track is a gradient driven by --fill (see style.css). */
  function ctPaintRanges(scope) {
    const rootEl = scope && scope.querySelectorAll ? scope : (typeof document !== "undefined" ? document : null);
    if (!rootEl) return;
    rootEl.querySelectorAll('input[type="range"]').forEach((el) => {
      const min = Number(el.min), max = Number(el.max), val = Number(el.value);
      const pct = Number.isFinite(min) && Number.isFinite(max) && max > min ? Math.max(0, Math.min(100, (val - min) / (max - min) * 100)) : 50;
      el.style.setProperty("--fill", pct + "%");
    });
  }
  function ctDownload(name, body, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([body], { type }));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  function ctExportCssText(theme) {
    const builtin = CustomThemes.toBuiltin(theme);
    const schemes = {};
    ["dark", "light"].forEach((sch) => {
      const acc = themeAccents(builtin, sch);
      schemes[sch] = Object.assign({}, tokensFor(builtin, sch, null),
        { "--tint": acc[0], "--tint-2": acc[1], "--tint-3": acc[2] });
    });
    return CustomThemes.exportCss(theme, schemes);
  }
  /* The export dialog offers JSON and CSS side by side; the backdrop
     behaviour comes from modal() itself (it only closes on a click
     whose target is the backdrop, or on [data-cancel]). */
  function ctExportDialog(found) {
    if (typeof modal !== "function") { ctDownload(CustomThemes.fileName(found), CustomThemes.exportText(found), "application/json"); return; }
    modal(`<h3>${esc(L("custom.export_title"))}</h3><p class="mm-sub" style="margin:0">${esc(L("custom.export_hint"))}</p><div class="mm-ct-export-acts"><button type="button" class="mm-btn tint" data-exp="json">${esc(L("custom.export_json"))}</button><button type="button" class="mm-btn ghost" data-exp="css">${esc(L("custom.export_css"))}</button><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button></div>`,
      (card, close) => {
        card.querySelectorAll("[data-exp]").forEach((btn) => {
          btn.onclick = () => {
            const fmt = btn.dataset.exp; close();
            if (fmt === "css") ctDownload(CustomThemes.cssFileName(found), ctExportCssText(found), "text/css");
            else ctDownload(CustomThemes.fileName(found), CustomThemes.exportText(found), "application/json");
          };
        });
      });
  }
  function ctRenderEditor() {
    if (!ctHost) return;
    const box = ctHost.querySelector("#ct-editor");
    if (box) { box.innerHTML = ctDraft ? ctEditorHtml() : ""; ctPaintRanges(box); ctPaintCss();
      const cssArea = box.querySelector ? box.querySelector(".mm-ct-css") : null;
      if (cssArea && cssArea.addEventListener) cssArea.addEventListener("scroll", ctSyncCssScroll, { passive: true }); }
  }
  function builtinName(id) { const b = THEMES.find((x) => x.id === id); return b ? L("theme." + id) : id; }
  function ctCard(ct) {
    const th = CustomThemes.toBuiltin(ct);
    return `<div class="mm-ap-card mm-ct-card${ui.theme === ct.id ? " on" : ""}" data-ct-id="${esc(ct.id)}"><button type="button" class="mm-ct-pick" data-v="${esc(ct.id)}" role="radio" aria-checked="${ui.theme === ct.id}" aria-label="${esc(ct.name)}">${mini(th)}<span class="mm-ap-name"><b>${esc(ct.name)}</b><span>${esc(ct.description || ct.author || "")}</span></span><i class="mm-ap-check">${icon("check")}</i></button><span class="mm-ct-actions"><button type="button" class="mm-icon sm" data-ct-act="edit" data-ct-id="${esc(ct.id)}" title="${esc(L("custom.edit"))}" aria-label="${esc(L("custom.edit"))}">${icon("sparkle")}</button><button type="button" class="mm-icon sm" data-ct-act="duplicate" data-ct-id="${esc(ct.id)}" title="${esc(L("custom.duplicate"))}" aria-label="${esc(L("custom.duplicate"))}">${icon("plus")}</button><button type="button" class="mm-icon sm" data-ct-act="export" data-ct-id="${esc(ct.id)}" title="${esc(L("custom.export"))}" aria-label="${esc(L("custom.export"))}">${icon("download")}</button><button type="button" class="mm-icon sm" data-ct-act="delete" data-ct-id="${esc(ct.id)}" title="${esc(L("custom.delete"))}" aria-label="${esc(L("custom.delete"))}">${icon("trash")}</button></span></div>`;
  }
  function ctGroupHtml() {
    return `<div class="mm-ct-toolbar"><button type="button" class="mm-btn ghost sm" data-ct-act="new">${icon("plus")}${esc(L("custom.new"))}</button><button type="button" class="mm-btn ghost sm" data-ct-act="import">${icon("download")}${esc(L("custom.import"))}</button><input type="file" id="ct-file" accept=".json,.css,application/json,text/css" hidden></div>${customThemes.length ? "" : `<p class="mm-ct-empty">${esc(L("custom.empty"))}</p>`}<div class="mm-ct-editor" id="ct-editor"></div>`;
  }
  const TOK_FIELD_DEFS = [
    ["tok-bg", "--mm-bg", "custom.token_bg", true], ["tok-surface", "--mm-surface", "custom.token_surface", true],
    ["tok-text", "--mm-text", "custom.token_text", true], ["tok-panel", "--mm-panel", "custom.token_panel", true],
    ["tok-panel2", "--mm-panel-2", "custom.token_panel2", true], ["tok-line", "--mm-line", "custom.token_line", true],
    ["tok-hover", "--mm-hover", "custom.token_hover", true], ["tok-muted", "--mm-muted", "custom.token_muted", true],
    ["tok-faint", "--mm-faint", "custom.token_faint", true], ["tok-shadow", "--mm-shadow", "custom.token_shadow", false],
  ];
  const TOK_FIELD_MAP = {};
  TOK_FIELD_DEFS.forEach(([f, key]) => { TOK_FIELD_MAP[f] = key; });
  const VAR_FIELD_DEFS = [
    { field: "var-rk", name: "--mm-rk", kind: "pair", min: 0.2, max: 2, step: 0.05, label: "custom.var_rk", def: "1" },
    { field: "var-tile", name: "--mm-tile", kind: "px", min: 100, max: 320, label: "custom.var_tile", def: "176" },
    { field: "var-row", name: "--mm-row", kind: "px", min: 32, max: 88, label: "custom.var_row", def: "56" },
    { field: "var-padx", name: "--mm-pad-x", kind: "px", min: 8, max: 80, label: "custom.var_padx", def: "36" },
    { field: "var-gap", name: "--mm-gap", kind: "px", min: 4, max: 48, label: "custom.var_gap", def: "18" },
    { field: "var-side", name: "--mm-side", kind: "px", min: 160, max: 360, label: "custom.var_side", def: "232" },
    { field: "var-bar", name: "--mm-bar", kind: "px", min: 56, max: 120, label: "custom.var_bar", def: "84" },
    { field: "var-font", name: "--mm-font", kind: "font", label: "custom.var_font" },
    { field: "var-display", name: "--mm-display", kind: "font", label: "custom.var_display" },
    { field: "var-weight", name: "--mm-display-weight", kind: "pair", min: 100, max: 900, step: 50, label: "custom.var_weight", def: "800" },
    { field: "var-tracking", name: "--mm-title-track", kind: "track", min: -0.1, max: 0.1, step: 0.005, label: "custom.var_tracking", def: "-0.035" },
    { field: "var-cover-opacity", name: "--mm-cover-opacity", kind: "pair", min: 0, max: 0.6, step: 0.02, label: "custom.var_cover_opacity", def: "0.34" },
    { field: "var-cover-blur", name: "--mm-cover-blur", kind: "px", min: 0, max: 120, label: "custom.var_cover_blur", def: "72" },
    { field: "var-ambient-opacity", name: "--mm-ambient-opacity", kind: "pair", min: 0, max: 0.8, step: 0.02, label: "custom.var_ambient_opacity", def: "0.55" },
    { field: "var-danger", name: "--mm-danger", kind: "color", label: "custom.var_danger" },
    { field: "var-ok", name: "--mm-ok", kind: "color", label: "custom.var_ok" },
  ];
  const CSS_PRESETS = {
    compact: ":root{--mm-row:44px;--mm-gap:10px;--mm-pad-x:20px;--mm-tile:148px}",
    bigtext: "body{font-size:16px;line-height:1.55}",
    noshadow: "*{box-shadow:none!important}",
  };
  function ctColorParts(value) {
    const v = String(value || "").trim();
    let m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(v);
    if (m) return { hex: CustomThemes.normHex(v), alpha: 1 };
    m = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*([0-9.]+))?\s*\)$/i.exec(v);
    if (m) {
      const hex = "#" + [1, 2, 3].map((i) => Math.max(0, Math.min(255, Number(m[i]))).toString(16).padStart(2, "0")).join("");
      return { hex, alpha: m[4] == null ? 1 : Math.max(0, Math.min(1, Number(m[4]))) };
    }
    return null;
  }
  function ctRgba(hex, alpha) {
    const rgb = hexToRgb(hex) || [0, 0, 0];
    return alpha >= 0.999 ? hex : `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.round(alpha * 100) / 100})`;
  }
  function ctPickerHtml(field, value, withAlpha) {
    const parts = ctColorParts(value) || { hex: "#7c5cff", alpha: 1 };
    const hsl = CustomThemes.hexToHsl(parts.hex) || [252, 70, 60];
    const seen = {}, list = [];
    recentColors.concat(PICKER_SWATCHES).forEach((c) => { const k = c.toLowerCase(); if (!seen[k]) { seen[k] = 1; list.push(c); } });
    const slider = (key, min, max, val, label) => `<label><i>${key}</i><input type="range" data-ct-slider="${key.toLowerCase()}" data-ct-for="${field}" min="${min}" max="${max}" step="1" value="${val}" aria-label="${esc(label)}"></label>`;
    return `<span class="mm-ct-picker" data-ct-picker="${field}"><span class="mm-ct-swatch" style="--sw:${parts.hex}" aria-hidden="true"></span>`
      + `<input class="mm-input mm-ct-hex" data-ct-field="${field}" data-ct-kind="hex" value="${esc(parts.hex)}" maxlength="7" spellcheck="false" autocomplete="off" aria-label="${esc(L("custom.picker_hex"))}">`
      + `<span class="mm-ct-sliders">${slider("H", 0, 359, hsl[0], L("custom.picker_hue"))}${slider("S", 0, 100, hsl[1], L("custom.picker_sat"))}${slider("L", 0, 100, hsl[2], L("custom.picker_light"))}${withAlpha ? slider("A", 0, 100, Math.round(parts.alpha * 100), L("custom.picker_alpha")) : ""}</span>`
      + `<span class="mm-ct-swatches" aria-label="${esc(L("custom.picker_swatches"))}">${list.slice(0, 20).map((c) => `<button type="button" class="mm-ct-sw" data-ct-act="pick" data-ct-for="${field}" data-v="${c}" style="--sw:${c}" aria-label="${c}"></button>`).join("")}</span></span>`;
  }
  function ctComputedToken(key) {
    const got = tokensFor(CustomThemes.toBuiltin(ctDraft), ctScheme)[key];
    return typeof got === "string" ? got : "#7c5cff";
  }
  function ctFieldColor(field) {
    if (!ctDraft) return "#7c5cff";
    if (field.indexOf("acc") === 0) return ctDraft.acc[Number(field.slice(3))] || "#7c5cff";
    if (field.indexOf("tok-") === 0) {
      const key = TOK_FIELD_MAP[field], explicit = (ctDraft.tokens[ctScheme] || {})[key];
      if (explicit && ctColorParts(explicit)) return explicit;
      return ctComputedToken(key);
    }
    if (field.indexOf("var-") === 0) {
      const def = VAR_FIELD_DEFS.find((x) => x.field === field);
      const v = def && ctDraft.vars[def.name];
      if (v && ctColorParts(v)) return v;
      return def && def.name === "--mm-ok" ? "#4ade80" : "#ff7b7b";
    }
    return "#7c5cff";
  }
  function ctPickerAlpha(field) {
    if (!ctHost || !ctHost.querySelector) return 1;
    const box = ctHost.querySelector('[data-ct-picker="' + field + '"]');
    const slider = box && box.querySelector ? box.querySelector('[data-ct-slider="a"]') : null;
    return slider ? Math.max(0, Math.min(100, Number(slider.value) || 0)) / 100 : 1;
  }
  function ctSyncPicker(field) {
    if (!ctHost || !ctHost.querySelector) return;
    const box = ctHost.querySelector('[data-ct-picker="' + field + '"]');
    if (!box || !box.querySelector) return;
    const parts = ctColorParts(ctFieldColor(field)) || { hex: "#7c5cff", alpha: 1 };
    const sw = box.querySelector(".mm-ct-swatch"); if (sw) sw.style.setProperty("--sw", parts.hex);
    const hex = box.querySelector(".mm-ct-hex");
    if (hex && !(typeof document !== "undefined" && document.activeElement === hex)) hex.value = parts.hex;
    const hsl = CustomThemes.hexToHsl(parts.hex) || [0, 0, 0];
    const vals = { h: hsl[0], s: hsl[1], l: hsl[2], a: Math.round(parts.alpha * 100) };
    if (box.querySelectorAll) box.querySelectorAll("[data-ct-slider]").forEach((el) => { const k = el.dataset.ctSlider; if (vals[k] != null && el.value !== String(vals[k])) el.value = String(vals[k]); });
    ctPaintRanges(box);
  }
  function ctSetColor(field, hex, alpha) {
    if (!ctDraft) return;
    const d = ctDraft, value = ctRgba(hex, alpha == null ? 1 : alpha);
    if (field.indexOf("acc") === 0) { const i = Number(field.slice(3)); d.acc[i] = hex; }
    else if (field.indexOf("tok-") === 0) { const key = TOK_FIELD_MAP[field]; if (!key) return; d.tokens[ctScheme] = d.tokens[ctScheme] || {}; d.tokens[ctScheme][key] = value; }
    else if (field.indexOf("var-") === 0) { const def = VAR_FIELD_DEFS.find((x) => x.field === field); if (!def) return; d.vars[def.name] = value; }
    else return;
    ctSyncPicker(field);
    previewTheme = CustomThemes.toBuiltin(d);
    ctApply();
    ctHistPush(field);
  }
  function ctOnSlider(el) {
    if (!ctDraft) return;
    const field = el.dataset.ctFor;
    const box = el.closest ? el.closest("[data-ct-picker]") : null;
    if (!box || !box.querySelector) return;
    const get = (k) => { const s = box.querySelector('[data-ct-slider="' + k + '"]'); return s ? Number(s.value) : null; };
    const h = get("h"), s = get("s"), l = get("l"), a = get("a");
    if (h == null || s == null || l == null) return;
    ctSetColor(field, CustomThemes.hslToHex(h, s, l), a == null ? 1 : a / 100);
  }
  function ctSyncVarInputs(field, value) {
    if (!ctHost || !ctHost.querySelectorAll) return;
    ctHost.querySelectorAll('[data-ct-field="' + field + '"]').forEach((el) => { if (el.value !== String(value)) el.value = String(value); });
  }
  /* CSS highlight for the theme editor's own CSS box. When the host
     (MemuAI) exposes MemuSyntax -- the same tokenizer MemuNote/MemuCode
     use -- its CSS output is reused verbatim; the standalone app has no
     syntax.js, so a small local tokenizer with the same tok-* classes
     covers selectors, properties, values, colours and comments. The
     overlay <pre> only paints: the textarea keeps the text, the caret
     and the scrolling, so editing is never disturbed. */
  function ctHighlightCss(text) {
    try {
      if (typeof window !== "undefined" && window.MemuSyntax && typeof window.MemuSyntax.highlight === "function") {
        return window.MemuSyntax.highlight(String(text || ""), "css").join("\n");
      }
    } catch (e) { /* fall through to the local tokenizer */ }
    return ctHighlightCssLocal(text);
  }
  function ctHighlightCssLocal(text) {
    const src = String(text || "");
    const escHtml = (v) => v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const span = (cls, v) => cls ? '<span class="tok-' + cls + '">' + escHtml(v) + "</span>" : escHtml(v);
    let out = "", i = 0, depth = 0, expectProp = true;
    const n = src.length;
    while (i < n) {
      const rest = src.slice(i);
      const ws = /^\s+/.exec(rest);
      if (ws) { out += ws[0]; i += ws[0].length; continue; }
      if (rest.startsWith("/*")) {
        const end = rest.indexOf("*/", 2);
        const tok = end < 0 ? rest : rest.slice(0, end + 2);
        out += span("com", tok); i += tok.length; continue;
      }
      const ch = src[i];
      if (ch === '"' || ch === "'") {
        let j = i + 1;
        while (j < n && src[j] !== ch) { if (src[j] === "\\") j++; j++; }
        const tok = src.slice(i, Math.min(n, j + 1));
        out += span("str", tok); i += tok.length; continue;
      }
      if (ch === "{") { depth++; expectProp = true; out += span("punct", ch); i++; continue; }
      if (ch === "}") { depth = Math.max(0, depth - 1); expectProp = false; out += span("punct", ch); i++; continue; }
      if (ch === ";") { expectProp = true; out += span("punct", ch); i++; continue; }
      if (depth === 0 && ch === "@") {
        const m = /^@[\w-]+/.exec(rest);
        if (m) { out += span("kw", m[0]); i += m[0].length; continue; }
      }
      if (depth === 0 && ch === ":") {
        const m = /^::?[\w-]+/.exec(rest);
        if (m) { out += span("kw", m[0]); i += m[0].length; continue; }
      }
      if (depth > 0 && expectProp) {
        const m = /^(--[\w-]+|[a-zA-Z][\w-]*)(?=\s*:)/.exec(rest);
        if (m) { out += span("attr", m[0]); i += m[0].length; expectProp = false; continue; }
      }
      if (ch === ":") { expectProp = false; out += span("punct", ch); i++; continue; }
      if (depth > 0 && !expectProp) {
        let m = /^#[0-9a-fA-F]{3,8}\b/.exec(rest);
        if (m) { out += span("num", m[0]); i += m[0].length; continue; }
        m = /^-?(?:\d+\.?\d*|\.\d+)(?:[a-zA-Z%]+)?/.exec(rest);
        if (m) { out += span("num", m[0]); i += m[0].length; continue; }
        m = /^!important\b/.exec(rest);
        if (m) { out += span("kw", m[0]); i += m[0].length; continue; }
        m = /^[a-zA-Z-][\w-]*(?=\()/.exec(rest);
        if (m) { out += span("fn", m[0]); i += m[0].length; continue; }
        m = /^--[\w-]+/.exec(rest);
        if (m) { out += span("var", m[0]); i += m[0].length; continue; }
        m = /^[a-zA-Z-][\w-]*/.exec(rest);
        if (m) { out += span("lit", m[0]); i += m[0].length; continue; }
        out += span("punct", ch); i++; continue;
      }
      let m = /^[.#][\w-]+/.exec(rest);
      if (m) { out += span(ch === "." ? "type" : "fn", m[0]); i += m[0].length; continue; }
      m = /^\[[^\]]*\]/.exec(rest);
      if (m) { out += span("attr", m[0]); i += m[0].length; continue; }
      m = /^-?(?:\d+\.?\d*|\.\d+)(?:[a-zA-Z%]+)?/.exec(rest);
      if (m) { out += span("num", m[0]); i += m[0].length; continue; }
      m = /^[a-zA-Z][\w-]*/.exec(rest);
      if (m) { out += span("tag", m[0]); i += m[0].length; continue; }
      out += span("punct", ch); i++;
    }
    return out;
  }
  function ctPaintCss() {
    if (!ctHost || typeof ctHost.querySelector !== "function") return;
    const area = ctHost.querySelector(".mm-ct-css");
    const code = ctHost.querySelector("#ct-css-hl");
    if (!area || !code) return;
    try { code.innerHTML = ctHighlightCss(area.value) + "\n"; } catch (e) { code.textContent = area.value; return; }
    const wrap = area.closest ? area.closest(".mm-ct-css-wrap") : null;
    if (wrap && wrap.classList) wrap.classList.add("mm-ct-css-live");
    ctSyncCssScroll();
  }
  function ctSyncCssScroll() {
    if (!ctHost || typeof ctHost.querySelector !== "function") return;
    const area = ctHost.querySelector(".mm-ct-css");
    const pre = ctHost.querySelector(".mm-ct-css-hl");
    if (!area || !pre || !pre.style) return;
    pre.style.transform = "translate(" + (-area.scrollLeft) + "px, " + (-area.scrollTop) + "px)";
  }
  function ctCssCheck() {
    if (!ctHost || !ctHost.querySelector) return;
    const bad = ctDraft ? CustomThemes.validateCss(ctDraft.css || "") : "";
    const err = ctHost.querySelector("#ct-css-err"), ok = ctHost.querySelector("#ct-css-ok");
    if (err) { err.hidden = !bad; err.textContent = bad ? t(bad) : ""; }
    if (ok) ok.hidden = !!bad || !(ctDraft && (ctDraft.css || "").trim());
  }
  function ctHistReset() {
    ctHistory = ctDraft ? [JSON.stringify(ctDraft)] : [];
    ctHistoryAt = ctHistory.length - 1;
    ctOriginal = ctDraft ? JSON.parse(JSON.stringify(ctDraft)) : null;
    ctHistLast = 0; ctHistField = "";
  }
  function ctHistPush(field) {
    if (!ctDraft || ctHistQuiet) return;
    const snap = JSON.stringify(ctDraft);
    if (ctHistory[ctHistoryAt] === snap) return;
    const now = Date.now();
    if (field && field === ctHistField && now - ctHistLast < 900 && ctHistoryAt >= 0) ctHistory[ctHistoryAt] = snap;
    else { ctHistory = ctHistory.slice(0, ctHistoryAt + 1); ctHistory.push(snap); if (ctHistory.length > 60) ctHistory.shift(); ctHistoryAt = ctHistory.length - 1; }
    ctHistLast = now; ctHistField = field || "";
    ctHistRender();
  }
  function ctHistRestore(at) {
    if (!ctDraft || at < 0 || at >= ctHistory.length) return;
    ctHistQuiet = true;
    ctHistoryAt = at;
    ctDraft = JSON.parse(ctHistory[at]);
    previewTheme = CustomThemes.toBuiltin(ctDraft);
    ctApply();
    ctRenderEditor();
    ctHistQuiet = false;
    ctHistRender();
  }
  const CT_HIST_LABELS = { name: "custom.name", author: "custom.author", description: "custom.description", h: "custom.hue", s: "custom.sat",
    dyn: "custom.dyn", mono: "custom.mono", base: "custom.base", dark: "custom.dark_bg", light: "custom.light_bg",
    acc: "custom.accents", tokens: "custom.tokens", vars: "custom.vars", css: "custom.css" };
  function ctHistEntries() {
    const out = [];
    for (let i = 1; i < ctHistory.length; i++) {
      let a, b;
      try { a = JSON.parse(ctHistory[i - 1]); b = JSON.parse(ctHistory[i]); } catch (e) { continue; }
      const changed = Object.keys(CT_HIST_LABELS).filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
      out.push({ n: i, label: changed.length ? changed.map((k) => L(CT_HIST_LABELS[k])).join(", ") : L("custom.hist_change") });
    }
    return out.slice(-10).reverse();
  }
  function ctHistEntriesHtml() {
    const entries = ctHistEntries();
    return entries.length ? entries.map((e) => `<li><b>${e.n}</b> ${esc(e.label)}</li>`).join("") : `<li>${esc(L("custom.hist_empty"))}</li>`;
  }
  function ctHistRender() {
    if (!ctHost || !ctHost.querySelector) return;
    const undo = ctHost.querySelector('[data-ct-act="hist-undo"]'), redo = ctHost.querySelector('[data-ct-act="hist-redo"]');
    if (undo) undo.disabled = ctHistoryAt <= 0;
    if (redo) redo.disabled = ctHistoryAt >= ctHistory.length - 1;
    const list = ctHost.querySelector("#ct-hist-list");
    if (list) list.innerHTML = ctHistEntriesHtml();
  }
  function apPickerHtml() {
    const cur = hexToRgb(ui.accent) ? ui.accent.toLowerCase() : "#7c5cff";
    const hsl = CustomThemes.hexToHsl(cur) || [252, 70, 60];
    const slider = (key, max, val, label) => `<label><i>${key}</i><input type="range" data-ap-slider="${key.toLowerCase()}" min="0" max="${max}" step="1" value="${val}" aria-label="${esc(label)}"></label>`;
    return `<span class="mm-ct-picker ap" data-ap-picker><span class="mm-ct-swatch" style="--sw:${cur}" aria-hidden="true"></span>`
      + `<input class="mm-input mm-ct-hex" data-ap-field="accent" value="${esc(cur)}" maxlength="7" spellcheck="false" autocomplete="off" aria-label="${esc(L("accent.custom"))}">`
      + `<span class="mm-ct-sliders">${slider("H", 359, hsl[0], L("custom.picker_hue"))}${slider("S", 100, hsl[1], L("custom.picker_sat"))}${slider("L", 100, hsl[2], L("custom.picker_light"))}</span></span>`;
  }
  const apSetAccent = debounce((hex) => set("accent", hex), 60);
  function apSync(hex) {
    $$("[data-ap-picker]").forEach((box) => {
      const sw = box.querySelector(".mm-ct-swatch"); if (sw) sw.style.setProperty("--sw", hex);
      const input = box.querySelector(".mm-ct-hex");
      if (input && !(typeof document !== "undefined" && document.activeElement === input)) input.value = hex;
      const hsl = CustomThemes.hexToHsl(hex) || [252, 70, 60];
      const vals = { h: hsl[0], s: hsl[1], l: hsl[2] };
      box.querySelectorAll("[data-ap-slider]").forEach((el) => { const v = vals[el.dataset.apSlider]; if (v != null) el.value = String(v); });
      if (ctHost) ctPaintRanges(ctHost);
    });
  }
  function apOnSlider(el) {
    const box = el.closest ? el.closest("[data-ap-picker]") : null;
    if (!box || !box.querySelector) return;
    const get = (k) => { const s = box.querySelector('[data-ap-slider="' + k + '"]'); return s ? Number(s.value) : null; };
    const h = get("h"), s = get("s"), l = get("l");
    if (h == null || s == null || l == null) return;
    const hex = CustomThemes.hslToHex(h, s, l);
    apSync(hex);
    apSetAccent(hex);
  }
  function ctEditorHtml() {
    const d = ctDraft;
    const pair = (field, min, max, step, value) => `<span class="mm-ct-pair"><input type="range" data-ct-field="${field}" min="${min}" max="${max}" step="${step}" value="${value}"><input type="number" data-ct-field="${field}" min="${min}" max="${max}" step="${step}" value="${value}"></span>`;
    const sw = (field, on) => `<label class="mm-switch"><input type="checkbox" data-ct-field="${field}"${on ? " checked" : ""}><i></i></label>`;
    const ctSeg = (act, values, cur, labelOf) => `<div class="mm-seg2" role="radiogroup">${values.map((v) => `<button type="button" role="radio" data-ct-act="${act}" data-v="${v}" aria-checked="${cur === v}">${esc(labelOf(v))}</button>`).join("")}</div>`;
    const from = d.extends ? `<p class="mm-ct-note">${esc(L("custom.from"))}: ${esc(builtinName(d.extends))}</p>` : "";
    const presetBlock = `<div class="mm-ct-sec"><h4>${esc(L("custom.presets"))}</h4><span class="mm-ct-presets">${THEMES.map((th) => `<button type="button" class="mm-btn ghost sm" data-ct-act="preset" data-v="${th.id}">${esc(builtinName(th.id))}</button>`).join("")}</span></div>`;
    const accentRows = [0, 1, 2].map((i) => line(`${L("custom.accents")} ${i + 1}`, "", ctPickerHtml("acc" + i, ctFieldColor("acc" + i), false))).join("");
    const schemeSeg = ctSeg("scheme", ["dark", "light"], ctScheme, (v) => L("mode." + v));
    const tokenRows = TOK_FIELD_DEFS.map(([f, key, label, isColor]) => {
      const explicit = (d.tokens[ctScheme] || {})[key];
      const autoBtn = `<button type="button" class="mm-btn ghost sm" data-ct-act="tok-auto" data-tok="${key}">${esc(L("custom.auto"))}</button>`;
      if (!isColor) return line(L(label), "", `<span class="mm-ct-pair"><input class="mm-input" data-ct-field="${f}" value="${esc(explicit || "")}" placeholder="${esc(ctComputedToken(key))}" style="width:min(360px,100%)" spellcheck="false">${autoBtn}</span>`);
      return line(L(label), "", `<span class="mm-ct-pair">${ctPickerHtml(f, ctFieldColor(f), true)}${autoBtn}</span>`);
    }).join("");
    const varRows = VAR_FIELD_DEFS.map((def) => {
      const cur = d.vars[def.name];
      const autoBtn = `<button type="button" class="mm-btn ghost sm" data-ct-act="var-auto" data-var="${def.name}">${esc(L("custom.auto"))}</button>`;
      if (def.kind === "font") return "";
      if (def.kind === "color") return line(L(def.label), "", `<span class="mm-ct-pair">${ctPickerHtml(def.field, ctFieldColor(def.field), false)}${autoBtn}</span>`);
      const num = cur == null ? NaN : parseFloat(cur);
      const disp = Number.isFinite(num) ? String(num) : def.def;
      if (def.kind === "pair" || def.kind === "track") return line(L(def.label), "", `<span class="mm-ct-pair">${pair(def.field, def.min, def.max, def.step, disp)}${autoBtn}</span>`);
      return line(L(def.label), "", `<span class="mm-ct-pair"><input type="number" class="mm-input" data-ct-field="${def.field}" min="${def.min}" max="${def.max}" step="1" value="${disp}">${autoBtn}</span>`);
    }).join("");
    const fontRow = (def) => {
      const cur = d.vars[def.name] || "";
      const presets = Object.keys(FONTS).map((fk) => `<button type="button" class="mm-btn ghost sm" data-ct-act="font-preset" data-v="${fk}" data-target="${def.name}">${esc(L("font." + fk))}</button>`).join("");
      return line(L(def.label), "", `<span class="mm-ct-font"><input class="mm-input" data-ct-field="${def.field}" value="${esc(cur)}" placeholder="${esc(FONTS.system)}" style="width:min(420px,100%)" spellcheck="false">${presets}<button type="button" class="mm-btn ghost sm" data-ct-act="var-auto" data-var="${def.name}">${esc(L("custom.auto"))}</button></span>`);
    };
    const fontRows = VAR_FIELD_DEFS.filter((def) => def.kind === "font").map(fontRow).join("");
    const cssErr = CustomThemes.validateCss(d.css || "");
    const cssBlock = `<div class="mm-ct-sec"><h4>${esc(L("custom.css"))}</h4><p class="mm-ct-note">${esc(L("custom.css_hint"))}</p>`
      + `<div class="mm-ct-css-wrap"><pre class="mm-ct-css-hl" aria-hidden="true"><code id="ct-css-hl"></code></pre><textarea class="mm-input mm-ct-css" data-ct-field="css" rows="7" spellcheck="false" placeholder=":root{--mm-gap:24px}">${esc(d.css || "")}</textarea></div>`
      + `<p class="mm-ct-css-err" id="ct-css-err"${cssErr ? "" : " hidden"}>${cssErr ? esc(t(cssErr)) : ""}</p>`
      + `<p class="mm-ct-note" id="ct-css-ok"${cssErr || !(d.css || "").trim() ? " hidden" : ""}>${esc(L("custom.css_ok"))}</p>`
      + `<span class="mm-ct-presets">${Object.keys(CSS_PRESETS).map((k) => `<button type="button" class="mm-btn ghost sm" data-ct-act="css-preset" data-v="${k}">${esc(L("custom.css_preset_" + k))}</button>`).join("")}</span></div>`;
    const histBlock = `<div class="mm-ct-sec mm-ct-hist"><h4>${esc(L("custom.history"))}</h4><ol id="ct-hist-list">${ctHistEntriesHtml()}</ol></div>`;
    const headTitle = ctDraft.id ? L("custom.editor_edit") + ": " + ctDraft.name : L("custom.new");
    const head = `<div class="mm-ct-head"><h4>${esc(headTitle)}</h4><button type="button" class="mm-icon sm" data-ct-act="cancel" title="${esc(L("custom.cancel"))}" aria-label="${esc(t("close"))}">${icon("close")}</button></div>`;
    return `<div class="mm-ct-form" role="group" aria-label="${esc(headTitle)}">${head}${from}`
      + line(L("custom.name"), "", `<input class="mm-input" data-ct-field="name" value="${esc(d.name)}" maxlength="80" style="width:min(320px,100%)">`)
      + line(L("custom.author"), "", `<input class="mm-input" data-ct-field="author" value="${esc(d.author)}" maxlength="80" style="width:min(320px,100%)">`)
      + line(L("custom.description"), "", `<input class="mm-input" data-ct-field="description" value="${esc(d.description)}" maxlength="200" style="width:min(420px,100%)">`)
      + presetBlock
      + line(L("custom.hue"), "", pair("h", 0, 359, 1, d.h))
      + line(L("custom.sat"), "", pair("s", 0, 100, 1, d.s))
      + line(L("custom.dark_bg"), "", pair("darkBg", 0, 20, 0.5, d.dark[0]))
      + line(L("custom.dark_surface"), "", pair("darkSf", 0, 35, 0.5, d.dark[1]))
      + line(L("custom.dark_sat"), "", pair("darkSat", 0, 100, 1, Math.round(d.dark[2] * 100)))
      + line(L("custom.light_bg"), "", pair("lightBg", 85, 100, 0.5, d.light[0]))
      + line(L("custom.light_surface"), "", pair("lightSf", 88, 100, 0.5, d.light[1]))
      + accentRows
      + line(L("custom.dyn"), L("custom.dyn_hint"), sw("dyn", d.dyn))
      + line(L("custom.mono"), "", sw("mono", d.mono))
      + line(L("custom.base"), "", ctSeg("base", ["dark", "light"], d.base, (v) => L(v === "dark" ? "custom.base_dark" : "custom.base_light")))
      + `<div class="mm-ct-sec"><h4>${esc(L("custom.tokens"))}</h4>${schemeSeg}${tokenRows}</div>`
      + `<div class="mm-ct-sec"><h4>${esc(L("custom.vars"))}</h4>${varRows}${fontRows}<span class="mm-ct-presets"><button type="button" class="mm-btn ghost sm" data-ct-act="vars-auto">${esc(L("custom.vars_auto"))}</button></span></div>`
      + cssBlock
      + histBlock
      + `<div class="mm-ct-foot"><button type="button" class="mm-btn tint sm" data-ct-act="save">${esc(L("custom.save"))}</button><button type="button" class="mm-btn ghost sm" data-ct-act="revert">${esc(L("custom.hist_revert"))}</button><button type="button" class="mm-btn ghost sm" data-ct-act="reset-draft">${esc(L("custom.hist_reset"))}</button><button type="button" class="mm-btn ghost sm" data-ct-act="hist-undo"${ctHistoryAt <= 0 ? " disabled" : ""}>${esc(L("custom.hist_undo"))}</button><button type="button" class="mm-btn ghost sm" data-ct-act="hist-redo"${ctHistoryAt >= ctHistory.length - 1 ? " disabled" : ""}>${esc(L("custom.hist_redo"))}</button><button type="button" class="mm-btn ghost sm" data-ct-act="cancel">${esc(L("custom.cancel"))}</button></div></div>`;
  }
  function ctPairValue(field) {
    const d = ctDraft;
    return { h: d.h, s: d.s, darkBg: d.dark[0], darkSf: d.dark[1], darkSat: Math.round(d.dark[2] * 100), lightBg: d.light[0], lightSf: d.light[1] }[field];
  }
  function ctOnField(f) {
    if (!ctDraft) return;
    const field = f.dataset.ctField, d = ctDraft;
    if (field === "name" || field === "author" || field === "description") d[field] = f.value;
    else if (field === "dyn" || field === "mono") d[field] = f.checked;
    else if (field === "h" || field === "s") d[field] = Math.round(Number(f.value) || 0);
    else if (field === "darkBg") d.dark[0] = Number(f.value) || 0;
    else if (field === "darkSf") d.dark[1] = Number(f.value) || 0;
    else if (field === "darkSat") d.dark[2] = (Number(f.value) || 0) / 100;
    else if (field === "lightBg") d.light[0] = Number(f.value) || 0;
    else if (field === "lightSf") d.light[1] = Number(f.value) || 0;
    else if (field === "css") {
      d.css = f.value;
      ctCssCheck();
      previewTheme = CustomThemes.toBuiltin(d);
      ctApply();
      ctHistPush(field);
      return;
    }
    else if (f.dataset.ctKind === "hex") {
      const raw = f.value.trim();
      if (!/^#?[0-9a-f]{3}$|^#?[0-9a-f]{6}$/i.test(raw)) return;
      const hex = CustomThemes.normHex(raw[0] === "#" ? raw : "#" + raw);
      ctSetColor(field, hex, ctPickerAlpha(field));
      return;
    }
    else if (field.indexOf("acc") === 0) { const i = Number(field.slice(3)); if (CustomThemes.isHex(f.value)) d.acc[i] = CustomThemes.normHex(f.value); }
    else if (field.indexOf("tok-") === 0) {
      const key = TOK_FIELD_MAP[field];
      if (!key) return;
      if (key === "--mm-shadow") {
        if (!CustomThemes.isSafeToken(f.value)) return;
        d.tokens[ctScheme] = d.tokens[ctScheme] || {};
        if (f.value.trim()) d.tokens[ctScheme][key] = f.value.trim(); else delete d.tokens[ctScheme][key];
      } else return;
    }
    else if (field.indexOf("var-") === 0) {
      const def = VAR_FIELD_DEFS.find((x) => x.field === field);
      if (!def || def.kind === "color") return;
      if (def.kind === "font") {
        const v = f.value.trim();
        if (/[;{}<>]/.test(v) || /url\s*\(|expression/i.test(v)) return;
        if (!v) delete d.vars[def.name]; else d.vars[def.name] = v;
      } else {
        const n = Number(f.value);
        if (!Number.isFinite(n)) return;
        const clamped = Math.max(def.min, Math.min(def.max, n));
        if (def.kind === "px") d.vars[def.name] = clamped + "px";
        else if (def.kind === "track") d.vars[def.name] = clamped + "em";
        else d.vars[def.name] = String(clamped);
        ctSyncVarInputs(field, clamped);
      }
    } else return;
    if (ctPairValue(field) !== undefined && ctHost) {
      const value = String(ctPairValue(field));
      ctHost.querySelectorAll('[data-ct-field="' + field + '"]').forEach((el) => { if (el.value !== value) el.value = value; });
    }
    previewTheme = CustomThemes.toBuiltin(d);
    ctApply();
    ctHistPush(field);
  }
  function ctSave() {
    if (!ctDraft) return;
    const cssErr = CustomThemes.validateCss(ctDraft.css || "");
    if (cssErr) { toast(t(cssErr)); return; }
    let saved;
    try { saved = CustomThemes.normalize(ctDraft, customThemes.map((x) => x.id)); }
    catch (e) { toast(t(e.message || "ui.custom.err.object")); return; }
    try { customThemes = CustomThemes.upsert(customThemes, saved); }
    catch (e) { toast(t(e.message || "ui.custom.err.limit")); return; }
    saveCustomThemes();
    const id = saved.id;
    ctDraft = null; previewTheme = null;
    ctHistory = []; ctHistoryAt = -1; ctOriginal = null;
    set("theme", id);
    if (ctHost) render(ctHost);
    toast(t("ui.custom.saved", { name: saved.name }));
  }
  async function ctImport(file) {
    let theme;
    try { theme = CustomThemes.parseImportFile(await file.text(), file.name); }
    catch (e) { toast(t(e.message || "ui.custom.err.json")); return; }
    if (customThemes.some((x) => x.id === theme.id)) theme = CustomThemes.normalize(Object.assign({}, theme, { id: undefined }), customThemes.map((x) => x.id));
    try { customThemes = CustomThemes.upsert(customThemes, theme); }
    catch (e) { toast(t(e.message || "ui.custom.err.limit")); return; }
    saveCustomThemes();
    set("theme", theme.id);
    if (ctHost) render(ctHost);
    toast(t("ui.custom.imported", { name: theme.name }));
  }
  function ctOpen(id) {
    const found = customThemes.find((x) => x.id === id);
    if (!found) return false;
    ctDraft = JSON.parse(JSON.stringify(found)); ctScheme = scheme();
    previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistReset();
    return true;
  }
  function ctAction(el) {
    const act = el.dataset.ctAct, id = el.dataset.ctId;
    if (act === "pick") {
      if (!ctDraft) return;
      const field = el.dataset.ctFor, hex = CustomThemes.normHex(el.dataset.v || "");
      ctSetColor(field, hex, ctPickerAlpha(field));
      rememberColor(hex);
      return;
    }
    if (act === "preset" && ctDraft) {
      const b = THEMES.find((x) => x.id === el.dataset.v); if (!b) return;
      const fresh = CustomThemes.draftFrom(b, ctDraft.name);
      ["h", "s", "acc", "dyn", "mono", "light", "dark", "extends", "base"].forEach((k) => { ctDraft[k] = fresh[k]; });
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("preset");
      return;
    }
    if (act === "font-preset" && ctDraft) {
      const stack = FONTS[el.dataset.v]; if (!stack) return;
      ctDraft.vars[el.dataset.target] = stack;
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("font-preset");
      return;
    }
    if (act === "var-auto" && ctDraft) {
      delete ctDraft.vars[el.dataset.var];
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("var-auto");
      return;
    }
    if (act === "vars-auto" && ctDraft) {
      ctDraft.vars = {};
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("vars-auto");
      return;
    }
    if (act === "css-preset" && ctDraft) {
      const snippet = CSS_PRESETS[el.dataset.v]; if (!snippet) return;
      ctDraft.css = snippet;
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("css-preset");
      return;
    }
    if (act === "hist-undo") { ctHistRestore(ctHistoryAt - 1); return; }
    if (act === "hist-redo") { ctHistRestore(ctHistoryAt + 1); return; }
    if (act === "revert" && ctDraft && ctOriginal) {
      ctDraft = JSON.parse(JSON.stringify(ctOriginal));
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("revert");
      return;
    }
    if (act === "reset-draft" && ctDraft) {
      const base = THEMES.find((x) => x.id === ctDraft.extends);
      const fresh = base ? CustomThemes.draftFrom(base, ctDraft.name) : CustomThemes.blankDraft();
      const keep = { name: ctDraft.name, author: ctDraft.author, description: ctDraft.description, id: ctDraft.id };
      Object.assign(ctDraft, fresh, keep);
      ctDraft.tokens = { dark: {}, light: {} }; ctDraft.vars = {}; ctDraft.css = "";
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("reset-draft");
      return;
    }
    if (act === "new") {
      ctDraft = CustomThemes.blankDraft(); ctScheme = scheme();
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistReset();
      const name = ctHost && ctHost.querySelector('[data-ct-field="name"]');
      if (name) name.focus();
      return;
    }
    if (act === "edit") { ctOpen(id); return; }
    if (act === "duplicate") {
      const found = customThemes.find((x) => x.id === id);
      if (!found) return;
      try {
        const copy = CustomThemes.normalize(Object.assign({}, found, { id: undefined, name: found.name + L("custom.copy") }), customThemes.map((x) => x.id));
        customThemes = CustomThemes.upsert(customThemes, copy);
        saveCustomThemes(); set("theme", copy.id);
        if (ctHost) render(ctHost);
        toast(t("ui.custom.duplicated", { name: copy.name }));
      } catch (e) { toast(t(e.message || "ui.custom.err.object")); }
      return;
    }
    if (act === "delete") {
      const found = customThemes.find((x) => x.id === id);
      if (!found) return;
      const wasActive = ui.theme === id;
      customThemes = CustomThemes.remove(customThemes, id);
      saveCustomThemes();
      if (ctDraft && ctDraft.id === id) { ctDraft = null; previewTheme = null; ctHistory = []; ctHistoryAt = -1; ctOriginal = null; }
      if (wasActive) set("theme", CustomThemes.fallbackId(customThemes, id));
      if (ctHost) render(ctHost);
      toast(t("ui.custom.deleted"));
      return;
    }
    if (act === "export") {
      const found = customThemes.find((x) => x.id === id);
      if (!found) return;
      ctExportDialog(found);
      return;
    }
    if (act === "import") { const input = ctHost && ctHost.querySelector("#ct-file"); if (input) input.click(); return; }
    if (act === "base" && ctDraft) { ctDraft.base = el.dataset.v === "light" ? "light" : "dark"; ctRenderEditor(); ctHistPush("base"); return; }
    if (act === "scheme") { ctScheme = el.dataset.v === "light" ? "light" : "dark"; ctRenderEditor(); return; }
    if (act === "tok-auto" && ctDraft) {
      if (ctDraft.tokens[ctScheme]) delete ctDraft.tokens[ctScheme][el.dataset.tok];
      previewTheme = CustomThemes.toBuiltin(ctDraft); ctApply(); ctRenderEditor(); ctHistPush("tok-auto");
      return;
    }
    if (act === "save") { ctSave(); return; }
    if (act === "cancel") { ctCancel(); return; }
  }

  const L = (k, v) => t("ui." + k, v);

  function mini(th) {
    const one = (sch) => {
      const tok = tokensFor(th, sch, th.dyn ? 330 : null);
      const acc = th.dyn ? ["#ff6a8a", "#ffb35c", "#6c7bff"] : themeAccents(th, sch);
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
    ctHost = host;
    host.innerHTML = `<div class="mm-ap">
      ${group(L("mode.title"), L("mode.hint"), seg("mode", ["light", "dark", "system", "memu"]))}
      ${group(L("theme.title"), L("theme.hint"), `<div class="mm-ap-themes" role="radiogroup" data-ui="theme">${THEMES.map((th) => `<button type="button" class="mm-ap-card" role="radio" data-v="${th.id}" aria-checked="${ui.theme === th.id}">${mini(th)}<span class="mm-ap-name"><b>${esc(L("theme." + th.id))}</b><span>${esc(L("theme." + th.id + "_hint"))}</span></span><i class="mm-ap-check">${icon("check")}</i></button>`).join("")}${customThemes.map(ctCard).join("")}</div>`)}
      ${group(L("custom.title"), L("custom.hint"), ctGroupHtml())}
      ${group(L("design.title"), L("design.hint"), `<div class="mm-ap-designs" role="radiogroup" data-ui="design">${DESIGNS.map((d) => `<button type="button" class="mm-ap-card" role="radio" data-v="${d}" aria-checked="${ui.design === d}">${designMini(d)}<span class="mm-ap-name"><b>${esc(L("design." + d))}</b><span>${esc(L("design." + d + "_hint"))}</span></span><i class="mm-ap-check">${icon("check")}</i></button>`).join("")}</div>`)}
      ${group(L("accent.title"), L("accent.hint"), `<div class="mm-ap-accents" role="radiogroup" data-ui="accent"><button type="button" class="mm-sw auto" role="radio" data-v="" aria-checked="${!ui.accent}" title="${esc(L("accent.theme"))}" aria-label="${esc(L("accent.theme"))}"></button>${ACCENTS.map((c) => `<button type="button" class="mm-sw" role="radio" data-v="${c}" aria-checked="${ui.accent === c}" style="--sw:${c}" aria-label="${c}"></button>`).join("")}${apPickerHtml()}</div>
        ${line(L("cover_tint"), L("cover_tint_hint"), `<label class="mm-switch"><input type="checkbox" data-ui-bool="coverTint"${ui.coverTint ? " checked" : ""}><i></i></label>`)}`)}
      ${group(L("layout.title"), "", [
        line(L("cover.title"), L("cover.hint"), seg("cover", ["s", "m", "l"])),
        line(L("density.title"), L("density.hint"), seg("density", ["compact", "normal", "comfy"])),
        line(L("radius.title"), "", seg("radius", ["sharp", "normal", "round"])),
        line(L("font.title"), "", `<div class="mm-seg2 fonts" role="radiogroup" data-ui="font">${Object.keys(FONTS).map((f) => `<button type="button" role="radio" data-v="${f}" aria-checked="${ui.font === f}" style="font-family:${esc(FONTS[f])}">${esc(L("font." + f))}</button>`).join("")}</div>`),
        line(L("motion.title"), L("motion.hint"), seg("motion", ["full", "lite", "off"])),
      ].join(""), "lines")}
      ${group(L("np.title"), L("np.hint"), `<div class="mm-ap-np" role="radiogroup" data-ui="npStyle">${["card", "vinyl", "full", "minimal", "center", "karaoke"].map((v) => `<button type="button" class="mm-ap-card" role="radio" data-v="${v}" aria-checked="${ui.npStyle === v}"><span class="mm-npp" data-s="${v}" aria-hidden="true"><i></i><s></s><u></u></span><span class="mm-ap-name"><b>${esc(L("np." + v))}</b></span><i class="mm-ap-check">${icon("check")}</i></button>`).join("")}</div>
        ${line(L("npTheme.title"), L("npTheme.hint"), seg("npTheme", ["dark", "auto"]))}
        ${line(L("viz.title"), L("viz.hint"), seg("viz", ["ring", "wave", "bars", "off"]))}
        ${line(L("bg.title"), L("bg.hint"), seg("bg", ["cover", "gradient", "solid"]))}`)}
      ${group(L("home.title"), L("home.hint"), `<div class="mm-ap-home">${HOME_SECTIONS.map((k) => `<label class="mm-ap-toggle"><span>${esc(L("home." + k))}</span><span class="mm-switch"><input type="checkbox" data-home="${k}"${hidden(k) ? "" : " checked"}><i></i></span></label>`).join("")}</div>`)}
      <div class="mm-ap-foot"><button type="button" class="mm-btn ghost sm" id="ap-reset">${icon("refresh")}${esc(L("reset"))}</button></div>
    </div>`;
    if (ctDraft) ctRenderEditor();
    if (!ctEscBound && typeof document !== "undefined" && document.addEventListener) {
      ctEscBound = true;
      document.addEventListener("keydown", (e) => {
        if (!e || e.key !== "Escape" || !ctDraft) return;
        const m = document.getElementById("modal");
        if (m && !m.hidden) return;
        const tgt = e.target;
        if (!tgt || !tgt.closest || !tgt.closest("#ct-editor")) return;
        e.stopPropagation();
        ctRequestClose();
      }, true);
    }
    if (host.__mmCtBound) return;
    host.__mmCtBound = true;
    host.addEventListener("click", (e) => {
      const act = e.target.closest("[data-ct-act]");
      if (act) { ctAction(act); return; }
      const card = e.target.closest(".mm-ct-card");
      if (card) {
        const id = card.dataset.ctId;
        set("theme", id);
        if (!(ctDraft && ctDraft.id === id)) ctOpen(id);
        return;
      }
      /* Clicks anywhere in the settings panel — including every
         control of the open theme editor — must never close the editor.
         It closes only from its own Cancel/close button, from Escape
         (with the unsaved-changes check), or by opening another theme. */
      const b = e.target.closest("[data-ui] [data-v]"); if (!b) return;
      const key = b.closest("[data-ui]").dataset.ui;
      set(key, b.dataset.v);
    });
    host.addEventListener("change", (e) => {
      const tgt = e.target;
      if (tgt.dataset.uiBool) set(tgt.dataset.uiBool, tgt.checked);
      else if (tgt.dataset.home) { const k = tgt.dataset.home; ui.homeHidden = tgt.checked ? ui.homeHidden.filter((x) => x !== k) : ui.homeHidden.concat(k); changed("homeHidden"); }
    });
    host.addEventListener("input", (e) => {
      const slider = e.target.closest && e.target.closest("[data-ct-slider]");
      if (slider) { ctOnSlider(slider); return; }
      const apSlider = e.target.closest && e.target.closest("[data-ap-slider]");
      if (apSlider) { apOnSlider(apSlider); return; }
      const apHex = e.target.closest && e.target.closest("[data-ap-field]");
      if (apHex) {
        const raw = apHex.value.trim();
        if (/^#?[0-9a-f]{3}$|^#?[0-9a-f]{6}$/i.test(raw)) {
          const hex = CustomThemes.normHex(raw[0] === "#" ? raw : "#" + raw);
          apSync(hex); apSetAccent(hex);
        }
        return;
      }
      const f = e.target.closest && e.target.closest("[data-ct-field]"); if (f) ctOnField(f);
      if (e.target && e.target.classList && e.target.classList.contains("mm-ct-css")) ctPaintCss();
      ctPaintRanges(host);
    });
    host.addEventListener("change", (e) => {
      const slider = e.target.closest && e.target.closest("[data-ct-slider]");
      if (slider && ctDraft) rememberColor(ctFieldColor(slider.dataset.ctFor));
      const apSlider = e.target.closest && e.target.closest("[data-ap-slider]");
      if (apSlider && hexToRgb(ui.accent)) rememberColor(ui.accent);
      const hex = e.target.closest && e.target.closest("[data-ct-kind='hex']");
      if (hex && ctDraft) rememberColor(hex.value);
      const f = e.target.closest && e.target.closest("[data-ct-field]");
      if (f && f.type === "checkbox") ctOnField(f);
      if (e.target.id === "ct-file") { const file = e.target.files && e.target.files[0]; e.target.value = ""; if (file) ctImport(file); }
    });
    host.querySelector("#ap-reset").addEventListener("click", () => { ctDraft = null; previewTheme = null; Object.assign(ui, DEFAULTS, { homeHidden: [] }); changed("design"); render(host); });
  }

  function syncControls() {
    $$("[data-ui]").forEach((g) => { const key = g.dataset.ui; $$("[data-v]", g).forEach((b) => b.setAttribute("aria-checked", String(String(ui[key] || "") === b.dataset.v))); });
    $$(".mm-ap-themes .mm-ap-card").forEach((b) => { const th = THEMES.find((x) => x.id === b.dataset.v); const old = b.querySelector(".mm-tp2"); if (th && old) old.outerHTML = mini(th); });
    $$(".mm-ct-card").forEach((c) => { c.classList.toggle("on", ui.theme === c.dataset.ctId); const ct = customThemes.find((x) => x.id === c.dataset.ctId); const old = c.querySelector(".mm-tp2"); if (ct && old) old.outerHTML = mini(CustomThemes.toBuiltin(ct)); });
    $$("[data-ui-bool]").forEach((i) => { i.checked = !!ui[i.dataset.uiBool]; });
    if (hexToRgb(ui.accent)) apSync(ui.accent);
  }

  let barInk = "";
  function appbar() {
    const bar = window.Memu && Memu.appbar;

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
  let barOn = false;
  if (window.Memu && Memu.appbar && Memu.appbar.on) Memu.appbar.on((st) => { const on = !!(st && st.active); if (on !== barOn) { barOn = on; barInk = ""; } appbar(); });
  if (typeof SETTINGS_TABS !== "undefined") SETTINGS_TABS.push({ id: "appearance", order: 10, icon: "sparkle", title: () => t("ui.tab"), render });
  apply();
  return { apply, paint, coverBg, accents, coverAccent, coverSoft, scheme, row, hidden, set, ui, render, contrast, appbar, THEMES, DESIGNS, resetTint, coverHue: () => dynHue };
})();
