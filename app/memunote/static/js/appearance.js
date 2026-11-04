// Appearance: palettes, accents and designs. Applies the server-resolved look to the page and
// builds the pickers for the app menu and Settings → Appearance.
import { api, esc, icon, t } from "./core.js";

const LOOK_KEYS = ["theme", "palette", "accent", "accent_custom", "design"];
let catalog = null, pending = null;

export function lookChanged(patch) { return LOOK_KEYS.some((k) => patch && patch[k] !== undefined); }
export function lookDiffers(a, b) { return LOOK_KEYS.some((k) => (a || {})[k] !== (b || {})[k]); }

export function applyLook(MN, theme) {
  const prev = MN.theme?.tokens || {};
  MN.theme = theme;
  const root = document.documentElement;
  root.dataset.theme = theme.mode;
  root.dataset.design = theme.design || "classic";
  root.dataset.palette = theme.palette || "auto";
  const next = theme.tokens || {};
  for (const k of Object.keys(prev)) if (!(k in next)) root.style.removeProperty(k);
  for (const [k, v] of Object.entries(next)) root.style.setProperty(k, v);
  root.classList.toggle("fallback-tokens", !next["--bg"]);
}

export async function loadCatalog(force = false) {
  if (catalog && !force) return catalog;
  pending ||= api("appearance").then((r) => (catalog = r)).finally(() => (pending = null));
  return pending;
}
export function cachedCatalog() { return catalog; }

const modeOf = (MN) => (MN.theme?.mode === "light" ? "light" : "dark");

// Submenus for the app menu: palette, accent and design with live swatches.
export function lookMenus(MN, save) {
  const s = MN.settings, cat = catalog, mode = modeOf(MN);
  if (!cat) { loadCatalog().catch(() => {}); return []; }
  const pal = cat.palettes.map((p) => ({ label: p.label, checked: (s.palette || "auto") === p.id, swatch: [p[mode].bg, p[mode].accent], run: () => save({ palette: p.id }) }));
  const acc = [
    { label: t("ap_accent_auto"), checked: (s.accent || "auto") === "auto", run: () => save({ accent: "auto" }) },
    { label: t("ap_accent_palette"), checked: s.accent === "palette", run: () => save({ accent: "palette" }) },
    "-",
    ...cat.accents.map((a) => ({ label: a.label, checked: s.accent === a.id, swatch: a[mode], run: () => save({ accent: a.id }) })),
    ...(s.accent_custom ? [{ label: t("ap_accent_custom") + " " + s.accent_custom, checked: s.accent === "custom", swatch: s.accent_custom, run: () => save({ accent: "custom" }) }] : []),
  ];
  const des = cat.designs.map((d) => ({ label: d.label, checked: (s.design || "auto") === d.id, title: d.note, run: () => save({ design: d.id }) }));
  return [
    { label: t("ap_palette"), icon: "color", sub: pal },
    { label: t("ap_accent"), icon: "drop", sub: acc },
    { label: t("ap_design"), icon: "layout", sub: des },
  ];
}

function preview(sw, follow) {
  const vars = `--pv-bg:${sw.bg || "var(--bg)"};--pv-panel:${sw.panel || "var(--panel)"};--pv-text:${sw.text || "var(--text)"};--pv-muted:${sw.muted || "var(--muted)"};--pv-accent:${sw.accent || "var(--accent)"};--pv-kw:${sw.kw || sw.accent || "var(--accent)"};--pv-str:${sw.str || sw.text || "var(--text)"}`;
  const safe = vars.replace(/[<>"]/g, "");
  return `<span class="ap-prev${follow ? " follow" : ""}" style="${safe}"><i class="pv-side"></i><span class="pv-main"><i class="pv-line s"></i><i class="pv-line m"></i><i class="pv-line t"></i></span><i class="pv-dot"></i></span>`;
}

// Settings → Appearance: the full visual picker.
export function appearanceHtml(MN) {
  const cat = catalog; const s = MN.settings; const mode = modeOf(MN);
  if (!cat) return `<p class="muted small">${esc(t("loading"))}</p>`;
  const modes = [["auto", "theme_auto", "settings"], ["dark", "theme_dark", "moon"], ["light", "theme_light", "sun"]]
    .map(([v, k, ic]) => `<button class="ap-mode ${s.theme === v ? "on" : ""}" data-look="theme" data-v="${v}">${icon(ic)}${esc(t(k))}</button>`).join("");
  const pals = cat.palettes.map((p) => `<button class="ap-card ${(s.palette || "auto") === p.id ? "on" : ""}" data-look="palette" data-v="${esc(p.id)}" title="${esc(p.label)}">${preview(p[mode], p.id === "auto")}<b>${esc(p.label)}</b></button>`).join("");
  const accOn = (id) => ((s.accent || "auto") === id ? "on" : "");
  const accents = `<button class="ap-acc word ${accOn("auto")}" data-look="accent" data-v="auto">${esc(t("ap_accent_auto"))}</button>`
    + `<button class="ap-acc word ${accOn("palette")}" data-look="accent" data-v="palette">${esc(t("ap_accent_palette"))}</button>`
    + cat.accents.map((a) => `<button class="ap-acc ${accOn(a.id)}" data-look="accent" data-v="${esc(a.id)}" style="--sw:${esc(a[mode])}" title="${esc(a.label)}" aria-label="${esc(a.label)}"></button>`).join("")
    + `<span class="ap-acc custom ${accOn("custom")}" title="${esc(t("ap_accent_custom"))}"><input type="color" data-look-color value="${esc(s.accent_custom || "#3366ff")}" aria-label="${esc(t("ap_accent_custom"))}"></span>`;
  const designs = cat.designs.map((d) => `<button class="ap-card ap-design ${(s.design || "auto") === d.id ? "on" : ""}" data-look="design" data-v="${esc(d.id)}"><span class="ap-dprev" data-d="${esc(d.id)}"></span><b>${esc(d.label)}</b><small>${esc(d.note)}</small></button>`).join("");
  const follow = MN.theme?.design_setting === "auto" && MN.theme?.design ? `<span class="pill">${esc(t("ap_design_now", { name: (cat.designs.find((d) => d.id === MN.theme.design) || {}).label || MN.theme.design }))}</span>` : "";
  return `<div class="ap-sec"><h4>${esc(t("theme"))}</h4><div class="ap-modes">${modes}</div></div>
    <div class="ap-sec"><h4>${esc(t("ap_palette"))}</h4><div class="ap-grid">${pals}</div><p class="ap-hint">${esc(t("ap_palette_hint"))}</p></div>
    <div class="ap-sec"><h4>${esc(t("ap_accent"))}</h4><div class="ap-accents">${accents}</div><p class="ap-hint">${esc(t("ap_accent_hint"))}</p></div>
    <div class="ap-sec"><h4>${esc(t("ap_design"))}${follow}</h4><div class="ap-grid ap-designs">${designs}</div></div>`;
}

export async function bindAppearance(host, MN, save) {
  try { await loadCatalog(); } catch { host.innerHTML = `<p class="muted small">${esc(t("ap_failed"))}</p>`; return; }
  const paint = () => { host.innerHTML = appearanceHtml(MN); };
  paint();
  host.onclick = async (e) => {
    const b = e.target.closest("[data-look]"); if (!b) return;
    await save({ [b.dataset.look]: b.dataset.v });
    paint();
  };
  // Live while the native picker is open (no repaint, it would close the picker); repaint on commit.
  host.oninput = (e) => {
    const c = e.target.closest("[data-look-color]"); if (!c) return;
    clearTimeout(host._colorTimer);
    host._colorTimer = setTimeout(() => save({ accent: "custom", accent_custom: c.value }), 120);
  };
  host.onchange = async (e) => {
    const c = e.target.closest("[data-look-color]"); if (!c) return;
    clearTimeout(host._colorTimer);
    await save({ accent: "custom", accent_custom: c.value });
    paint();
  };
}
