

import { ZipArchive } from "./zip.js";
import { newDeck, uid } from "./pptx.js";
import { THEMES } from "./templates.js";

const NS = {
  draw: "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0", svg: "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
  text: "urn:oasis:names:tc:opendocument:xmlns:text:1.0", style: "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
  fo: "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0", pres: "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
  xlink: "http://www.w3.org/1999/xlink", table: "urn:oasis:names:tc:opendocument:xmlns:table:1.0", office: "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
};
const A = (el, ns, name) => el?.getAttributeNS(NS[ns], name) ?? null;
function len(v) {
  const m = /^(-?[\d.]+)\s*(cm|mm|in|pt|pc|px)?$/.exec(String(v || "").trim());
  if (!m) return 0;
  const n = parseFloat(m[1]);
  return Math.round(n * ({ cm: 360000, mm: 36000, in: 914400, pt: 12700, pc: 152400, px: 9525 }[m[2] || "cm"]));
}
const kids = (el) => Array.from(el?.children || []);

function readStyles(doc, map) {
  for (const st of doc.getElementsByTagNameNS(NS.style, "style")) {
    const name = A(st, "style", "name"); if (!name) continue;
    const s = { parent: A(st, "style", "parent-style-name") };
    const g = st.getElementsByTagNameNS(NS.style, "graphic-properties")[0];
    if (g) {
      const fill = A(g, "draw", "fill"); if (fill === "none") s.fill = null; else if (A(g, "draw", "fill-color") && fill !== "none") s.fill = A(g, "draw", "fill-color");
      const stroke = A(g, "draw", "stroke"); if (stroke === "none") s.stroke = null; else if (A(g, "svg", "stroke-color")) s.stroke = A(g, "svg", "stroke-color");
      if (A(g, "svg", "stroke-width")) s.strokeW = len(A(g, "svg", "stroke-width"));
      if (A(g, "draw", "textarea-vertical-align")) s.anchor = { top: "t", middle: "ctr", bottom: "b" }[A(g, "draw", "textarea-vertical-align")];
      if (A(g, "draw", "marker-end")) s.tail = "triangle";
      if (A(g, "draw", "marker-start")) s.head = "triangle";
    }
    const tp = st.getElementsByTagNameNS(NS.style, "text-properties")[0];
    if (tp) {
      if (A(tp, "fo", "font-size")) { const v = A(tp, "fo", "font-size"); if (/pt$/.test(v)) s.sz = parseFloat(v); }
      if (A(tp, "fo", "font-weight")) s.b = A(tp, "fo", "font-weight") === "bold" || +A(tp, "fo", "font-weight") >= 600;
      if (A(tp, "fo", "font-style")) s.i = A(tp, "fo", "font-style") === "italic";
      if (A(tp, "style", "text-underline-style")) s.u = A(tp, "style", "text-underline-style") !== "none";
      if (A(tp, "style", "text-line-through-style")) s.s = A(tp, "style", "text-line-through-style") !== "none";
      if (A(tp, "fo", "color")) s.color = A(tp, "fo", "color").toUpperCase();
      if (A(tp, "style", "font-name") || A(tp, "fo", "font-family")) s.font = (A(tp, "fo", "font-family") || A(tp, "style", "font-name")).replace(/['"]/g, "");
    }
    const pp = st.getElementsByTagNameNS(NS.style, "paragraph-properties")[0];
    if (pp && A(pp, "fo", "text-align")) s.algn = { start: "l", left: "l", center: "ctr", end: "r", right: "r", justify: "just" }[A(pp, "fo", "text-align")];
    const dp = st.getElementsByTagNameNS(NS.style, "drawing-page-properties")[0];
    if (dp) { if (A(dp, "draw", "fill") !== "none" && A(dp, "draw", "fill-color")) s.bg = A(dp, "draw", "fill-color").toUpperCase(); }
    map.set(name, s);
  }
}
function style(map, name, key) { let s = map.get(name), n = 0; while (s && n++ < 12) { if (s[key] !== undefined) return s[key]; s = map.get(s.parent); } return undefined; }
const runKeys = ["sz", "b", "i", "u", "s", "color", "font"];
function runProps(map, name) { const o = {}; for (const k of runKeys) { const v = style(map, name, k); if (v !== undefined) o[k] = v; } return o; }

function readParas(container, styles, level = 0, base = {}) {
  const paras = [];
  for (const el of kids(container)) {
    if (el.localName === "p" || el.localName === "h") {
      const pName = A(el, "text", "style-name");
      const pBase = { ...base, ...runProps(styles, pName) };
      const para = { runs: [] };
      const algn = style(styles, pName, "algn"); if (algn) para.algn = algn;
      if (level) { para.lvl = level - 1 || undefined; para.bullet = { char: "•" }; para.marL = 342900 + (level - 1) * 342900; para.indent = -342900; if (!para.lvl) delete para.lvl; }
      const walk = (node, props) => {
        for (const n of node.childNodes) {
          if (n.nodeType === 3) { if (n.nodeValue) para.runs.push({ ...props, t: n.nodeValue }); continue; }
          if (n.nodeType !== 1) continue;
          if (n.localName === "span") walk(n, { ...props, ...runProps(styles, A(n, "text", "style-name")) });
          else if (n.localName === "s") para.runs.push({ ...props, t: " ".repeat(+A(n, "text", "c") || 1) });
          else if (n.localName === "tab") para.runs.push({ ...props, t: "\t" });
          else if (n.localName === "line-break") para.runs.push({ br: true, t: "\n" });
          else if (n.localName === "a") walk(n, props);
          else if (n.localName === "page-number") para.runs.push({ ...props, t: n.textContent || "#", fld: "slidenum" });
          else walk(n, props);
        }
      };
      walk(el, pBase);
      for (const r of para.runs) for (const k of Object.keys(r)) if (r[k] === undefined) delete r[k];
      paras.push(para);
    } else if (el.localName === "list") {
      for (const item of kids(el)) if (item.localName === "list-item" || item.localName === "list-header") paras.push(...readParas(item, styles, level + 1, base));
    }
  }
  return paras;
}

export async function openOdp(buffer, opts = {}) {
  const zip = await ZipArchive.read(buffer);
  const content = new DOMParser().parseFromString(await zip.text("content.xml"), "application/xml");
  const stylesDoc = zip.has("styles.xml") ? new DOMParser().parseFromString(await zip.text("styles.xml"), "application/xml") : null;
  const styles = new Map();
  if (stylesDoc) readStyles(stylesDoc, styles);
  readStyles(content, styles);
  let w = 0, h = 0;
  for (const pl of stylesDoc?.getElementsByTagNameNS(NS.style, "page-layout-properties") || []) { w = len(A(pl, "fo", "page-width")); h = len(A(pl, "fo", "page-height")); if (w && h) break; }
  if (!w || !h) { w = 12192000; h = 6858000; }
  const deck = await newDeck({ theme: THEMES[1], size: Math.abs(w / h - 4 / 3) < 0.05 ? "4:3" : "16:9", lang: opts.lang });
  deck.w = w; deck.h = h; deck._sizeChanged = true;
  deck.format = "odp";
  const blank = deck.layouts.find((l) => l.type === "blank") || deck.layouts[deck.layouts.length - 1];
  const media = new Map();
  const mediaKey = async (href) => {
    if (!href || /^https?:/i.test(href)) return null;
    const p = href.replace(/^\.\//, "");
    if (media.has(p)) return media.get(p);
    if (!zip.has(p)) return null;
    const bytes = await zip.bytes(p);
    const ext = p.split(".").pop().toLowerCase();
    const type = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", bmp: "image/bmp", svg: "image/svg+xml" }[ext];
    if (!type || type === "image/svg+xml") { media.set(p, null); return null; }
    const key = deck.addMedia(bytes, type); media.set(p, key); return key;
  };
  const slides = [];
  const pages = content.getElementsByTagNameNS(NS.draw, "page");
  for (const page of pages) {
    const s = { id: uid(), path: null, layout: blank.path, elements: [], notes: "", _new: true };
    const bg = style(styles, A(page, "draw", "style-name"), "bg"); if (bg) s.bg = { color: bg };
    let n = 2;
    const walkShapes = async (parent, into) => {
      for (const el of kids(parent)) {
        const ln = el.localName;
        if (ln === "notes") { for (const tb of el.getElementsByTagNameNS(NS.draw, "text-box")) s.notes = readParas(tb, styles).map((p) => p.runs.map((r) => r.t).join("")).join("\n").trim(); continue; }
        if (!["frame", "custom-shape", "rect", "ellipse", "line", "g", "connector"].includes(ln)) continue;
        const sName = A(el, "draw", "style-name") || A(el, "pres", "style-name");
        let x = len(A(el, "svg", "x")), y = len(A(el, "svg", "y")), ww = len(A(el, "svg", "width")), hh = len(A(el, "svg", "height")), rot = 0;
        const tr = A(el, "draw", "transform");
        if (tr) { const r = /rotate\s*\(\s*(-?[\d.]+)/.exec(tr), tt = /translate\s*\(\s*(-?[\d.]+\w*)\s*[, ]\s*(-?[\d.]+\w*)/.exec(tr); if (r) rot = ((-parseFloat(r[1]) * 180 / Math.PI) % 360 + 360) % 360; if (tt) { x = len(tt[1]); y = len(tt[2]); } if (r) { const th = -parseFloat(r[1]); const cx = x + (ww / 2) * Math.cos(th) - (hh / 2) * Math.sin(th), cy = y + (ww / 2) * Math.sin(th) + (hh / 2) * Math.cos(th); x = Math.round(cx - ww / 2); y = Math.round(cy - hh / 2); } }
        const base = { id: uid(), nvId: n++, name: A(el, "draw", "name") || `${ln} ${n}`, x, y, w: ww, h: hh, rot: Math.round(rot * 100) / 100 };
        if (ln === "g") { await walkShapes(el, into); continue; }
        if (ln === "line" || ln === "connector") {
          const x1 = len(A(el, "svg", "x1")), y1 = len(A(el, "svg", "y1")), x2 = len(A(el, "svg", "x2")), y2 = len(A(el, "svg", "y2"));
          into.push({ ...base, kind: "shape", geom: "line", x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1), flipH: x2 < x1 || undefined, flipV: y2 < y1 || undefined,
            line: { w: style(styles, sName, "strokeW") || 19050, color: style(styles, sName, "stroke") || "#000000", ...(style(styles, sName, "tail") ? { tail: "triangle" } : {}), ...(style(styles, sName, "head") ? { head: "triangle" } : {}) } });
          continue;
        }
        const cls = A(el, "pres", "class");
        const img = el.getElementsByTagNameNS(NS.draw, "image")[0];
        const tbl = el.getElementsByTagNameNS(NS.table, "table")[0];
        const tb = ln === "frame" ? el.getElementsByTagNameNS(NS.draw, "text-box")[0] : el;
        if (ln === "frame" && img && !tb?.textContent.trim()) {
          const key = await mediaKey(A(img, "xlink", "href"));
          if (key) into.push({ ...base, kind: "pic", pic: { media: key } });
          continue;
        }
        if (tbl) {
          const rows = Array.from(tbl.getElementsByTagNameNS(NS.table, "table-row")).map((r) => Array.from(r.getElementsByTagNameNS(NS.table, "table-cell")).map((c) => c.textContent.trim()));
          const cols = Math.max(1, ...rows.map((r) => r.length));
          into.push({ ...base, kind: "table", table: { cols: Array.from({ length: cols }, () => Math.round(ww / cols)), firstRow: true, bandRow: true, style: "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}", rows: rows.map((r) => ({ h: Math.round(hh / Math.max(1, rows.length)), cells: Array.from({ length: cols }, (_, j) => ({ text: { paras: [{ runs: [{ t: r[j] || "" }] }] } })) })) } });
          continue;
        }
        let paras = tb ? readParas(tb, styles) : [];
        const dflt = cls === "title" ? { sz: 40 } : cls === "subtitle" ? { sz: 24, algn: "ctr" } : cls === "outline" ? { sz: 24 } : {};
        if (cls === "outline") for (const p of paras) if (!p.bullet) { p.bullet = { char: "•" }; p.marL = p.marL || 342900; p.indent = -342900; }
        for (const p of paras) { if (dflt.algn && !p.algn) p.algn = dflt.algn; for (const r of p.runs) if (dflt.sz && r.sz === undefined && !r.br) r.sz = dflt.sz; }
        if (!paras.length) paras = [{ runs: [{ t: "" }] }];
        const isText = ln === "frame";
        const geomType = ln === "ellipse" ? "ellipse" : ln === "rect" ? "rect" : ln === "custom-shape" ? ({ ellipse: "ellipse", rectangle: "rect", "round-rectangle": "roundRect", diamond: "diamond", "isosceles-triangle": "triangle", "right-triangle": "rtTriangle", pentagon: "pentagon", hexagon: "hexagon", octagon: "octagon", star5: "star5", "right-arrow": "rightArrow", "left-arrow": "leftArrow", "up-arrow": "upArrow", "down-arrow": "downArrow", heart: "heart", cloud: "cloud", parallelogram: "parallelogram", trapezoid: "trapezoid" }[A(el.getElementsByTagNameNS(NS.draw, "enhanced-geometry")[0], "draw", "type")] || "rect") : "rect";
        const fill = style(styles, sName, "fill"), stroke = style(styles, sName, "stroke");
        into.push({ ...base, kind: "shape", geom: geomType, txBox: isText || undefined,
          fill: isText ? (fill ? { color: fill.toUpperCase() } : null) : fill === null ? null : { color: (fill || "#729FCF").toUpperCase() },
          line: isText ? { none: true } : stroke === null ? { none: true } : { color: (stroke || "#3465A4").toUpperCase(), w: style(styles, sName, "strokeW") || 12700 },
          text: { paras, anchor: style(styles, sName, "anchor") || (cls === "title" ? "ctr" : isText ? "t" : "ctr"), ...(cls === "title" || cls === "subtitle" || cls === "outline" ? { autofit: { norm: true } } : {}) } });
      }
    };
    await walkShapes(page, s.elements);
    slides.push(s);
  }
  if (slides.length) deck.slides = slides;
  deck.isNew = true;
  return deck;
}
