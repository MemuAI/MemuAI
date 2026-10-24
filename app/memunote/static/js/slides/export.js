// MemuNote Slides — export: PNG per slide and PDF (slides rasterised through an SVG foreignObject,
// assembled with the vendored pdf-lib). Media are inlined as data: URLs for the rasteriser.
import { api, t, toast, basename, dirname } from "../core.js";
import { renderSlide, fitTexts } from "./render.js";

let CSS_TEXT = null;
async function slideCss() {
  if (CSS_TEXT === null) {
    try { CSS_TEXT = await (await fetch(new URL("../../slides.css", import.meta.url))).text(); } catch { CSS_TEXT = ""; }
    const start = CSS_TEXT.indexOf("/* @render-start */"), end = CSS_TEXT.indexOf("/* @render-end */");
    if (start >= 0 && end > start) CSS_TEXT = CSS_TEXT.slice(start, end);
  }
  return CSS_TEXT;
}
async function toDataUrl(blob) {   // no fetch(blob:) here: connect-src 'self' in the page CSP blocks it
  return new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => res(""); r.readAsDataURL(blob); });
}

/** Render slide i to a canvas `width` px wide. */
export async function slideCanvas(deck, i, width, dataUrls) {
  const s = width / deck.w;
  const saved = deck.urls;
  deck.urls = dataUrls;
  let node;
  try { node = renderSlide(deck, deck.slides[i], { scale: s, slideNo: i + 1 }); } finally { deck.urls = saved; }
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-100000px;top:0;pointer-events:none;contain:layout";
  host.className = "sl-export-host";
  host.append(node); document.body.append(host);
  fitTexts(node);
  const W = Math.round(deck.w * s), H = Math.round(deck.h * s);
  const css = await slideCss();
  node.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  const html = new XMLSerializer().serializeToString(node);
  host.remove();
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><foreignObject x="0" y="0" width="${W}" height="${H}"><div xmlns="http://www.w3.org/1999/xhtml"><style>${css.replace(/<\/style/gi, "")}</style>${html}</div></foreignObject></svg>`;
  const img = await new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error("render failed")); im.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg); });
  const c = document.createElement("canvas"); c.width = W; c.height = H;
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
  g.drawImage(img, 0, 0, W, H);
  return c;
}
export async function mediaDataUrls(deck) {
  await deck.preloadMedia();
  const out = new Map();
  for (const [k, u] of deck.urls) out.set(k, await deck.mediaData(k).then((m) => (m ? toDataUrl(new Blob([m.bytes], { type: m.type })) : "")).catch(() => ""));
  return out;
}
const blobOf = (c, type, q) => new Promise((r) => c.toBlob(r, type, q));

async function savePath(view, ext, label) {
  const stem = (view.tab.name || "presentation").replace(/\.\w+$/, "");
  const path = await view.hooks.saveDialog?.({ filename: stem + "." + ext, filters: [`${label} (*.${ext})`] });
  if (!path) return null;
  return /\.\w+$/.test(basename(path)) ? path : path + "." + ext;
}
async function write(path, bytes, type) {
  const q = new URLSearchParams({ path, force: "1" });
  const r = await api("save_binary?" + q, new Blob([bytes], { type }), { raw: true, type: "application/octet-stream" });
  if (!r.ok) throw new Error(r.error || "write failed");
  return r;
}

function progress(text) {
  const el = document.createElement("div"); el.className = "loading-overlay"; el.innerHTML = `<div class="spinner"></div><span></span>`;
  el.querySelector("span").textContent = text; document.body.append(el);
  return { set: (s) => { el.querySelector("span").textContent = s; }, done: () => el.remove() };
}

export async function exportPdf(view) {
  const path = await savePath(view, "pdf", "PDF"); if (!path) return;
  const deck = view.deck, p = progress(t("sl_exporting"));
  try {
    const { PDFDocument } = await import("../../vendor/pdflib/pdflib.js");
    const doc = await PDFDocument.create();
    doc.setTitle(view.tab.name.replace(/\.\w+$/, "")); doc.setCreator("MemuNote"); doc.setProducer("MemuNote");
    const urls = await mediaDataUrls(deck);
    const list = deck.slides.map((s, i) => i).filter((i) => !deck.slides[i].hidden);
    const pw = deck.w / 12700, ph = deck.h / 12700;
    for (const [k, i] of list.entries()) {
      p.set(t("sl_exporting_n", { n: k + 1, total: list.length }));
      const c = await slideCanvas(deck, i, 1920, urls);
      const img = await doc.embedJpg(new Uint8Array(await (await blobOf(c, "image/jpeg", 0.92)).arrayBuffer()));
      const page = doc.addPage([pw, ph]);
      page.drawImage(img, { x: 0, y: 0, width: pw, height: ph });
    }
    await write(path, await doc.save(), "application/pdf");
    toast(t("sl_exported", { name: basename(path) }), { kind: "ok" });
  } finally { p.done(); }
}
export async function exportPng(view, all) {
  const path = await savePath(view, "png", "PNG"); if (!path) return;
  const deck = view.deck, p = progress(t("sl_exporting"));
  try {
    const urls = await mediaDataUrls(deck);
    const list = all ? deck.slides.map((s, i) => i).filter((i) => !deck.slides[i].hidden) : [view.cur];
    const stem = path.replace(/\.png$/i, ""), sep = path.includes("\\") ? "\\" : "/";
    for (const [k, i] of list.entries()) {
      p.set(t("sl_exporting_n", { n: k + 1, total: list.length }));
      const c = await slideCanvas(deck, i, 1920, urls);
      const out = list.length > 1 ? `${stem}-${String(i + 1).padStart(String(deck.slides.length).length, "0")}.png` : path;
      await write(out, new Uint8Array(await (await blobOf(c, "image/png")).arrayBuffer()), "image/png");
    }
    toast(list.length > 1 ? t("sl_exported_n", { n: list.length, dir: dirname(path).split(sep).pop() }) : t("sl_exported", { name: basename(path) }), { kind: "ok" });
  } finally { p.done(); }
}
