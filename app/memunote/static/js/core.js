// MemuNote — core helpers: API client, i18n, icons, toasts, menus, dialogs, command palette.
import { DICT } from "./i18n.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export function debounce(fn, ms) { let t; const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; d.flush = (...a) => { clearTimeout(t); fn(...a); }; d.cancel = () => clearTimeout(t); return d; }
export const isMac = /Mac/.test(navigator.platform);
export const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
export function fmtBytes(n) { const ru = I18N.lang !== "en"; if (n < 1024) return n + (ru ? " Б" : " B"); const u = ru ? ["КБ", "МБ", "ГБ"] : ["KB", "MB", "GB"]; let i = -1; do { n /= 1024; i++; } while (n >= 1024 && i < 2); return n.toFixed(n < 10 ? 1 : 0) + " " + u[i]; }
export function basename(p) { return String(p || "").split(/[\\/]/).pop(); }
export function dirname(p) { const s = String(p || ""); const i = Math.max(s.lastIndexOf("\\"), s.lastIndexOf("/")); return i > 0 ? s.slice(0, i) : s; }
export function extname(p) { const b = basename(p); const i = b.lastIndexOf("."); return i > 0 ? b.slice(i).toLowerCase() : ""; }

// ------------------------------------------------------------------ API
export const API = { token: "", base: "" };
(function initToken() {
  const m = /[#&]t=([^&]+)/.exec(location.hash);
  if (m) { API.token = decodeURIComponent(m[1]); sessionStorage.setItem("mn_token", API.token); history.replaceState(null, "", location.pathname); }
  else API.token = sessionStorage.getItem("mn_token") || "";
})();
export async function api(path, body, opts = {}) {
  const init = { method: opts.method || (body === undefined ? "GET" : "POST"), headers: { "X-MN-Token": API.token } };
  if (body !== undefined) {
    if (opts.raw) { init.body = body; init.headers["Content-Type"] = opts.type || "text/plain; charset=utf-8"; }
    else { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
  }
  if (opts.signal) init.signal = opts.signal;
  const res = await fetch("/api/" + path, init);
  if (opts.stream) return res;
  let data = null;
  try { data = await res.json(); } catch { data = { ok: false, error: "HTTP " + res.status }; }
  if (!res.ok && !opts.allowError) { const e = new Error(data?.error || "HTTP " + res.status); e.status = res.status; e.data = data; throw e; }
  return data;
}
export const rawUrl = (path) => `/api/raw?path=${encodeURIComponent(path)}&t=${encodeURIComponent(API.token)}`;
export const assetUrl = (path) => `/api/asset?path=${encodeURIComponent(path)}&t=${encodeURIComponent(API.token)}`;

// ------------------------------------------------------------------ i18n
export const I18N = { lang: "ru" };
export function t(key, vars) {
  let s = DICT[I18N.lang]?.[key] ?? DICT.en[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ""));
  return s;
}
export function plural(n, forms) { // forms: ru [one, few, many] / en [one, other]
  if (I18N.lang === "en") return n === 1 ? forms[0] : forms[1];
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return forms[2]; if (b > 1 && b < 5) return forms[1]; if (b === 1) return forms[0]; return forms[2];
}

// ------------------------------------------------------------------ icons (MemuAI line style)
const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
export const ICONS = {
  menu: P('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  plus: P('<path d="M12 5v14M5 12h14"/>'),
  close: P('<path d="m6 6 12 12M18 6 6 18"/>'),
  file: P('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
  fileText: P('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>'),
  markdown: P('<rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M6 15V9l2.5 3L11 9v6M15.5 9v6m0 0-2-2m2 2 2-2"/>'),
  code: P('<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>'),
  doc: P('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="m8.5 12 1.2 5 1.3-4 1.3 4 1.2-5"/>'),
  pdf: P('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M8 17v-4h1.2a1.2 1.2 0 0 1 0 2.4H8M12.5 17v-4h.8a2 2 0 0 1 0 4zM16.5 13H15v4M15 15h1.3"/>'),
  table: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16M15 4v16"/>'),
  json: P('<path d="M8 4H7a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2h1M16 4h1a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2h-1"/>'),
  log: P('<path d="M4 6h16M4 10h10M4 14h16M4 18h8"/>'),
  image: P('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.6"/><path d="m4 18 5-5 4 4 3-3 4 4"/>'),
  globe: P('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/>'),
  folder: P('<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  folderOpen: P('<path d="M3 8V6a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v1"/><path d="M3.5 10h17l-2 9H5.5z"/>'),
  save: P('<path d="M5 3h11l4 4v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 1-2z"/><path d="M8 3v5h7V3M8 21v-6h8v6"/>'),
  search: P('<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>'),
  replace: P('<path d="M4 7h11l-3-3M20 17H9l3 3"/>'),
  undo: P('<path d="M4 8h9a5 5 0 0 1 0 10H8"/><path d="m8 4-4 4 4 4"/>'),
  redo: P('<path d="M20 8h-9a5 5 0 0 0 0 10h5"/><path d="m16 4 4 4-4 4"/>'),
  wrap: P('<path d="M4 6h16M4 12h13a3 3 0 0 1 0 6h-4m0 0 2-2m-2 2 2 2M4 18h6"/>'),
  sparkle: P('<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/><path d="m19 16 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z"/>'),
  settings: P('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21h-4v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1L4.2 17l.1-.1A1.7 1.7 0 0 0 3.1 14H3v-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1L7 4.2l.1.1A1.7 1.7 0 0 0 10 3.1V3h4v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.1v4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  palette: P('<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m8 10 3 3-3 3M13 16h4"/>'),
  split: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>'),
  sidebar: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
  eye: P('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  bold: P('<path d="M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z"/>'),
  italic: P('<path d="M14 5h-4M14 19h-4M14.5 5l-5 14"/>'),
  underline: P('<path d="M7 4v7a5 5 0 0 0 10 0V4M5 20h14"/>'),
  strike: P('<path d="M5 12h14M16 7a4 3 0 0 0-4-2c-2.2 0-4 1.2-4 3s1.5 2.6 4 3M8 17c.6 1.3 2.2 2 4 2 2.2 0 4-1.2 4-3"/>'),
  inlineCode: P('<path d="m9 8-4 4 4 4M15 8l4 4-4 4"/>'),
  h1: P('<path d="M4 6v12M12 6v12M4 12h8M17 10l2-1.5V18"/>'),
  h2: P('<path d="M4 6v12M12 6v12M4 12h8M16 11a2 2 0 0 1 4 .3c0 1.7-4 3.4-4 6.7h4"/>'),
  h3: P('<path d="M4 6v12M12 6v12M4 12h8M16 9.5a2 2 0 1 1 2 3 2 2 0 1 1-2 3"/>'),
  heading: P('<path d="M6 4v16M18 4v16M6 12h12"/>'),
  listUl: P('<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>'),
  listOl: P('<path d="M10 6h10M10 12h10M10 18h10M4 4.5l1.5-1v5M3.5 13.2c.4-1.2 2.6-1 2.3.3-.2.9-2.3 2.3-2.3 2.5H6M3.5 19.5h2.2L4.2 21"/>'),
  checklist: P('<rect x="3" y="4.5" width="5" height="5" rx="1"/><path d="m3.8 16.5 1.4 1.4L8 15M11 7h10M11 17h10"/>'),
  quote: P('<path d="M7 7h4v5c0 2.5-1.5 4-4 5M15 7h4v5c0 2.5-1.5 4-4 5"/>'),
  link: P('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
  hr: P('<path d="M3 12h18M7 7h10M7 17h10" opacity=".9"/>'),
  alignLeft: P('<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>'),
  alignCenter: P('<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>'),
  alignRight: P('<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>'),
  alignJustify: P('<path d="M4 6h16M4 10h16M4 14h16M4 18h16"/>'),
  indent: P('<path d="M10 6h10M10 12h10M10 18h10M4 9l3 3-3 3"/>'),
  outdent: P('<path d="M10 6h10M10 12h10M10 18h10M7 9l-3 3 3 3"/>'),
  color: P('<path d="m6 16 6-12 6 12M8.2 12h7.6"/><path d="M4 20h16" stroke-width="3"/>'),
  highlight: P('<path d="m9 11 6-6 4 4-6 6zM9 11l-4 4v3h3l4-4M3 21h18"/>'),
  eraser: P('<path d="m7 20-4-4 10-10 6 6-8 8zM11 20h10M8.5 10.5l6 6"/>'),
  pageBreak: P('<path d="M6 3v6h12V3M6 21v-6h12v6M3 12h3M9 12h2M13 12h2M18 12h3"/>'),
  sup: P('<path d="m4 8 8 10M12 8 4 18M17 4a1.8 1.8 0 0 1 3.4.6c0 1.3-3.4 2.4-3.4 4.4h3.6"/>'),
  sub: P('<path d="m4 6 8 10M12 6 4 16M17 15a1.8 1.8 0 0 1 3.4.6c0 1.3-3.4 2.4-3.4 4.4h3.6"/>'),
  copy: P('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  check: P('<path d="m5 12 5 5L20 7"/>'),
  retry: P('<path d="M20 6v5h-5"/><path d="M19 11a8 8 0 1 0 1 5"/>'),
  stop: P('<rect x="7" y="7" width="10" height="10" rx="2"/>'),
  send: P('<path d="M12 19V5m-6 6 6-6 6 6"/>'),
  brain: P('<path d="M9.5 3.5A3 3 0 0 0 7 8a3 3 0 0 0-1 5.8A3 3 0 0 0 9 20a2.5 2.5 0 0 0 3-2.4V4.9a2 2 0 0 0-2.5-1.4z"/><path d="M14.5 3.5A3 3 0 0 1 17 8a3 3 0 0 1 1 5.8A3 3 0 0 1 15 20a2.5 2.5 0 0 1-3-2.4"/>'),
  attach: P('<path d="m20 12-8 8a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9 9a2 2 0 1 1-2.8-2.8l8-8"/>'),
  insert: P('<path d="M12 4v10m-4-4 4 4 4-4M5 20h14"/>'),
  more: P('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
  chevron: P('<path d="m6 9 6 6 6-6"/>'),
  chevronRight: P('<path d="m9 6 6 6-6 6"/>'),
  up: P('<path d="m6 15 6-6 6 6"/>'),
  down: P('<path d="m6 9 6 6 6-6"/>'),
  zoomIn: P('<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M8 11h6M11 8v6"/>'),
  zoomOut: P('<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M8 11h6"/>'),
  export: P('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>'),
  print: P('<path d="M7 9V3h10v6M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><rect x="7" y="14" width="10" height="7" rx="1"/>'),
  info: P('<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M12 12v4"/>'),
  warn: P('<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>'),
  moon: P('<path d="M20 15.5A8.5 8.5 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5z"/>'),
  sun: P('<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  keyboard: P('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 16h10"/>'),
  clock: P('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  pin: P('<path d="m14 4 6 6-3 1-4 4-1 5-2-6-6-2 5-1 4-4z"/><path d="m4 20 6-6"/>'),
  outline: P('<path d="M4 5h16M7 10h13M10 15h10M7 20h13"/>'),
  translate: P('<path d="M4 5h9M8.5 3v2c0 4-2 7-5 9M6 9c1.5 2.5 3.5 4 6 5M13 21l4-10 4 10M14.5 17.5h5"/>'),
  wand: P('<path d="m15 4 1 2 2 1-2 1-1 2-1-2-2-1 2-1zM5 20 16 9l-1-1L4 19zM19 13l.7 1.3L21 15l-1.3.7L19 17l-.7-1.3L17 15l1.3-.7z"/>'),
  minimap: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16M6 8h6M6 11h4M6 14h6M17 8h2M17 10h2"/>'),
  lines: P('<path d="M9 6h11M9 12h11M9 18h11M4 6h1M4 12h1M4 18h1"/>'),
  play: P('<path d="m8 5 11 7-11 7z"/>'),
  terminal: P('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7 9 3 3-3 3M12.5 15H17"/>'),   // Round 33: run output
  spell: P('<path d="m4 15 3.5-9 3.5 9M5.3 12h4.4M13 6v9h2.7a2.25 2.25 0 0 0 0-4.5H13M13 10.5h2.2a2.25 2.25 0 0 0 0-4.5H13"/><path d="M4 19c2-1.2 3-1.2 5 0s3 1.2 5 0 3-1.2 5 0"/>'),
  liveMd: P('<path d="M4 6h7M4 10h5M4 14h7M4 18h4"/><path d="m14 18 1-4 5.5-5.5a1.4 1.4 0 0 1 2 2L17 16z"/>'),
  follow: P('<path d="M12 4v14m-6-6 6 6 6-6M5 21h14"/>'),
  lock: P('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
  fullscreen: P('<path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/>'),
  slides: P('<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v3M8 21h8M7 9h6M7 12h10"/>'),   // Round 32
  history: P('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>'),
};
export const icon = (name, cls = "") => `<span class="ic ${cls}">${ICONS[name] || ""}</span>`;

export function kindIcon(kind, lang = "") {
  return { markdown: "markdown", docx: "doc", odt: "doc", rtf: "doc", pdf: "pdf", csv: "table", json: "json", log: "log",
    image: "image", html: "globe", code: "code", sheet: "table", book: "doc", notebook: "code", slides: "slides" }[kind] || (lang ? "code" : "fileText");
}

// ------------------------------------------------------------------ toasts
export function toast(message, opts = {}) {
  let host = $("#toasts");
  if (!host) { host = document.createElement("div"); host.id = "toasts"; document.body.append(host); }
  const el = document.createElement("div");
  el.className = "toast " + (opts.kind || "");
  el.setAttribute("role", "status");
  el.innerHTML = `${opts.kind === "error" ? icon("warn") : opts.kind === "ok" ? icon("check") : icon("info")}<span>${esc(message)}</span>`;
  if (opts.action) {
    const b = document.createElement("button"); b.className = "toast-action"; b.textContent = opts.action.label;
    b.onclick = () => { opts.action.run(); el.remove(); }; el.append(b);
  }
  host.append(el);
  requestAnimationFrame(() => el.classList.add("in"));
  setTimeout(() => { el.classList.remove("in"); setTimeout(() => el.remove(), 250); }, opts.ms || (opts.kind === "error" ? 6000 : 2800));
  return el;
}

// ------------------------------------------------------------------ menus
let openMenu = null;
export function closeMenus() { if (openMenu) { openMenu.remove(); openMenu = null; document.removeEventListener("pointerdown", onDocDown, true); } }
function onDocDown(e) { if (openMenu && !openMenu.contains(e.target)) closeMenus(); }
/** items: [{label, icon, kbd, run, disabled, checked, danger, sub:[...]}, "-" separator, {header}] */
export function showMenu(items, anchor, opts = {}) {
  closeMenus();
  const menu = buildMenu(items, opts);
  document.body.append(menu);
  placeMenu(menu, anchor, opts);
  openMenu = menu;
  setTimeout(() => document.addEventListener("pointerdown", onDocDown, true), 0);
  const first = menu.querySelector(".mi:not([aria-disabled=true])"); first?.focus({ preventScroll: true });
  return menu;
}
function buildMenu(items, opts) {
  const menu = document.createElement("div");
  menu.className = "menu " + (opts.cls || "");
  menu.setAttribute("role", "menu");
  for (const item of items) {
    if (!item) continue;
    if (item === "-") { menu.append(Object.assign(document.createElement("div"), { className: "menu-sep" })); continue; }
    if (item.header) { const h = document.createElement("div"); h.className = "menu-head"; h.textContent = item.header; menu.append(h); continue; }
    const el = document.createElement("button");
    el.className = "mi" + (item.danger ? " danger" : "") + (item.sub ? " has-sub" : "") + (item.cls ? " " + item.cls : "");   // cls: Round 33 (spell suggestions)
    el.setAttribute("role", item.checked !== undefined ? "menuitemcheckbox" : "menuitem");
    if (item.checked !== undefined) el.setAttribute("aria-checked", item.checked ? "true" : "false");
    if (item.disabled) el.setAttribute("aria-disabled", "true");
    el.innerHTML = `<span class="mi-ic">${item.checked ? icon("check") : item.icon ? icon(item.icon) : ""}</span><span class="mi-label">${esc(item.label)}</span>`
      + (item.hint ? `<span class="mi-hint">${esc(item.hint)}</span>` : "")
      + (item.kbd ? `<kbd>${esc(item.kbd)}</kbd>` : "") + (item.sub ? `<span class="mi-arrow">${icon("chevronRight")}</span>` : "");
    if (item.title) el.title = item.title;
    if (item.sub) {
      let subMenu = null;
      const open = () => {
        if (subMenu) return;
        menu.querySelectorAll(":scope > .menu.sub").forEach((m) => m.remove());
        subMenu = buildMenu(item.sub, { cls: "sub" }); subMenu.classList.add("sub");
        document.body.append(subMenu);
        const r = el.getBoundingClientRect();
        placeMenu(subMenu, { x: r.right - 4, y: r.top - 6, right: r.left + 4 }, { side: true });
        subMenu._owner = menu;
        const close = () => { subMenu?.remove(); subMenu = null; };
        el._closeSub = close;
        menu.addEventListener("pointerover", (e) => { if (!el.contains(e.target) && e.target.closest(".mi")) close(); });
        const prev = menu._subs || []; prev.push(subMenu); menu._subs = prev;
      };
      el.addEventListener("pointerenter", open);
      el.addEventListener("click", (e) => { e.stopPropagation(); open(); subMenu?.querySelector(".mi")?.focus(); });
      el.addEventListener("keydown", (e) => { if (e.key === "ArrowRight") { open(); subMenu?.querySelector(".mi")?.focus(); } });
    } else if (!item.disabled) {
      el.addEventListener("click", (e) => { e.stopPropagation(); closeAll(); try { item.run?.(); } catch (err) { console.error(err); } });
    }
    menu.append(el);
  }
  menu.addEventListener("keydown", (e) => {
    const all = Array.from(menu.querySelectorAll(":scope > .mi:not([aria-disabled=true])"));
    const i = all.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); all[(i + 1) % all.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); all[(i - 1 + all.length) % all.length]?.focus(); }
    else if (e.key === "Escape") { e.preventDefault(); if (menu.classList.contains("sub")) { menu.remove(); } else closeAll(); }
    else if (e.key === "ArrowLeft" && menu.classList.contains("sub")) { menu.remove(); }
  });
  return menu;
}
function closeAll() { document.querySelectorAll(".menu.sub").forEach((m) => m.remove()); closeMenus(); }
function placeMenu(menu, anchor, opts = {}) {
  let x, y;
  if (anchor instanceof Element) { const r = anchor.getBoundingClientRect(); x = opts.alignRight ? r.right : r.left; y = r.bottom + 4; menu._anchorTop = r.top; }
  else { x = anchor.x; y = anchor.y; }
  menu.style.left = "0px"; menu.style.top = "0px";
  const mw = menu.offsetWidth, mh = menu.offsetHeight, W = innerWidth, H = innerHeight;
  if (opts.alignRight) x -= mw;
  if (x + mw > W - 6) x = opts.side && anchor.right ? anchor.right - mw : W - mw - 6;
  if (y + mh > H - 6) y = menu._anchorTop !== undefined && menu._anchorTop - mh - 4 > 6 ? menu._anchorTop - mh - 4 : Math.max(6, H - mh - 6);
  menu.style.left = Math.max(6, x) + "px"; menu.style.top = Math.max(6, y) + "px";
}
document.addEventListener("pointerdown", (e) => {
  if (!e.target.closest(".menu")) document.querySelectorAll(".menu.sub").forEach((m) => m.remove());
}, true);

// ------------------------------------------------------------------ dialogs
/** modal({title, body (html), buttons:[{label, value, primary, danger}], onOpen(el), wide, dismiss}) → Promise<value|null> */
export function modal(opts) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal-back";
    const box = document.createElement("div");
    box.className = "modal" + (opts.wide ? " wide" : "") + (opts.cls ? " " + opts.cls : "");
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
    box.innerHTML = `<div class="modal-head"><h2>${esc(opts.title || "")}</h2><button class="icon-btn modal-x" aria-label="${esc(t("close"))}">${icon("close")}</button></div>
      <div class="modal-body">${opts.body || ""}</div>
      ${opts.buttons?.length ? `<div class="modal-foot">${opts.buttons.map((b, i) => `<button class="btn ${b.primary ? "primary" : ""} ${b.danger ? "danger" : ""} ${b.left ? "left" : ""}" data-i="${i}">${esc(b.label)}</button>`).join("")}</div>` : ""}`;
    back.append(box); document.body.append(back);
    const prevFocus = document.activeElement;
    const done = (v) => { back.classList.remove("in"); setTimeout(() => back.remove(), 160); document.removeEventListener("keydown", key, true); prevFocus?.focus?.(); resolve(v); };
    const key = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(null); }
      else if (e.key === "Enter" && !e.shiftKey && opts.enter !== false && !(e.target.tagName === "TEXTAREA")) {
        const p = opts.buttons?.findIndex((b) => b.primary); if (p >= 0) { e.preventDefault(); e.stopPropagation(); finish(p); }
      }
    };
    const finish = (i) => { const b = opts.buttons[i]; const v = typeof b.value === "function" ? b.value(box) : b.value; if (v === undefined && b.keepOpen) return; done(v); };
    box.querySelector(".modal-x").onclick = () => done(null);
    back.addEventListener("pointerdown", (e) => { if (e.target === back && opts.dismiss !== false) done(null); });
    box.querySelectorAll(".modal-foot .btn").forEach((b) => (b.onclick = () => finish(+b.dataset.i)));
    document.addEventListener("keydown", key, true);
    requestAnimationFrame(() => back.classList.add("in"));
    opts.onOpen?.(box, done);
    (box.querySelector("[autofocus]") || box.querySelector(".modal-foot .btn.primary") || box.querySelector("button"))?.focus();
  });
}
export async function prompt(title, label, value = "", opts = {}) {
  const r = await modal({ title, body: `<label class="field"><span>${esc(label)}</span><input class="input" id="mn-prompt" value="${esc(value)}" placeholder="${esc(opts.placeholder || "")}" autofocus spellcheck="false"></label>${opts.note ? `<p class="muted small">${esc(opts.note)}</p>` : ""}`,
    buttons: [{ label: t("cancel"), value: null }, { label: opts.ok || t("ok"), primary: true, value: (box) => box.querySelector("#mn-prompt").value }],
    onOpen: (box) => { const i = box.querySelector("#mn-prompt"); setTimeout(() => { i.focus(); i.select(); }, 30); } });
  return r;
}
export function confirm(title, text, ok, opts = {}) {
  return modal({ title, body: `<p>${esc(text)}</p>`, buttons: [{ label: t("cancel"), value: false }, { label: ok || t("ok"), primary: !opts.danger, danger: !!opts.danger, value: true }] });
}

// ------------------------------------------------------------------ command palette
export function fuzzyScore(query, text) {
  query = query.toLowerCase(); text = text.toLowerCase();
  if (!query) return 1;
  const idx = text.indexOf(query);
  if (idx >= 0) return 100 - idx + (idx === 0 ? 50 : 0);
  let ti = 0, score = 0, streak = 0;
  for (const ch of query) {
    const f = text.indexOf(ch, ti);
    if (f < 0) return 0;
    streak = f === ti ? streak + 1 : 0; score += 1 + streak * 2; ti = f + 1;
  }
  return score;
}
/** palette(items:[{label, hint, kbd, icon, run}], {placeholder}) */
export function palette(items, opts = {}) {
  closeMenus();
  document.querySelector(".palette-back")?.remove();
  const back = document.createElement("div"); back.className = "palette-back";
  back.innerHTML = `<div class="palette" role="dialog"><div class="palette-input">${icon(opts.icon || "search")}<input placeholder="${esc(opts.placeholder || t("palette_placeholder"))}" spellcheck="false"></div><div class="palette-list" role="listbox"></div></div>`;
  document.body.append(back);
  const input = back.querySelector("input"), list = back.querySelector(".palette-list");
  let shown = [], sel = 0;
  const render = () => {
    const q = input.value.trim();
    shown = items.map((it) => ({ it, s: Math.max(fuzzyScore(q, it.label), it.keywords ? fuzzyScore(q, it.keywords) * 0.8 : 0) })).filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s).slice(0, 80).map((x) => x.it);
    if (opts.dynamic) { const extra = opts.dynamic(q); if (extra) shown = extra.concat(shown); }
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    list.innerHTML = shown.length ? shown.map((it, i) => `<div class="pi ${i === sel ? "sel" : ""}" data-i="${i}" role="option">${it.icon ? icon(it.icon) : "<span class='ic'></span>"}<span class="pi-label">${esc(it.label)}</span>${it.hint ? `<span class="pi-hint">${esc(it.hint)}</span>` : ""}${it.kbd ? `<kbd>${esc(it.kbd)}</kbd>` : ""}</div>`).join("")
      : `<div class="pi-empty">${esc(opts.empty || t("nothing_found"))}</div>`;
    list.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
  };
  const close = () => { back.remove(); opts.onClose?.(); };
  const run = (i) => { const it = shown[i]; if (!it) return; close(); setTimeout(() => it.run?.(input.value), 0); };
  input.addEventListener("input", () => { sel = 0; render(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); render(); }
    else if (e.key === "Enter") { e.preventDefault(); run(sel); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
  });
  list.addEventListener("pointerdown", (e) => { const p = e.target.closest(".pi"); if (p) { e.preventDefault(); run(+p.dataset.i); } });
  back.addEventListener("pointerdown", (e) => { if (e.target === back) close(); });
  if (opts.value) input.value = opts.value;
  render(); input.focus();
}

// ------------------------------------------------------------------ misc
export function tooltip(el, text) { el.setAttribute("title", text); el.setAttribute("aria-label", text); }
export function kbd(spec) { return isMac ? spec.replace(/Ctrl/g, "⌘").replace(/Alt/g, "⌥") : spec; }
export function copyText(text) { return navigator.clipboard?.writeText(text).then(() => true, () => fallbackCopy(text)) ?? Promise.resolve(fallbackCopy(text)); }
function fallbackCopy(text) { const ta = document.createElement("textarea"); ta.value = text; document.body.append(ta); ta.select(); const ok = document.execCommand("copy"); ta.remove(); return ok; }
export function countWords(text) {
  if (!text) return 0;
  const m = text.match(/[\p{L}\p{N}][\p{L}\p{N}'’_-]*/gu);
  return m ? m.length : 0;
}
