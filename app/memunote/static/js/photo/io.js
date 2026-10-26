

import { api, rawUrl, API } from "../core.js";
import { PhotoDoc, newCanvas, ctx2d, renderVector, loadSvgImage, DEFAULT_TEXT, fontString, wrapText, textBox, shapePath } from "./doc.js";

export const BROWSER_EXT = new Set([".png", ".jpg", ".jpeg", ".jfif", ".pjpeg", ".pjp", ".webp", ".gif", ".bmp", ".ico", ".cur", ".avif", ".apng", ".svg"]);
export const SAVE_FORMATS = {
  png: { ext: ".png", mime: "image/png", browser: true, alpha: true }, jpeg: { ext: ".jpg", mime: "image/jpeg", browser: true, quality: true },
  webp: { ext: ".webp", mime: "image/webp", browser: true, quality: true, alpha: true }, avif: { ext: ".avif", quality: true, alpha: true },
  bmp: { ext: ".bmp" }, tiff: { ext: ".tif", alpha: true }, ico: { ext: ".ico", alpha: true }, gif: { ext: ".gif", alpha: true },
  pdf: { ext: ".pdf" }, tga: { ext: ".tga", alpha: true }, svg: { ext: ".svg", alpha: true, layers: true }, psd: { ext: ".psd", alpha: true, layers: true },
  memuimg: { ext: ".memuimg", alpha: true, layers: true },
};
export function formatOfPath(path) {
  const e = (String(path).match(/\.[^.\\/]+$/) || [""])[0].toLowerCase();
  return { ".jpg": "jpeg", ".jpeg": "jpeg", ".jfif": "jpeg", ".jpe": "jpeg", ".tif": "tiff", ".tiff": "tiff", ".png": "png", ".apng": "png", ".webp": "webp", ".avif": "avif",
    ".bmp": "bmp", ".ico": "ico", ".gif": "gif", ".pdf": "pdf", ".tga": "tga", ".svg": "svg", ".psd": "psd", ".memuimg": "memuimg" }[e] || "";
}
export const extOf = (p) => (String(p).match(/\.[^.\\/]+$/) || [""])[0].toLowerCase();

export function canvasFromImage(img, w = img.width, h = img.height) { const c = newCanvas(w, h); ctx2d(c).drawImage(img, 0, 0, w, h); return c; }
export function loadImage(src) { return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error("image decode failed")); i.src = src; }); }
export async function canvasFromB64(b64, mime = "image/png") { const img = await loadImage(`data:${mime};base64,${b64}`); return canvasFromImage(img); }
export function canvasToBlob(c, mime = "image/png", quality) { return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("encode failed"))), mime, quality)); }
export async function canvasToB64(c) { const b = await canvasToBlob(c); return blobToB64(b); }
export function blobToB64(b) { return new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.readAsDataURL(b); }); }
export function blobToDataURL(b) { return new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.readAsDataURL(b); }); }
const enc = new TextEncoder(), dec = new TextDecoder();

const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
export function crc32(u8) { let c = 0xffffffff; for (let i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
export function zipStore(files) {
  const parts = [], central = []; let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name), crc = crc32(f.data), n = f.data.length;
    const h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint16(8, 0, true);
    h.setUint32(14, crc, true); h.setUint32(18, n, true); h.setUint32(22, n, true); h.setUint16(26, name.length, true);
    parts.push(new Uint8Array(h.buffer), name, f.data);
    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
    c.setUint32(16, crc, true); c.setUint32(20, n, true); c.setUint32(24, n, true); c.setUint16(28, name.length, true); c.setUint32(42, offset, true);
    central.push(new Uint8Array(c.buffer), name);
    offset += 30 + name.length + n;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true); e.setUint32(12, cdSize, true); e.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(e.buffer)], { type: "application/zip" });
}
export async function zipRead(buf) {
  const u8 = new Uint8Array(buf), dv = new DataView(buf);
  let eocd = -1; for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new Error("not a ZIP");
  const count = dv.getUint16(eocd + 10, true); let p = dv.getUint32(eocd + 16, true); const out = {};
  for (let k = 0; k < count; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true), lo = dv.getUint32(p + 42, true);
    const name = dec.decode(u8.subarray(p + 46, p + 46 + nlen));
    const start = lo + 30 + dv.getUint16(lo + 26, true) + dv.getUint16(lo + 28, true), raw = u8.subarray(start, start + csize);
    out[name] = { method, raw };
    p += 46 + nlen + xlen + clen;
  }
  const get = async (name) => {
    const f = out[name]; if (!f) return null;
    if (f.method === 0) return f.raw;
    if (f.method === 8) return new Uint8Array(await new Response(new Blob([f.raw]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).arrayBuffer());
    throw new Error("ZIP method " + f.method);
  };
  return { names: Object.keys(out), get };
}

async function fetchRaw(path) { const r = await fetch(rawUrl(path)); if (!r.ok) throw Object.assign(new Error("HTTP " + r.status), { status: r.status }); return r.blob(); }
async function decodeBrowser(path, ext) {
  const blob = await fetchRaw(path);
  if (ext === ".svg") {
    const source = await blob.text();
    return { svg: source, ...(await svgSize(source)) };
  }
  const bmp = await createImageBitmap(blob);
  const c = canvasFromImage(bmp); bmp.close?.();
  return { canvas: c, width: c.width, height: c.height };
}
export async function svgSize(source) {
  const doc = new DOMParser().parseFromString(source, "image/svg+xml"), el = doc.documentElement;
  if (!el || el.nodeName.toLowerCase() !== "svg") throw new Error("not an SVG");
  const num = (v) => { const m = /^([\d.]+)(px)?$/.exec(String(v || "").trim()); return m ? parseFloat(m[1]) : 0; };
  let w = num(el.getAttribute("width")), h = num(el.getAttribute("height"));
  const vb = (el.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  if ((!w || !h) && vb.length === 4 && vb[2] > 0 && vb[3] > 0) { if (!w && !h) { w = vb[2]; h = vb[3]; } else if (!w) w = (h * vb[2]) / vb[3]; else h = (w * vb[3]) / vb[2]; }
  w = w || 1024; h = h || 1024;
  if (Math.max(w, h) < 512) { const k = 1024 / Math.max(w, h); w *= k; h *= k; }

  el.setAttribute("width", String(Math.round(w))); el.setAttribute("height", String(Math.round(h)));
  if (!el.getAttribute("viewBox") && vb.length !== 4) {}
  return { width: Math.round(w), height: Math.round(h), svgFixed: new XMLSerializer().serializeToString(el) };
}

export async function openDocument(path, t) {
  const ext = extOf(path);
  if (ext === ".memuimg") return { doc: await readMemuimg(await (await fetchRaw(path)).arrayBuffer()), info: { format: "memuimg" } };
  let decoded = null, info = { format: ext.slice(1).toUpperCase(), frames: 1 };
  if (BROWSER_EXT.has(ext)) {
    try { decoded = await decodeBrowser(path, ext); } catch (e) { decoded = null; }
  }
  if (!decoded || ext === ".gif" || ext === ".ico" || ext === ".apng") {

    try {
      const r = await api("photo/decode", { path });
      info = { format: r.format, frames: r.frames || 1, warning: r.warning || "" };
      if (r.layers?.length) return { doc: await docFromPsd(r, t), info };
      if (!decoded || ext === ".ico") decoded = { canvas: await canvasFromB64(r.png), width: r.width, height: r.height };
    } catch (e) {
      if (!decoded) throw e;
    }
  }
  const doc = new PhotoDoc(decoded.width, decoded.height);
  if (decoded.svg) {
    const l = doc.makeLayer({ type: "svg", name: t("ph_layer_vector"), svg: { source: decoded.svgFixed || decoded.svg, x: 0, y: 0, w: decoded.width, h: decoded.height } });
    l.svg.img = await loadSvgImage(l.svg.source); renderVector(doc, l);
    doc.layers.push(l); doc.activeId = l.id; info.format = "SVG";
  } else {
    const l = doc.makeLayer({ name: t("ph_layer_background"), canvas: decoded.canvas });
    doc.layers.push(l); doc.activeId = l.id;
  }
  return { doc, info };
}

async function docFromPsd(r, t) {
  const doc = new PhotoDoc(r.width, r.height);
  const build = async (items) => {
    const out = [];
    for (const it of items) {
      if (it.type === "group") { const g = doc.makeLayer({ type: "group", name: it.name, opacity: it.opacity ?? 1, blend: it.blend || "normal", visible: it.visible !== false, open: it.open !== false }); g.children = await build(it.children || []); out.push(g); continue; }
      const canvas = it.png ? await canvasFromB64(it.png) : newCanvas(1, 1);
      const l = doc.makeLayer({ name: it.name || t("ph_layer"), canvas, x: it.x || 0, y: it.y || 0, opacity: it.opacity ?? 1, blend: it.blend || "normal", visible: it.visible !== false });
      if (it.mask?.png) l.mask = await maskFromPsd(it.mask, l);
      out.push(l);
    }
    return out;
  };
  doc.layers = await build(r.layers);
  if (!doc.layers.length) { const l = doc.makeLayer({ name: t("ph_layer_background"), canvas: await canvasFromB64(r.png) }); doc.layers.push(l); }
  doc.activeId = doc.rasterLayers().pop()?.id || doc.layers[0].id;
  return doc;
}

async function maskFromPsd(m, l) {
  const grey = await canvasFromB64(m.png), w = l.canvas.width, h = l.canvas.height;
  const c = newCanvas(w, h), x = ctx2d(c);
  x.fillStyle = m.default ? "#fff" : "rgba(0,0,0,0)"; x.fillRect(0, 0, w, h);
  x.clearRect(m.x - l.x, m.y - l.y, grey.width, grey.height);
  const g = ctx2d(grey).getImageData(0, 0, grey.width, grey.height), d = g.data;
  for (let i = 0; i < d.length; i += 4) { d[i + 3] = d[i]; d[i] = d[i + 1] = d[i + 2] = 255; }
  const tmp = newCanvas(grey.width, grey.height); ctx2d(tmp).putImageData(g, 0, 0);
  x.drawImage(tmp, m.x - l.x, m.y - l.y);
  return c;
}
function maskToGrey(mask) {
  const d = ctx2d(mask).getImageData(0, 0, mask.width, mask.height);
  for (let i = 0; i < d.data.length; i += 4) { const a = d.data[i + 3]; d.data[i] = d.data[i + 1] = d.data[i + 2] = a; d.data[i + 3] = 255; }
  const c = newCanvas(mask.width, mask.height); ctx2d(c).putImageData(d, 0, 0); return c;
}

export async function framesOf(path) { return api("photo/frames", { path }); }

function layerMeta(l) {
  const m = { id: l.id, name: l.name, type: l.type, x: l.x, y: l.y, opacity: l.opacity, blend: l.blend, visible: l.visible, locked: !!l.locked, maskOn: l.maskOn !== false };
  if (l.text) m.text = { ...l.text };
  if (l.shape) m.shape = { ...l.shape };
  if (l.svg) { const { img, ...rest } = l.svg; m.svg = rest; }
  if (l.type === "group") m.open = l.open !== false;
  return m;
}
export async function writeMemuimg(doc) {
  const files = [];
  const pngBytes = async (c) => new Uint8Array(await (await canvasToBlob(c)).arrayBuffer());
  const walk = async (list) => {
    const out = [];
    for (const l of list) {
      const m = layerMeta(l);
      if (l.type === "group") m.children = await walk(l.children);
      else if (l.type === "raster") { m.src = `layers/${l.id}.png`; files.push({ name: m.src, data: await pngBytes(l.canvas) }); }
      if (l.mask) { m.mask = `masks/${l.id}.png`; files.push({ name: m.mask, data: await pngBytes(l.mask) }); }
      out.push(m);
    }
    return out;
  };
  const layers = await walk(doc.layers);
  const json = { format: "memuimg", version: 1, app: "MemuNote", width: doc.width, height: doc.height, activeId: doc.activeId, guides: doc.guides, layers,
    history: doc.editsSummary().slice(-60) };
  const flat = doc.flatten();
  const thumbK = Math.min(1, 256 / Math.max(doc.width, doc.height)), thumb = newCanvas(doc.width * thumbK, doc.height * thumbK);
  ctx2d(thumb).drawImage(flat, 0, 0, thumb.width, thumb.height);
  return zipStore([{ name: "mimetype", data: enc.encode("application/x-memuimg") }, { name: "document.json", data: enc.encode(JSON.stringify(json, null, 1)) },
    ...files, { name: "preview.png", data: await pngBytes(flat) }, { name: "thumbnail.png", data: await pngBytes(thumb) }]);
}
export async function readMemuimg(buf) {
  const z = await zipRead(buf), raw = await z.get("document.json");
  if (!raw) throw new Error("not a .memuimg document");
  const json = JSON.parse(dec.decode(raw));
  if (json.format !== "memuimg") throw new Error("not a .memuimg document");
  const doc = new PhotoDoc(json.width | 0, json.height | 0);
  const img = async (name) => { const u8 = await z.get(name); if (!u8) return null; const bmp = await createImageBitmap(new Blob([u8], { type: "image/png" })); const c = canvasFromImage(bmp); bmp.close?.(); return c; };
  const build = async (list) => {
    const out = [];
    for (const m of list || []) {
      const l = doc.makeLayer({ ...m, canvas: m.type === "group" ? undefined : (m.src ? await img(m.src) : newCanvas(doc.width, doc.height)) || newCanvas(doc.width, doc.height), children: undefined, src: undefined });
      delete l.src;
      if (m.type === "group") { l.children = await build(m.children); l.open = m.open !== false; }
      if (m.mask) l.mask = await img(m.mask);
      if (l.type === "svg" && l.svg?.source) { try { l.svg.img = await loadSvgImage(l.svg.source); } catch {  } }
      if (l.type !== "raster" && l.type !== "group") renderVector(doc, l);
      out.push(l);
    }
    return out;
  };
  doc.layers = await build(json.layers);
  doc.guides = Array.isArray(json.guides) ? json.guides.filter((g) => g && (g.axis === "x" || g.axis === "y")) : [];
  doc.activeId = doc.find(json.activeId) ? json.activeId : doc.flat().pop()?.id || null;
  return doc;
}

const xmlEsc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export async function writeSvg(doc) {
  let defs = "", n = 0;
  const parts = [];
  const pathD = (s) => {
    const st = `${s.fill && !["line", "arrow"].includes(s.kind) && !(s.kind === "path" && !s.closed) ? `fill="${xmlEsc(s.fill)}"` : 'fill="none"'}${(s.stroke && s.strokeWidth) || ["line", "arrow"].includes(s.kind) ? ` stroke="${xmlEsc(s.stroke || s.fill || "#000")}" stroke-width="${Math.max(1, s.strokeWidth || 4)}" stroke-linecap="round" stroke-linejoin="round"` : ""}`;
    const x = Math.min(s.x, s.x + s.w), y = Math.min(s.y, s.y + s.h), w = Math.abs(s.w), h = Math.abs(s.h);
    if (s.kind === "rect") return `<rect x="${x}" y="${y}" width="${w}" height="${h}"${s.radius ? ` rx="${s.radius}"` : ""} ${st}/>`;
    if (s.kind === "ellipse") return `<ellipse cx="${x + w / 2}" cy="${y + h / 2}" rx="${w / 2}" ry="${h / 2}" ${st}/>`;
    if (s.kind === "line") return `<line x1="${s.x}" y1="${s.y}" x2="${s.x + s.w}" y2="${s.y + s.h}" ${st}/>`;
    if (s.kind === "arrow") { const a = Math.atan2(s.h, s.w), L = Math.max(10, (s.strokeWidth || 4) * 4), ex = s.x + s.w, ey = s.y + s.h; const pts = [-0.45, 0.45].map((d) => `M${ex} ${ey}L${ex - L * Math.cos(a + d)} ${ey - L * Math.sin(a + d)}`).join(""); return `<path d="M${s.x} ${s.y}L${ex} ${ey}${pts}" ${st}/>`; }
    if (s.kind === "polygon" || s.kind === "star") {
      const nn = Math.max(3, s.sides | 0), cx = x + w / 2, cy = y + h / 2, k = s.kind === "star" ? 2 : 1, pts = [];
      for (let i = 0; i < nn * k; i++) { const a = -Math.PI / 2 + (i * Math.PI * 2) / (nn * k), r = s.kind === "star" && i % 2 ? 0.45 : 1; pts.push(`${(cx + Math.cos(a) * (w / 2) * r).toFixed(2)},${(cy + Math.sin(a) * (h / 2) * r).toFixed(2)}`); }
      return `<polygon points="${pts.join(" ")}" ${st}/>`;
    }
    if (s.kind === "path" && s.points?.length) return `<path d="${s.points.map(([px, py], i) => (i ? "L" : "M") + px.toFixed(1) + " " + py.toFixed(1)).join("")}${s.closed ? "Z" : ""}" ${st}/>`;
    return "";
  };
  const emit = async (list) => {
    const out = [];
    for (const l of list) {
      if (!l.visible) continue;
      const style = `${l.opacity < 1 ? ` opacity="${+l.opacity.toFixed(3)}"` : ""}${l.blend && l.blend !== "normal" ? ` style="mix-blend-mode:${l.blend}"` : ""}`;
      let maskAttr = "";
      if (l.mask && l.maskOn !== false) {
        const id = "m" + n++;
        defs += `<mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="${doc.width}" height="${doc.height}"><image x="${l.x}" y="${l.y}" width="${l.mask.width}" height="${l.mask.height}" href="data:image/png;base64,${await canvasToB64(maskToGrey(l.mask))}"/></mask>`;
        maskAttr = ` mask="url(#${id})"`;
      }
      let body = "";
      if (l.type === "group") body = (await emit(l.children)).join("\n");
      else if (l.type === "text") {
        const t = l.text, c = ctx2d(newCanvas(1, 1)); c.font = fontString(t);
        const lines = wrapText(c, t), lh = t.size * t.lineHeight, box = textBox(doc, l);
        const ax = t.align === "center" ? box.x + box.w / 2 : t.align === "right" ? box.x + box.w : box.x, anchor = { left: "start", center: "middle", right: "end" }[t.align] || "start";
        const rot = t.rotate ? ` transform="rotate(${t.rotate} ${box.x + box.w / 2} ${box.y + box.h / 2})"` : "";
        body = `<text font-family="${xmlEsc(t.font)}, sans-serif" font-size="${t.size}" font-weight="${t.bold ? 700 : 400}"${t.italic ? ' font-style="italic"' : ""}${t.underline ? ' text-decoration="underline"' : ""} fill="${xmlEsc(t.color)}" text-anchor="${anchor}" dominant-baseline="hanging"${t.stroke ? ` stroke="${xmlEsc(t.strokeColor)}" stroke-width="${t.stroke}" paint-order="stroke"` : ""}${rot}>`
          + lines.map((s, i) => `<tspan x="${ax}" y="${t.y + i * lh + (lh - t.size) / 2}">${xmlEsc(s)}</tspan>`).join("") + "</text>";
      } else if (l.type === "shape") body = pathD(l.shape);
      else if (l.type === "svg") body = `<image x="${l.svg.x}" y="${l.svg.y}" width="${l.svg.w}" height="${l.svg.h}" href="data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(l.svg.source)))}"/>`;
      else body = `<image x="${l.x}" y="${l.y}" width="${l.canvas.width}" height="${l.canvas.height}" href="data:image/png;base64,${await canvasToB64(l.canvas)}"/>`;
      out.push(`<g id="${xmlEsc(l.id)}" data-name="${xmlEsc(l.name)}"${style}${maskAttr}>${body}</g>`);
    }
    return out;
  };
  parts.push(...(await emit(doc.layers)));
  const svg = `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${doc.width}" height="${doc.height}" viewBox="0 0 ${doc.width} ${doc.height}">\n<!-- MemuNote -->\n${defs ? `<defs>${defs}</defs>\n` : ""}${parts.join("\n")}\n</svg>\n`;
  return new Blob([svg], { type: "image/svg+xml" });
}

async function psdLayers(doc, list) {
  const out = [];
  for (const l of list) {
    const m = { name: l.name, opacity: l.opacity, blend: l.blend, visible: l.visible };
    if (l.type === "group") { m.type = "group"; m.open = l.open !== false; m.children = await psdLayers(doc, l.children); }
    else { m.png = await canvasToB64(l.canvas); m.x = l.x; m.y = l.y; }
    if (l.mask) { m.mask = { png: await canvasToB64(maskToGrey(l.mask)), x: l.type === "group" ? 0 : l.x, y: l.type === "group" ? 0 : l.y, default: 0, disabled: l.maskOn === false }; }
    out.push(m);
  }
  return out;
}

export async function saveDocument(doc, path, fmt, opts = {}) {
  const q = (o) => new URLSearchParams({ path, mtime: String(opts.mtime || 0), force: opts.force ? "1" : "0", ...o }).toString();
  if (fmt === "memuimg" || fmt === "svg") {
    const blob = fmt === "svg" ? await writeSvg(doc) : await writeMemuimg(doc);
    return api("save_binary?" + q({}), blob, { raw: true, type: "application/octet-stream" });
  }
  if (fmt === "psd") {
    return api("photo/psd", { path, width: doc.width, height: doc.height, layers: await psdLayers(doc, doc.layers), merged: await canvasToB64(doc.flatten()), mtime: opts.mtime || 0, force: !!opts.force });
  }
  const spec = SAVE_FORMATS[fmt] || SAVE_FORMATS.png;
  const flat = doc.flatten(spec.alpha ? null : (opts.background || "#ffffff"));
  if (spec.browser && (fmt !== "webp" || !opts.lossless)) {
    const blob = await canvasToBlob(flat, spec.mime, spec.quality ? (opts.quality ?? 90) / 100 : undefined);
    if (blob.type === spec.mime) return api("save_binary?" + q({}), blob, { raw: true, type: "application/octet-stream" });
  }
  const png = await canvasToBlob(flat);
  return api("photo/encode?" + q({ format: fmt, quality: String(opts.quality ?? 90), background: opts.background || "#ffffff", extra: JSON.stringify(opts.extra || {}) }), png, { raw: true, type: "image/png" });
}
export { API };
