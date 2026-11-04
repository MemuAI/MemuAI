// Appearance: palettes, accents, designs and folder colours. The server resolves the colours
// (appearance.py); this module marks the look on <html> and draws the pickers for Settings → Appearance
// and the «Look» submenu of the ⋯ menu.
import { addStrings, t } from "./i18n.js";
import { api, h, esc, icon, folderSvg } from "./core.js";

addStrings({
  "lk.menu": "Look",
  "lk.mode": "Theme",
  "lk.mode_auto": "Same as MemuAI",
  "lk.dark": "Dark",
  "lk.light": "Light",
  "lk.palette": "Palette",
  "lk.palette_hint": "Every palette has a dark and a light face — the theme picks one.",
  "lk.accent": "Accent colour",
  "lk.accent_auto": "Same as MemuAI",
  "lk.accent_palette": "From the palette",
  "lk.accent_custom": "Custom colour",
  "lk.accent_hint": "A chosen colour adapts to the background, so selections and links stay readable.",
  "lk.design": "Design",
  "lk.design_now": "now: {name}",
  "lk.folders": "Folder colour",
  "lk.rest": "Window and lists",
  "lk.all": "All appearance settings…",
  "lk.failed": "Couldn't load the appearance options",
}, {
  "lk.menu": "Оформление",
  "lk.mode": "Тема",
  "lk.mode_auto": "Как в MemuAI",
  "lk.dark": "Тёмная",
  "lk.light": "Светлая",
  "lk.palette": "Палитра",
  "lk.palette_hint": "У каждой палитры есть тёмный и светлый вариант — его выбирает тема.",
  "lk.accent": "Акцентный цвет",
  "lk.accent_auto": "Как в MemuAI",
  "lk.accent_palette": "Из палитры",
  "lk.accent_custom": "Свой цвет",
  "lk.accent_hint": "Выбранный цвет подстраивается под фон, чтобы выделение и ссылки оставались читаемыми.",
  "lk.design": "Дизайн",
  "lk.design_now": "сейчас: {name}",
  "lk.folders": "Цвет папок",
  "lk.rest": "Окно и списки",
  "lk.all": "Все настройки оформления…",
  "lk.failed": "Не удалось загрузить варианты оформления",
});

// Keys the visual picker owns; the plain rows of the Appearance group skip them.
export const LOOK_KEYS = ["theme", "palette", "accent", "accent_custom", "design", "folder_color"];

export function markLook(theme) {
  const root = document.documentElement;
  root.dataset.design = (theme && theme.design) || "classic";
  root.dataset.palette = (theme && theme.palette) || "auto";
  root.dataset.folders = (theme && theme.folders) || "classic";
}

let catalog = null, pending = null;
export function loadCatalog(force = false) {
  if (catalog && !force) return Promise.resolve(catalog);
  pending ||= api("appearance").then((r) => (catalog = r)).finally(() => { pending = null; });
  return pending;
}

const hex = (v) => (/^#[0-9a-f]{3,8}$/i.test(v || "") ? v : "");
const modeOf = (app) => (app.theme && app.theme.mode === "light" ? "light" : "dark");
const MODES = [["auto", "lk.mode_auto", "invert"], ["dark", "lk.dark", "moon"], ["light", "lk.light", "sun"]];

function palettePreview(sw, follow) {
  const v = (name, value, fallback) => `--pv-${name}:${hex(value) || fallback}`;
  const style = [v("bg", sw.bg, "var(--bg)"), v("panel", sw.panel, "var(--panel)"), v("deep", sw.deep, "var(--bg-deep)"),
    v("text", sw.text, "var(--text)"), v("muted", sw.muted, "var(--muted)"), v("line", sw.border, "var(--border)"),
    v("accent", sw.accent, "var(--accent)")].join(";");
  return `<span class="lk-pv${follow ? " follow" : ""}" style="${style}" aria-hidden="true"><i class="lk-pv-side"><b class="on"></b><b></b><b></b></i>`
    + `<i class="lk-pv-main"><b class="f"></b><b class="f sel"></b><b></b><b></b></i></span>`;
}

function designPreview(id) {
  return `<span class="lk-dz" data-d="${esc(id)}" aria-hidden="true"><i class="dz-tabs"><b class="on"></b><b></b></i><i class="dz-bar"></i>`
    + `<i class="dz-body"><i class="dz-side"><b class="on"></b><b></b><b></b></i><i class="dz-main"><b></b><b class="on"></b><b></b><b></b></i></i><i class="dz-status"></i></span>`;
}

// Settings → Appearance: the visual block (theme, palette, accent, design, folder colour).
export function lookBlock(app, save) {
  const host = h("div", { class: "lk", html: `<p class="muted small">…</p>` });
  const paint = () => {
    const cat = catalog;
    if (!cat) return;
    const s = app.settings, mode = modeOf(app), th = app.theme || {};
    const on = (key, id, dflt) => ((s[key] || dflt) === id);
    const radio = (key, id, dflt) => `role="radio" aria-checked="${on(key, id, dflt)}" data-look="${key}" data-v="${esc(id)}"`;
    const modes = MODES.map(([id, label, ic]) => `<button type="button" class="lk-mode${on("theme", id, "auto") ? " on" : ""}" ${radio("theme", id, "auto")}>${icon(ic)}<span>${esc(t(label))}</span></button>`).join("");
    const pals = cat.palettes.map((p) => `<button type="button" class="lk-card${on("palette", p.id, "auto") ? " on" : ""}" ${radio("palette", p.id, "auto")} title="${esc(p.label)}">${palettePreview(p[mode] || {}, p.id === "auto")}<b>${esc(p.label)}</b></button>`).join("");
    const word = (id, key) => `<button type="button" class="lk-acc word${on("accent", id, "auto") ? " on" : ""}" ${radio("accent", id, "auto")}>${esc(t(key))}</button>`;
    const accents = word("auto", "lk.accent_auto") + word("palette", "lk.accent_palette")
      + cat.accents.map((a) => `<button type="button" class="lk-acc${on("accent", a.id, "auto") ? " on" : ""}" ${radio("accent", a.id, "auto")} style="--sw:${hex(a[mode]) || "var(--accent)"}" title="${esc(a.label)}" aria-label="${esc(a.label)}"></button>`).join("")
      + `<label class="lk-acc custom${on("accent", "custom", "auto") ? " on" : ""}" title="${esc(t("lk.accent_custom"))}"${hex(s.accent_custom) ? ` style="--sw:${s.accent_custom}"` : ""}>`
      + `<input type="color" data-look-color value="${esc(hex(s.accent_custom) && s.accent_custom.length === 7 ? s.accent_custom : "#3366ff")}" aria-label="${esc(t("lk.accent_custom"))}"></label>`
      + (s.accent === "custom" && hex(s.accent_custom) ? `<code class="lk-hex">${esc(s.accent_custom)}</code>` : "");
    const now = th.design_setting === "auto" && th.design ? (cat.designs.find((d) => d.id === th.design) || {}).label : "";
    const designs = cat.designs.map((d) => `<button type="button" class="lk-card lk-design${on("design", d.id, "auto") ? " on" : ""}" ${radio("design", d.id, "auto")}>${designPreview(d.id)}<b>${esc(d.label)}</b><small>${esc(d.note)}</small></button>`).join("");
    const folders = (cat.folders || []).map((f) => `<button type="button" class="lk-chip${on("folder_color", f.id, "classic") ? " on" : ""}" ${radio("folder_color", f.id, "classic")}><span class="lk-fold" data-f="${esc(f.id)}">${folderSvg()}</span><span>${esc(f.label)}</span></button>`).join("");
    host.innerHTML = `<div class="lk-sec"><h4>${esc(t("lk.mode"))}</h4><div class="lk-modes" role="radiogroup" aria-label="${esc(t("lk.mode"))}">${modes}</div></div>
      <div class="lk-sec"><h4>${esc(t("lk.palette"))}</h4><div class="lk-grid" role="radiogroup" aria-label="${esc(t("lk.palette"))}">${pals}</div><p class="lk-hint">${esc(t("lk.palette_hint"))}</p></div>
      <div class="lk-sec"><h4>${esc(t("lk.accent"))}</h4><div class="lk-accents" role="radiogroup" aria-label="${esc(t("lk.accent"))}">${accents}</div><p class="lk-hint">${esc(t("lk.accent_hint"))}</p></div>
      <div class="lk-sec"><h4>${esc(t("lk.design"))}${now ? `<span class="pill">${esc(t("lk.design_now", { name: now }))}</span>` : ""}</h4><div class="lk-grid lk-designs" role="radiogroup" aria-label="${esc(t("lk.design"))}">${designs}</div></div>
      <div class="lk-sec"><h4>${esc(t("lk.folders"))}</h4><div class="lk-chips" role="radiogroup" aria-label="${esc(t("lk.folders"))}">${folders}</div></div>
      <div class="st41-sec lk-rest">${esc(t("lk.rest"))}</div>`;
  };
  host.addEventListener("click", (e) => {
    const b = e.target.closest("[data-look]");
    if (!b || !host.contains(b)) return;
    const key = b.dataset.look, value = b.dataset.v;
    if (app.settings[key] === value) return;
    save({ [key]: value });
    paint();
  });
  // Live while the native colour dialog is open; the final value lands on «change».
  let timer = 0;
  const pick = (input, now) => {
    clearTimeout(timer);
    const run = () => { save({ accent: "custom", accent_custom: input.value.toLowerCase() }); };
    if (now) { run(); paint(); } else timer = setTimeout(run, 150);
  };
  host.addEventListener("input", (e) => { const c = e.target.closest("[data-look-color]"); if (c) pick(c, false); });
  host.addEventListener("change", (e) => { const c = e.target.closest("[data-look-color]"); if (c) pick(c, true); });
  host.repaint = () => { if (!host.querySelector("[data-look-color]:focus")) paint(); };
  loadCatalog(true).then(paint).catch(() => { host.innerHTML = `<p class="muted small">${esc(t("lk.failed"))}</p>`; });
  return host;
}

// The «Look» submenu: quick switches without opening Settings.
export function lookMenu(app, openSettings) {
  const save = (patch) => api("settings", patch).then((res) => app.applySettings(res)).catch(() => {});
  return loadCatalog().then((cat) => {
    const s = app.settings, mode = modeOf(app);
    const pick = (key, dflt) => (id) => (s[key] || dflt) === id;
    const isMode = pick("theme", "auto"), isPal = pick("palette", "auto"), isAcc = pick("accent", "auto"),
      isDes = pick("design", "auto"), isFold = pick("folder_color", "classic");
    return [
      { label: t("lk.mode"), icon: "invert", sub: MODES.map(([id, label, ic]) => ({ label: t(label), checked: isMode(id), run: () => save({ theme: id }) })) },
      { label: t("lk.palette"), icon: "palette", sub: cat.palettes.map((p) => ({ label: p.label, checked: isPal(p.id), swatch: [(p[mode] || {}).bg, (p[mode] || {}).accent], run: () => save({ palette: p.id }) })) },
      { label: t("lk.accent"), icon: "drop", sub: [
        { label: t("lk.accent_auto"), checked: isAcc("auto"), run: () => save({ accent: "auto" }) },
        { label: t("lk.accent_palette"), checked: isAcc("palette"), run: () => save({ accent: "palette" }) },
        { sep: true },
        ...cat.accents.map((a) => ({ label: a.label, checked: isAcc(a.id), swatch: [a[mode]], run: () => save({ accent: a.id }) })),
        ...(hex(s.accent_custom) ? [{ label: `${t("lk.accent_custom")} ${s.accent_custom}`, checked: isAcc("custom"), swatch: [s.accent_custom], run: () => save({ accent: "custom" }) }] : []),
      ] },
      { label: t("lk.design"), icon: "layout", sub: cat.designs.map((d) => ({ label: d.label, checked: isDes(d.id), run: () => save({ design: d.id }) })) },
      { label: t("lk.folders"), icon: "folder", sub: (cat.folders || []).map((f) => ({ label: f.label, checked: isFold(f.id), run: () => save({ folder_color: f.id }) })) },
      { sep: true },
      { label: t("lk.all"), icon: "gear", run: openSettings },
    ];
  });
}

export const swatchColor = hex;
