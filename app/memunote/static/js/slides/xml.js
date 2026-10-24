// MemuNote Slides — XML helpers for OOXML: namespace-agnostic lookups (by localName), fragments
// with the usual prefixes, DrawingML colours (scheme colours, clrMap, lumMod/lumOff/tint/shade/alpha).

export const NS = {
  a: "http://schemas.openxmlformats.org/drawingml/2006/main",
  p: "http://schemas.openxmlformats.org/presentationml/2006/main",
  r: "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
  c: "http://schemas.openxmlformats.org/drawingml/2006/chart",
  pr: "http://schemas.openxmlformats.org/package/2006/relationships",
  ct: "http://schemas.openxmlformats.org/package/2006/content-types",
  mc: "http://schemas.openxmlformats.org/markup-compatibility/2006",
  p14: "http://schemas.microsoft.com/office/powerpoint/2010/main",
};
export const REL = {
  slide: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
  layout: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout",
  master: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster",
  theme: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
  image: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
  notes: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide",
  notesMaster: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster",
  chart: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart",
  hyperlink: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
  office: "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
};
export const CT = {
  slide: "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
  notes: "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml",
  notesMaster: "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml",
  theme: "application/vnd.openxmlformats-officedocument.theme+xml",
  chart: "application/vnd.openxmlformats-officedocument.drawingml.chart+xml",
};
export const DECL = `xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}"`;
export const XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';

export function parseXml(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  const err = doc.getElementsByTagName("parsererror")[0];
  if (err) throw new Error("XML: " + (err.textContent || "").slice(0, 200));
  return doc;
}
export function serialize(node) {
  const s = new XMLSerializer().serializeToString(node);
  return node.nodeType === 9 && !s.startsWith("<?xml") ? XML_HEAD + s : s;
}
/** Parse a fragment written with a:/p:/r:/c: prefixes and import it into `doc`. */
export function frag(doc, xml) {
  const tmp = parseXml(`<w ${DECL} xmlns:c="${NS.c}">${xml}</w>`);
  const el = tmp.documentElement.firstElementChild;
  return doc ? doc.importNode(el, true) : el;
}
export const kids = (el, name) => el ? Array.from(el.children).filter((c) => !name || c.localName === name) : [];
export const kid = (el, name) => { if (!el) return null; for (const c of el.children) if (c.localName === name) return c; return null; };
export function path(el, ...names) { for (const n of names) { el = kid(el, n); if (!el) return null; } return el; }
export function deep(el, name) { if (!el) return null; const all = el.getElementsByTagNameNS("*", name); return all.length ? all[0] : null; }
export function deepAll(el, name) { return el ? Array.from(el.getElementsByTagNameNS("*", name)) : []; }
export const attr = (el, name, dflt = null) => (el && el.hasAttribute(name) ? el.getAttribute(name) : dflt);
export const num = (el, name, dflt = null) => { const v = attr(el, name); if (v === null || v === "") return dflt; const n = Number(v); return Number.isFinite(n) ? n : dflt; };
export const bool = (el, name, dflt = null) => { const v = attr(el, name); return v === null ? dflt : v === "1" || v === "true" || v === "on"; };
export function rid(el, name = "embed") { return el ? (el.getAttributeNS(NS.r, name) || el.getAttribute("r:" + name) || null) : null; }
export const escXml = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]))
  .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g, "");
/** Insert `node` into `parent` before the first child whose localName is in `after` order list after `name`. */
export function insertOrdered(parent, node, order) {
  const idx = order.indexOf(node.localName);
  for (const c of parent.children) {
    const ci = order.indexOf(c.localName);
    if (ci > idx) { parent.insertBefore(node, c); return node; }
  }
  parent.appendChild(node);
  return node;
}
export function removeKids(el, names) { for (const c of Array.from(el?.children || [])) if (names.includes(c.localName)) c.remove(); }

// ------------------------------------------------------------------ colours
export const SCHEME_KEYS = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"];
export const DEFAULT_CLRMAP = { bg1: "lt1", tx1: "dk1", bg2: "lt2", tx2: "dk2", accent1: "accent1", accent2: "accent2", accent3: "accent3",
  accent4: "accent4", accent5: "accent5", accent6: "accent6", hlink: "hlink", folHlink: "folHlink" };
const PRESET = { black: "000000", white: "FFFFFF", red: "FF0000", green: "008000", blue: "0000FF", yellow: "FFFF00", gray: "808080",
  darkGray: "A9A9A9", lightGray: "D3D3D3", orange: "FFA500", navy: "000080", purple: "800080", silver: "C0C0C0" };
const SYS = { windowText: "000000", window: "FFFFFF" };

export function hexToRgb(hex) { const h = String(hex || "000000").replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) || 0); }
export function rgbToHex(r, g, b) { return "#" + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("").toUpperCase(); }
function rgbToHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b); let h = 0, s = 0; const l = (mx + mn) / 2;
  if (mx !== mn) { const d = mx - mn; s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h /= 6; }
  return [h, s, l];
}
function hslToRgb([h, s, l]) {
  if (!s) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const f = (t) => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
  return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255];
}
/** Apply DrawingML modifiers [{n: "lumMod", v: 75000}, …] → {hex, alpha}. */
export function applyMods(hex, mods) {
  let rgb = hexToRgb(hex), alpha = 1;
  for (const { n, v } of mods || []) {
    const f = v / 100000;
    if (n === "lumMod" || n === "lumOff") { const hsl = rgbToHsl(rgb); hsl[2] = n === "lumMod" ? hsl[2] * f : hsl[2] + f; hsl[2] = Math.max(0, Math.min(1, hsl[2])); rgb = hslToRgb(hsl); }
    else if (n === "tint") rgb = rgb.map((c) => c + (255 - c) * (1 - f));
    else if (n === "shade") rgb = rgb.map((c) => c * f);
    else if (n === "alpha") alpha = f;
    else if (n === "satMod") { const hsl = rgbToHsl(rgb); hsl[1] = Math.min(1, hsl[1] * f); rgb = hslToRgb(hsl); }
  }
  return { hex: rgbToHex(...rgb), alpha };
}
/** A colour element parent (e.g. a:solidFill) → model colour: "#RRGGBB" | "scheme:accent1" with optional mods. */
export function readColor(parent) {
  if (!parent) return null;
  const el = Array.from(parent.children).find((c) => ["srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr"].includes(c.localName));
  if (!el) return null;
  const mods = Array.from(el.children).map((m) => ({ n: m.localName, v: num(m, "val", 0) }));
  let base;
  if (el.localName === "srgbClr") base = "#" + (attr(el, "val") || "000000").toUpperCase();
  else if (el.localName === "schemeClr") base = "scheme:" + attr(el, "val");
  else if (el.localName === "sysClr") base = "#" + (attr(el, "lastClr") || SYS[attr(el, "val")] || "000000");
  else if (el.localName === "prstClr") base = "#" + (PRESET[attr(el, "val")] || "000000");
  else if (el.localName === "scrgbClr") base = rgbToHex(...["r", "g", "b"].map((k) => num(el, k, 0) / 100000 * 255));
  else base = "#000000";
  return mods.length ? { c: base, mods } : base;
}
/** model colour → CSS colour string using the theme palette + clrMap. `phClr` = placeholder colour (style refs). */
export function cssColor(color, theme, phClr) {
  if (!color) return null;
  const base = typeof color === "string" ? color : color.c;
  const mods = typeof color === "string" ? null : color.mods;
  let hex = base;
  if (base.startsWith("scheme:")) {
    let key = base.slice(7);
    if (key === "phClr") return phClr ? cssColor(phClr, theme) : "#000000";
    key = (theme?.clrMap || DEFAULT_CLRMAP)[key] || key;
    hex = theme?.colors?.[key] || "#000000";
  }
  if (!mods?.length) return hex;
  const r = applyMods(hex, mods);
  if (r.alpha >= 1) return r.hex;
  const [R, G, B] = hexToRgb(r.hex);
  return `rgba(${R},${G},${B},${r.alpha.toFixed(3)})`;
}
/** model colour → XML (inside a fill / a:solidFill). */
export function colorXml(color) {
  if (!color) return '<a:srgbClr val="000000"/>';
  const base = typeof color === "string" ? color : color.c;
  const mods = typeof color === "string" ? [] : color.mods || [];
  const inner = mods.map((m) => `<a:${m.n} val="${Math.round(m.v)}"/>`).join("");
  if (base.startsWith("scheme:")) return `<a:schemeClr val="${escXml(base.slice(7))}">${inner}</a:schemeClr>`;
  return `<a:srgbClr val="${escXml(base.replace("#", "").toUpperCase().slice(0, 6).padEnd(6, "0"))}">${inner}</a:srgbClr>`;
}

export const EMU_PER_PT = 12700, EMU_PER_PX = 9525, EMU_PER_IN = 914400, EMU_PER_CM = 360000;
