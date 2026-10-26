
import { t, getLang } from "./i18n.js";

export const S = { token: "", boot: null, settings: {}, sep: "/", platform: "posix", home: "/" };

export async function api(path, body, opts = {}) {
  const init = { method: body === undefined ? "GET" : "POST", headers: { "X-MF-Token": S.token } };
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  if (opts.signal) init.signal = opts.signal;
  const res = await fetch("/api/" + path, init);
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || ("HTTP " + res.status));
    err.code = data.error || ("http_" + res.status); err.detail = data.detail || ""; err.data = data; err.status = res.status;
    throw err;
  }
  return data;
}

export async function ndjson(path, onLine, signal) {
  const res = await fetch("/api/" + path, { headers: { "X-MF-Token": S.token }, signal });
  if (!res.ok) {
    let data = {};
    try { data = await res.json(); } catch (e) {  }
    const err = new Error(data.error || ("HTTP " + res.status));
    err.code = data.error || ("http_" + res.status); err.detail = data.detail || ""; err.status = res.status;
    throw err;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      if (line.trim()) onLine(JSON.parse(line));
    }
  }
  if (buffer.trim()) onLine(JSON.parse(buffer));
}

export const q = (path) => encodeURIComponent(path);
export const rawUrl = (path) => `/api/raw?path=${q(path)}&t=${encodeURIComponent(S.token)}`;
export const thumbUrl = (path, size, mtime) => `/api/thumb?path=${q(path)}&s=${size}&m=${mtime || 0}&t=${encodeURIComponent(S.token)}`;

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function isWin() { return S.platform === "nt"; }
export function joinPath(dir, name) {
  if (!dir) return name;
  const sep = S.sep;
  return dir.endsWith(sep) || dir.endsWith("/") ? dir + name : dir + sep + name;
}
export function baseName(path) {
  if (!path) return "";
  const trimmed = path.length > 1 ? path.replace(/[\\/]+$/, "") : path;
  if (isWin() && /^[A-Za-z]:$/.test(trimmed)) return trimmed + "\\";
  const parts = trimmed.split(/[\\/]/);
  return parts[parts.length - 1] || trimmed;
}
export function parentPath(path) {
  if (!path) return "";
  if (isWin()) {
    if (/^[A-Za-z]:\\?$/.test(path)) return "";
    const trimmed = path.replace(/\\+$/, "");
    const index = trimmed.lastIndexOf("\\");
    if (index < 0) return "";
    const parent = trimmed.slice(0, index);
    return /^[A-Za-z]:$/.test(parent) ? parent + "\\" : parent;
  }
  if (path === "/") return "";
  const trimmed = path.replace(/\/+$/, "");
  const index = trimmed.lastIndexOf("/");
  return index <= 0 ? "/" : trimmed.slice(0, index);
}
export function splitPath(path) {

  const out = [];
  if (!path) return out;
  if (isWin()) {
    const m = path.match(/^([A-Za-z]:)\\?(.*)$/);
    if (m) {
      let acc = m[1] + "\\";
      out.push({ name: m[1], path: acc });
      for (const part of m[2].split("\\").filter(Boolean)) { acc = acc.endsWith("\\") ? acc + part : acc + "\\" + part; out.push({ name: part, path: acc }); }
      return out;
    }
    const unc = path.match(/^\\\\([^\\]+)\\([^\\]+)(.*)$/);
    if (unc) {
      let acc = `\\\\${unc[1]}\\${unc[2]}`;
      out.push({ name: `\\\\${unc[1]}\\${unc[2]}`, path: acc });
      for (const part of unc[3].split("\\").filter(Boolean)) { acc += "\\" + part; out.push({ name: part, path: acc }); }
      return out;
    }
  }
  out.push({ name: "/", path: "/" });
  let acc = "";
  for (const part of path.split("/").filter(Boolean)) { acc += "/" + part; out.push({ name: part, path: acc }); }
  return out;
}
export function samePath(a, b) {
  if (!a || !b) return false;
  const n = (p) => (p.length > 1 ? p.replace(/[\\/]+$/, "") : p);
  return isWin() ? n(a).toLowerCase() === n(b).toLowerCase() : n(a) === n(b);
}
export function isInside(path, root) {
  if (samePath(path, root)) return true;
  const norm = (p) => (isWin() ? p.toLowerCase() : p);
  const r = norm(root).replace(/[\\/]+$/, "") + S.sep;
  return norm(path).startsWith(r);
}
export function driveOf(path) {
  if (isWin()) { const m = /^([A-Za-z]:)/.exec(path || ""); return m ? m[1].toUpperCase() : (path || "").split("\\").slice(0, 4).join("\\"); }
  return "/";
}
export function extOf(name) {
  if (!name) return "";
  if (name.startsWith(".") && name.indexOf(".", 1) < 0) return "";
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}
export function stemOf(name) {
  const ext = extOf(name);
  return ext ? name.slice(0, name.length - ext.length - 1) : name;
}

const FMT = { date: "auto", size: "auto" };
export function setFormats(settings) {
  const date = (settings && settings.date_format) || "auto", size = (settings && settings.size_format) || "auto";
  if (date !== FMT.date) { for (const k of Object.keys(dtCache)) delete dtCache[k]; }
  FMT.date = date; FMT.size = size;
}
export function fmtSize(bytes, exact) {
  if (bytes === null || bytes === undefined || isNaN(bytes)) return "";
  const units = getLang() === "en" ? ["B", "KB", "MB", "GB", "TB"] : ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  if (FMT.size === "kb" && !exact) return fmtNum(Math.ceil(bytes / 1024)) + " " + units[1];
  const step = FMT.size === "decimal" ? 1000 : 1024;
  if (bytes < step) return bytes + " " + units[0];
  let value = bytes, unit = 0;
  while (value >= step && unit < units.length - 1) { value /= step; unit++; }
  const text = (value >= 100 ? value.toFixed(0) : value >= 10 ? value.toFixed(1) : value.toFixed(2)).replace(/\.0+$|(\.\d*?)0+$/, "$1");
  const out = (getLang() === "en" ? text : text.replace(".", ",")) + " " + units[unit];
  return exact ? `${out} (${fmtNum(bytes)} ${units[0]})` : out;
}
const numFmt = {};
export function fmtNum(n) { const lang = getLang(); return (numFmt[lang] ||= new Intl.NumberFormat(lang === "en" ? "en-US" : "ru-RU")).format(Number(n || 0)); }
const dtCache = {};
export function fmtDate(ms, withSeconds) {
  if (!ms) return "";
  const lang = getLang();
  const key = lang + (withSeconds ? "s" : "") + FMT.date;
  const loc = lang === "en" ? "en-GB" : "ru-RU";
  const sec = withSeconds ? "2-digit" : undefined;
  if (FMT.date === "iso") {
    const d = new Date(ms), p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}` + (withSeconds ? ":" + p(d.getSeconds()) : "");
  }
  if (FMT.date === "relative") {
    const d = new Date(ms), now = new Date();
    const day0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const tk = "t" + key;
    if (!dtCache[tk]) dtCache[tk] = new Intl.DateTimeFormat(loc, { hour: "2-digit", minute: "2-digit", second: sec });
    if (ms >= day0 && ms < day0 + 86400000) return (lang === "en" ? "Today, " : "Сегодня, ") + dtCache[tk].format(d);
    if (ms >= day0 - 86400000 && ms < day0) return (lang === "en" ? "Yesterday, " : "Вчера, ") + dtCache[tk].format(d);
  }
  if (!dtCache[key]) dtCache[key] = new Intl.DateTimeFormat(loc, FMT.date === "long"
    ? { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: sec }
    : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: sec });
  return dtCache[key].format(new Date(ms));
}
export function plural(n, forms) {

  if (getLang() === "en") return `${fmtNum(n)} ${n === 1 ? forms[0] : forms[1]}`;
  const n10 = n % 10, n100 = n % 100;
  const f = n10 === 1 && n100 !== 11 ? 0 : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? 1 : 2;
  return `${fmtNum(n)} ${forms[f]}`;
}
export const itemsWord = (n) => plural(n, getLang() === "en" ? ["item", "items"] : ["элемент", "элемента", "элементов"]);

const P = {
  back: '<path d="M15 5l-7 7 7 7"/>', fwd: '<path d="M9 5l7 7-7 7"/>', up: '<path d="M12 19V6M6 11l6-6 6 6"/>',
  refresh: '<path d="M20 6v5h-5"/><path d="M19 11a8 8 0 1 0 1 5"/>',

  code: '<path d="M9 7l-5 5 5 5M15 7l5 5-5 5"/>', image: '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M20.5 16l-5-5-8.5 8"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  folderPlus: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  filePlus: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 11v6M9 14h6"/>',
  cut: '<circle cx="6.5" cy="17.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/><path d="M8.5 16L18 4M15.5 16L6 4"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
  paste: '<rect x="5" y="4.5" width="14" height="16" rx="2"/><path d="M9 4.5V3.5h6v1M9 11h6M9 15h4"/>',
  rename: '<path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M14 7l3 3"/>',
  trash: '<path d="M4.5 7h15M10 11v6M14 11v6M6 7l1 12.5h10L18 7M9 7V4.5h6V7"/>',
  sort: '<path d="M7 4v16M4 7l3-3 3 3M17 20V4M14 17l3 3 3-3"/>',
  view: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  details: '<path d="M4 6h16M4 12h16M4 18h16"/>', list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="5" cy="6" r="1"/><circle cx="5" cy="12" r="1"/><circle cx="5" cy="18" r="1"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  palette: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.6 1.7-1.6h2a4 4 0 0 0 4-4C20.5 6.9 16.7 3.5 12 3.5z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7.5" r="1"/><circle cx="14.5" cy="7.5" r="1"/>',
  tabs: '<path d="M3.5 19.5v-11A1.5 1.5 0 0 1 5 7h4.5l1.5 2h8A1.5 1.5 0 0 1 20.5 10.5v9z"/><path d="M14 7V5.5h4.5A1.5 1.5 0 0 1 20 7v2"/>',
  keyboard: '<rect x="3" y="6.5" width="18" height="11" rx="2"/><path d="M7 10h.01M10 10h.01M13 10h.01M16 10h.01M7 13.5h10"/>',
  tiles: '<rect x="3.5" y="4.5" width="7" height="6" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="6" rx="1.5"/><path d="M13.5 6h7M13.5 9h4.5M13.5 15h7M13.5 18h4.5"/>',
  gallery: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M20 16l-4.5-4.5L8 19"/>',
  split: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M12 4.5v15"/>',
  preview: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M14.5 4.5v15"/>',
  sparkle: '<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/><path d="M19 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  more: '<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>',
  home: '<path d="M4 11l8-6.5 8 6.5"/><path d="M6 9.5V19h12V9.5"/>', pc: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  drive: '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M7 14h.01M11 14h6"/>', usb: '<rect x="7" y="8" width="10" height="13" rx="2"/><path d="M9.5 8V3.5h5V8M10.5 5.5h.01M13.5 5.5h.01"/>',
  cloud: '<path d="M7 18h10.5a4 4 0 0 0 .5-8 6 6 0 0 0-11.5 1.5A3.3 3.3 0 0 0 7 18z"/>', network: '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M6 16v-2h12v2"/>',
  pin: '<path d="M9 4h6l-1 5 3 3H7l3-3zM12 12v8"/>', star: '<path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6z"/>',
  tag: '<path d="M3.5 12V4.5H11l9 9-7.5 7.5z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  desktop: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>', documents: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  downloads: '<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19h14"/>', pictures: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M20 16l-4.5-4.5L8 19"/>',
  music: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>', videos: '<rect x="3" y="5.5" width="13" height="13" rx="2"/><path d="M16 10l5-3v10l-5-3z"/>',
  terminal: '<rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="M7 10l3 2.5L7 15M12.5 15.5H17"/>',
  open: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>', link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  zip: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M11 3v2h2v2h-2v2h2v2h-2v2"/><rect x="10" y="14" width="4" height="4" rx="1"/>',
  extract: '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5V8"/><path d="M4 8h16v10.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z"/><path d="M12 11v6M9.5 14.5 12 17l2.5-2.5"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>', checkAll: '<path d="M3 12.5l3.5 3.5L13 9.5M11 16l1 1 8-8.5"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>', redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  pause: '<path d="M8 5v14M16 5v14"/>', play: '<path d="M7 4.5v15l12-7.5z"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>', eyeOff: '<path d="M3 3l18 18M10.6 6a8.6 8.6 0 0 1 1.4-.1c6 0 9.5 6.1 9.5 6.1a16 16 0 0 1-3 3.6M6.4 7.1A15.6 15.6 0 0 0 2.5 12S6 18.5 12 18.5a8.7 8.7 0 0 0 4.3-1.1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>', dupes: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/><path d="M11.5 14h5"/>',
  send: '<path d="M4 12l16-8-6 16-2.5-6.5z"/>', down: '<path d="M6 9l6 6 6-6"/>', right: '<path d="M9 6l6 6-6 6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>', restore: '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M12 8v4l3 2"/>',
  windows: '<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/><rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>',
  group: '<rect x="4" y="4" width="16" height="4" rx="1"/><path d="M7 12h13M7 16h13M7 20h9"/>', filter: '<path d="M4 5h16l-6 7.5V19l-4 1.5v-8z"/>',
  hash: '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>', warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4.5M12 17.5h.01"/>',
  invert: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17A8.5 8.5 0 0 0 12 3.5z" fill="currentColor"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>', clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
};
export function icon(name, cls = "") { return `<span class="ic ${cls}" aria-hidden="true"><svg viewBox="0 0 24 24">${P[name] || P.file}</svg></span>`; }

const TYPES = [
  ["image", "#4d95f0", ["png", "jpg", "jpeg", "jfif", "gif", "webp", "bmp", "svg", "ico", "tif", "tiff", "heic", "avif", "psd", "raw", "cr2", "nef"]],
  ["video", "#d6537a", ["mp4", "mkv", "avi", "mov", "webm", "wmv", "flv", "m4v", "mpg", "mpeg", "3gp"]],
  ["audio", "#a76cf0", ["mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "opus", "mid", "midi"]],
  ["archive", "#c9944a", ["zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "cab", "iso"]],
  ["pdf", "#e2574c", ["pdf"]],
  ["doc", "#3d7fe0", ["doc", "docx", "odt", "rtf"]],
  ["sheet", "#2f9e5b", ["xls", "xlsx", "ods", "csv", "tsv"]],
  ["slides", "#e07a3d", ["ppt", "pptx", "odp", "key"]],
  ["code", "#5ab3a7", ["py", "js", "ts", "tsx", "jsx", "java", "c", "h", "cpp", "hpp", "cs", "go", "rs", "rb", "php", "html", "htm", "css", "scss", "json", "xml", "yaml", "yml", "toml", "ini", "sh", "ps1", "bat", "cmd", "sql", "kt", "swift", "lua", "vue", "dart"]],
  ["text", "#8c8c96", ["txt", "md", "markdown", "log", "rst", "cfg", "conf", "env", "srt", "vtt", "nfo"]],
  ["exe", "#6c7a99", ["exe", "msi", "dll", "sys", "com", "appimage", "deb", "rpm", "apk", "lnk"]],
  ["font", "#b06cbf", ["ttf", "otf", "woff", "woff2"]],
  ["book", "#9b7b4f", ["epub", "fb2", "mobi", "djvu"]],
];
const EXT_TYPE = {};
for (const [type, color, exts] of TYPES) for (const ext of exts) EXT_TYPE[ext] = [type, color];
export function typeOf(name, isDir) { if (isDir) return "folder"; const e = EXT_TYPE[extOf(name)]; return e ? e[0] : "file"; }
export const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "jfif", "gif", "webp", "bmp", "ico", "tif", "tiff", "avif", "tga", "ppm", "heic"]);
export const BROWSER_IMG = new Set(["png", "jpg", "jpeg", "jfif", "gif", "webp", "bmp", "ico", "svg", "avif"]);
const GLYPH = {
  image: '<circle cx="9.2" cy="13.2" r="1.5" fill="#fff"/><path d="M6.5 19l3.5-3.5 2 2 3-3.5 2.5 5z" fill="#fff"/>',
  video: '<path d="M10 12.5v5.5l4.5-2.75z" fill="#fff"/>', audio: '<path d="M11 18.2V12.8l4-1v4.6" stroke="#fff" stroke-width="1.3" fill="none"/><circle cx="10" cy="18.3" r="1.3" fill="#fff"/><circle cx="14" cy="17.4" r="1.3" fill="#fff"/>',
  archive: '<path d="M11.4 9.5h1.2v1.2h-1.2zM12.6 10.7h1.2v1.2h-1.2zM11.4 11.9h1.2v1.2h-1.2zM12.6 13.1h1.2v1.2h-1.2z" fill="#fff"/><rect x="11.2" y="15" width="2.6" height="3" rx=".7" fill="#fff"/>',
  code: '<path d="M10 13.5l-2 2 2 2M14 13.5l2 2-2 2" stroke="#fff" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  text: '<path d="M8 13h8M8 15.5h8M8 18h5" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/>',
};
export function fileIcon(name, isDir, extra = "") {
  if (isDir) return `<span class="fi">${folderSvg()}${extra}</span>`;
  const ext = extOf(name);
  const meta = EXT_TYPE[ext] || ["file", "#8f96a3"];
  const color = meta[1];
  const glyph = GLYPH[meta[0]] || (ext ? `<text x="12" y="17.6" text-anchor="middle" font-family="Segoe UI, sans-serif" font-size="${ext.length > 3 ? 4.2 : 5}" font-weight="700" fill="#fff">${esc(ext.slice(0, 4).toUpperCase())}</text>` : "");
  return `<span class="fi"><svg viewBox="0 0 24 24"><path d="M6.5 2.5h7.5l4.5 4.5v13a1.5 1.5 0 0 1-1.5 1.5H6.5A1.5 1.5 0 0 1 5 20V4a1.5 1.5 0 0 1 1.5-1.5z" fill="${color}"/><path d="M14 2.5V6a1 1 0 0 0 1 1h3.5" fill="#fff" fill-opacity=".35"/>${glyph}</svg>${extra}</span>`;
}
export function folderSvg() {
  return '<svg viewBox="0 0 24 24"><path d="M2.5 6.5A1.5 1.5 0 0 1 4 5h5.2l1.8 2H20a1.5 1.5 0 0 1 1.5 1.5V9H2.5z" fill="var(--folder-2)"/><path d="M2.5 8.5A1.5 1.5 0 0 1 4 7h16a1.5 1.5 0 0 1 1.5 1.5v9.5a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 18z" fill="var(--folder)"/><path d="M2.5 10h19" stroke="#fff" stroke-opacity=".22"/></svg>';
}

export function toast(text, kind = "ok", action) {
  let box = $(".toasts");
  if (!box) { box = h("div", { class: "toasts" }); document.body.append(box); }
  const el = h("div", { class: "toast " + kind, role: "status" });
  el.innerHTML = icon(kind === "err" ? "warn" : kind === "info" ? "info" : "check") + `<span>${esc(text)}</span>`;
  if (action) {
    const b = h("button", { class: "btn sm", text: action.label });
    b.addEventListener("click", () => { action.run(); el.remove(); });
    el.append(b);
  }
  box.append(el);
  setTimeout(() => el.remove(), action ? 7000 : kind === "err" ? 6000 : 3200);
  return el;
}
export function errText(err) {
  const code = err && (err.code || err.message);
  const key = "err." + code;
  const text = t(key);
  if (text !== key) return text;
  return (err && (err.detail || err.message)) || String(err);
}
export function showError(err) { toast(errText(err), "err"); }

let openMenu = null;
let menuOpener = null;
export function closeMenus() {
  if (openMenu) {
    const inside = openMenu.some((m) => m.contains(document.activeElement));
    openMenu.forEach((m) => m.remove()); openMenu = null;
    if ((inside || document.activeElement === document.body) && menuOpener && document.body.contains(menuOpener)) menuOpener.focus({ preventScroll: true });
  }
  menuOpener = null;
  document.removeEventListener("mousedown", outside, true);
}
function outside(e) { if (openMenu && !openMenu.some((m) => m.contains(e.target))) closeMenus(); }

export function menu(items, x, y, opts = {}) {
  if (!opts.child) { closeMenus(); menuOpener = document.activeElement !== document.body ? document.activeElement : null; }
  const el = h("div", { class: "menu", role: "menu" });
  const buttons = [];
  for (const item of items) {
    if (!item) continue;
    if (item.sep) { el.append(h("div", { class: "msep" })); continue; }
    if (item.head) { el.append(h("div", { class: "mhead", text: item.head })); continue; }
    if (item.node) { el.append(item.node); continue; }
    const b = h("button", { class: "mi" + (item.danger ? " danger" : ""), role: "menuitem", disabled: item.disabled ? true : undefined });
    const check = item.checked === undefined ? "" : `<span class="chk">${item.checked ? icon("check", "sm") : ""}</span>`;
    b.innerHTML = check + (item.img ? `<img class="ic app-ic" alt="" src="${esc(item.img)}">` : item.icon ? icon(item.icon) : (item.checked === undefined && !opts.noIconPad ? '<span class="ic"></span>' : "")) +
      `<span class="lbl">${esc(item.label)}</span>` + (item.kb ? `<span class="kb">${esc(item.kb)}</span>` : "") +
      (item.sub ? `<span class="sub-caret">${icon("right", "sm")}</span>` : "");
    if (item.sub) {
      const openSub = (e) => {
        const focus = !e || e.type !== "mouseenter";
        if (openMenu) openMenu.slice(openMenu.indexOf(el) + 1).forEach((m) => { m.remove(); openMenu.splice(openMenu.indexOf(m), 1); });
        const r = b.getBoundingClientRect();
        const subItems = typeof item.sub === "function" ? item.sub() : item.sub;
        if (subItems && typeof subItems.then === "function") {
          const child = menu([{ label: t("ctx.loading"), disabled: true }], r.right - 4, r.top - 5, { child: true, parentRect: r, parentButton: b, focus });
          subItems.then((list) => {
            if (!openMenu || !openMenu.includes(child)) return;
            const hadFocus = child.contains(document.activeElement);
            openMenu.splice(openMenu.indexOf(child), 1); child.remove();
            menu(list, r.right - 4, r.top - 5, { child: true, parentRect: r, parentButton: b, focus: hadFocus || focus });
          });
          return;
        }
        menu(subItems, r.right - 4, r.top - 5, { child: true, parentRect: r, parentButton: b, focus });
      };
      b.addEventListener("mouseenter", openSub);
      b.addEventListener("click", openSub);
    } else {
      b.addEventListener("mouseenter", () => { if (openMenu) openMenu.slice(openMenu.indexOf(el) + 1).forEach((m) => { m.remove(); openMenu.splice(openMenu.indexOf(m), 1); }); });
      b.addEventListener("click", (e) => { e.stopPropagation(); closeMenus(); if (!item.disabled && item.run) item.run(); });
    }
    buttons.push(b);
    el.append(b);
  }
  document.body.append(el);

  const r = { width: el.offsetWidth, height: el.offsetHeight };
  let left = x, top = y;
  if (left + r.width > innerWidth - 6) left = opts.parentRect ? Math.max(6, opts.parentRect.left - r.width + 4) : Math.max(6, innerWidth - r.width - 6);
  if (top + r.height > innerHeight - 6) top = Math.max(6, innerHeight - r.height - 6);
  el.style.left = left + "px"; el.style.top = top + "px";
  if (!openMenu) openMenu = [];
  openMenu.push(el);
  setTimeout(() => document.addEventListener("mousedown", outside, true), 0);

  let index = -1;
  el.tabIndex = -1;
  el.addEventListener("keydown", (e) => {
    const enabled = buttons.filter((b) => !b.disabled);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      index = (index + (e.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length;
      enabled[index].focus();
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      if (opts.child) { el.remove(); openMenu.splice(openMenu.indexOf(el), 1); if (opts.parentButton) opts.parentButton.focus(); } else closeMenus();
    }
    else if (e.key === "ArrowRight" && document.activeElement && document.activeElement.querySelector(".sub-caret")) { e.preventDefault(); document.activeElement.click(); }
    else if (e.key === "ArrowLeft" && opts.child) { e.preventDefault(); el.remove(); openMenu.splice(openMenu.indexOf(el), 1); if (opts.parentButton) opts.parentButton.focus(); }
  });
  if (opts.focus !== false) setTimeout(() => el.focus(), 0);
  return el;
}

export function modal({ title, icon: ico, body, buttons = [], wide, xwide, danger, onClose, left, keys }) {
  const back = h("div", { class: "modal-back" });
  const box = h("div", { class: "modal" + (wide ? " wide" : "") + (xwide ? " xwide" : ""), role: "dialog", "aria-modal": "true" });
  const head = h("div", { class: "modal-head" });
  head.innerHTML = (ico ? `<span class="mh-ico${danger ? " danger" : ""}">${icon(ico)}</span>` : "") + `<h2>${esc(title)}</h2>`;
  const closeBtn = h("button", { class: "icon-btn sm", title: t("common.close"), html: icon("x") });
  head.append(closeBtn);
  const bodyEl = h("div", { class: "modal-body" });
  if (body instanceof Node) bodyEl.append(body); else bodyEl.innerHTML = body || "";
  const foot = h("div", { class: "modal-foot" });
  if (left) foot.append(h("div", { class: "left" }, left));
  const opener = document.activeElement;
  const close = (value) => {
    back.remove();
    document.removeEventListener("keydown", onKey, true);
    if (onClose) onClose(value);
    if (opener && opener !== document.body && document.body.contains(opener) && (!document.activeElement || document.activeElement === document.body || !document.body.contains(document.activeElement)))
      opener.focus({ preventScroll: true });
  };
  let primary = null;
  for (const b of buttons) {
    const el = h("button", { class: "btn " + (b.cls || ""), text: b.label });
    el.addEventListener("click", async () => {
      if (b.run) {
        const keep = await b.run(el);
        if (keep === false) return;
      }
      close(b.value);
    });
    if (b.primary) primary = el;
    foot.append(el);
  }
  box.append(head, bodyEl);
  if (buttons.length || left) box.append(foot);
  back.append(box);
  closeBtn.addEventListener("click", () => close(null));
  back.addEventListener("mousedown", (e) => { if (e.target === back) back.dataset.down = "1"; });
  back.addEventListener("mouseup", (e) => { if (e.target === back && back.dataset.down) close(null); back.dataset.down = ""; });
  function onKey(e) {
    if (!document.body.contains(back)) return;
    const modals = $$(".modal-back");
    if (modals[modals.length - 1] !== back) return;
    if (keys && keys(e)) { e.preventDefault(); e.stopPropagation(); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(null); }
    else if (e.key === "Enter" && primary && !(e.target instanceof HTMLTextAreaElement) && !e.target.closest(".no-enter")) { e.preventDefault(); e.stopPropagation(); primary.click(); }
    e.stopPropagation();
  }
  document.addEventListener("keydown", onKey, true);
  document.body.append(back);
  setTimeout(() => { const f = box.querySelector("[autofocus]") || primary || closeBtn; if (f) f.focus(); }, 20);
  return { el: box, body: bodyEl, foot, close };
}

export function confirmDialog({ title, text, ok, danger, icon: ico, extra }) {
  return new Promise((resolve) => {
    const body = h("div", {});
    body.innerHTML = `<p style="margin:2px 0 6px;line-height:1.5">${esc(text)}</p>`;
    if (extra) body.append(extra);
    modal({ title, icon: ico || (danger ? "warn" : "info"), danger, body,
      buttons: [{ label: t("common.cancel"), cls: "ghost", value: false }, { label: ok || t("common.ok"), cls: danger ? "danger solid" : "primary", primary: true, value: true }],
      onClose: (v) => resolve(!!v) });
  });
}

export function promptDialog({ title, label, value = "", ok, icon: ico, select }) {
  return new Promise((resolve) => {
    const input = h("input", { class: "input", value, autofocus: true, spellcheck: "false" });
    const body = h("div", { class: "field" }, h("span", { text: label || "" }), input);
    let result = null;
    modal({ title, icon: ico || "rename", body,
      buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: ok || t("common.ok"), cls: "primary", primary: true, run: () => { result = input.value; } }],
      onClose: () => resolve(result) });
    setTimeout(() => { input.focus(); if (select) input.setSelectionRange(select[0], select[1]); else input.select(); }, 30);
  });
}

export function debounce(fn, ms) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; }
