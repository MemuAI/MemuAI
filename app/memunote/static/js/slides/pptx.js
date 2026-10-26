

import { ZipArchive } from "./zip.js";
import { chartWorkbook, excelColumn } from "./chart-workbook.js";
import { NS, REL, CT, XML_HEAD, parseXml, serialize, frag, kids, kid, path, deep, deepAll, attr, num, bool, rid, escXml,
  insertOrdered, removeKids, readColor, colorXml, cssColor, DEFAULT_CLRMAP, SCHEME_KEYS } from "./xml.js";
import { newPackage, notesMasterXml, NOTES_MASTER_RELS, themeXml, emptySlideXml, LAYOUTS } from "./templates.js";

export const uid = () => Math.random().toString(36).slice(2, 9) + (Date.now() % 1e6).toString(36);
const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const MODEL_FIELDS = ["kind", "name", "x", "y", "w", "h", "rot", "flipH", "flipV", "xfrmInherited", "geom", "adj", "fill", "line", "text", "pic", "table", "chart", "children", "hidden", "descr"];

export function sigOf(el) { return JSON.stringify(el, (k, v) => (k.startsWith("_") || k === "id" || k === "anim" ? undefined : v)); }

function dirOf(p) { const i = p.lastIndexOf("/"); return i < 0 ? "" : p.slice(0, i); }
function relsPathOf(p) { return `${dirOf(p) ? dirOf(p) + "/" : ""}_rels/${p.split("/").pop()}.rels`; }
function resolve(base, target) {
  if (target.startsWith("/")) return target.slice(1);
  const parts = (dirOf(base) ? dirOf(base).split("/") : []);
  for (const seg of target.split("/")) { if (seg === "..") parts.pop(); else if (seg && seg !== ".") parts.push(seg); }
  return parts.join("/");
}
function relative(from, to) {
  const a = dirOf(from).split("/").filter(Boolean), b = to.split("/");
  let i = 0; while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return "../".repeat(a.length - i) + b.slice(i).join("/");
}
class Rels {
  constructor(part, xml) {
    this.part = part; this.items = []; this.orig = xml || null; this.dirty = !xml;
    if (xml) for (const r of kids(parseXml(xml).documentElement, "Relationship"))
      this.items.push({ id: attr(r, "Id"), type: attr(r, "Type"), target: attr(r, "Target"), external: attr(r, "TargetMode") === "External" });
  }
  byId(id) { return this.items.find((r) => r.id === id); }
  path(id) { const r = this.byId(id); return r && !r.external ? resolve(this.part, r.target) : null; }
  ofType(type) { return this.items.filter((r) => r.type === type); }
  add(type, targetPath, external = false) {
    const existing = this.items.find((r) => r.type === type && (external ? r.target === targetPath : resolve(this.part, r.target) === targetPath));
    if (existing) return existing.id;
    let n = 1; while (this.items.some((r) => r.id === "rId" + n)) n++;
    this.items.push({ id: "rId" + n, type, target: external ? targetPath : relative(this.part, targetPath), external });
    this.dirty = true;
    return "rId" + n;
  }
  remove(id) { this.items = this.items.filter((r) => r.id !== id); this.dirty = true; }
  retarget(item, target) { item.target = target; this.dirty = true; }
  xml() {
    if (!this.dirty && this.orig) return this.orig;
    return XML_HEAD + `<Relationships xmlns="${NS.pr}">` + this.items.map((r) => `<Relationship Id="${escXml(r.id)}" Type="${escXml(r.type)}" Target="${escXml(r.target)}"${r.external ? ' TargetMode="External"' : ""}/>`).join("") + "</Relationships>";
  }
}

function readLvl(pPr) {
  if (!pPr) return null;
  const p = {}, r = {};
  if (pPr.hasAttribute("algn")) p.algn = attr(pPr, "algn");
  if (pPr.hasAttribute("marL")) p.marL = num(pPr, "marL");
  if (pPr.hasAttribute("indent")) p.indent = num(pPr, "indent");
  const ln = kid(pPr, "lnSpc"); if (ln) p.lnSpc = kid(ln, "spcPct") ? num(kid(ln, "spcPct"), "val", 100000) / 1000 : { pts: num(kid(ln, "spcPts"), "val", 0) / 100 };
  const sb = kid(pPr, "spcBef"); if (sb) p.spcBef = kid(sb, "spcPts") ? num(kid(sb, "spcPts"), "val", 0) / 100 : { pct: num(kid(sb, "spcPct"), "val", 0) / 1000 };
  const sa = kid(pPr, "spcAft"); if (sa) p.spcAft = kid(sa, "spcPts") ? num(kid(sa, "spcPts"), "val", 0) / 100 : { pct: num(kid(sa, "spcPct"), "val", 0) / 1000 };
  if (kid(pPr, "buNone")) p.bullet = "none";
  else if (kid(pPr, "buChar")) p.bullet = { char: attr(kid(pPr, "buChar"), "char", "•") };
  else if (kid(pPr, "buAutoNum")) p.bullet = { num: attr(kid(pPr, "buAutoNum"), "type", "arabicPeriod"), start: num(kid(pPr, "buAutoNum"), "startAt", 1) };
  if (kid(pPr, "buClr")) p.buClr = readColor(kid(pPr, "buClr"));
  if (kid(pPr, "buSzPct")) p.buSz = num(kid(pPr, "buSzPct"), "val", 100000) / 1000;
  const d = kid(pPr, "defRPr"); if (d) Object.assign(r, readRPr(d));
  return { p, r };
}
function readRPr(rPr) {
  const r = {};
  if (!rPr) return r;
  if (rPr.hasAttribute("b")) r.b = bool(rPr, "b");
  if (rPr.hasAttribute("i")) r.i = bool(rPr, "i");
  if (rPr.hasAttribute("u")) r.u = attr(rPr, "u") !== "none";
  if (rPr.hasAttribute("strike")) r.s = attr(rPr, "strike") !== "noStrike";
  if (rPr.hasAttribute("sz")) r.sz = num(rPr, "sz") / 100;
  if (rPr.hasAttribute("baseline")) { const b = num(rPr, "baseline", 0); if (b) r.base = b > 0 ? "sup" : "sub"; }
  if (rPr.hasAttribute("cap") && attr(rPr, "cap") !== "none") r.cap = attr(rPr, "cap");
  if (rPr.hasAttribute("spc")) r.spc = num(rPr, "spc") / 100;
  const f = kid(rPr, "solidFill"); if (f) r.color = readColor(f);
  else if (kid(rPr, "gradFill")) { const gs = deep(rPr, "gs"); if (gs) r.color = readColor(gs); }
  const hl = kid(rPr, "highlight"); if (hl) r.hl = readColor(hl);
  const lat = kid(rPr, "latin"); if (lat && attr(lat, "typeface")) r.font = attr(lat, "typeface");
  return r;
}
function readParas(txBody, rels) {
  const paras = [];
  for (const p of kids(txBody, "p")) {
    const pPr = kid(p, "pPr");
    const lv = readLvl(pPr);
    const para = { ...(lv?.p || {}), runs: [] };
    if (pPr?.hasAttribute("lvl")) para.lvl = num(pPr, "lvl", 0);
    if (pPr) para._x = serialize(pPr);
    for (const c of p.children) {
      if (c.localName === "r" || c.localName === "fld") {
        const rPr = kid(c, "rPr");
        const run = { t: kid(c, "t")?.textContent ?? "", ...readRPr(rPr) };
        if (c.localName === "fld") { run.fld = attr(c, "type", ""); run._fid = attr(c, "id", ""); }
        const link = rPr && kid(rPr, "hlinkClick"); if (link) { const rr = rels?.byId(rid(link, "id")); if (rr?.external) run.href = rr.target; }
        if (rPr) run._x = serialize(rPr);
        para.runs.push(run);
      } else if (c.localName === "br") para.runs.push({ br: true, t: "\n", ...(kid(c, "rPr") ? { _x: serialize(kid(c, "rPr")) } : {}) });
      else if (c.localName === "endParaRPr") { const e = readRPr(c); if (e.sz) para.endSz = e.sz; para._end = serialize(c); }
    }
    paras.push(para);
  }
  return paras;
}
function readText(txBody, rels) {
  if (!txBody) return null;
  const bp = kid(txBody, "bodyPr");
  const t = { paras: readParas(txBody, rels) };
  if (bp) {
    if (bp.hasAttribute("anchor")) t.anchor = attr(bp, "anchor");
    for (const k of ["lIns", "tIns", "rIns", "bIns"]) if (bp.hasAttribute(k)) t[k] = num(bp, k);
    if (attr(bp, "wrap") === "none") t.wrap = "none";
    if (bp.hasAttribute("vert") && attr(bp, "vert") !== "horz") t.vert = attr(bp, "vert");
    const na = kid(bp, "normAutofit"); if (na) t.autofit = { norm: true, scale: num(na, "fontScale", 100000) / 1000, lnRed: num(na, "lnSpcReduction", 0) / 1000 };
    if (kid(bp, "spAutoFit")) t.autofit = { shape: true };
  }
  const lst = kid(txBody, "lstStyle");
  if (lst && lst.children.length) t._lst = readLstStyle(lst);
  return t;
}
function readLstStyle(el) {
  const out = [];
  if (!el) return out;
  for (let i = 1; i <= 9; i++) { const l = kid(el, `lvl${i}pPr`); out[i - 1] = l ? readLvl(l) : null; }
  return out;
}
export function plainText(text) { return (text?.paras || []).map((p) => p.runs.map((r) => (r.br ? "\n" : r.t)).join("")).join("\n"); }

function rPrXml(doc, run, base) {
  const el = base ? frag(doc, base) : frag(doc, '<a:rPr lang="ru-RU" dirty="0"/>');
  const setB = (a, v, on = "1", off = "0") => { if (v === undefined) el.removeAttribute(a); else el.setAttribute(a, v ? on : off); };
  setB("b", run.b); setB("i", run.i);
  if (run.u === undefined) el.removeAttribute("u"); else el.setAttribute("u", run.u ? "sng" : "none");
  if (run.s === undefined) el.removeAttribute("strike"); else el.setAttribute("strike", run.s ? "sngStrike" : "noStrike");
  if (run.sz === undefined) el.removeAttribute("sz"); else el.setAttribute("sz", String(Math.round(run.sz * 100)));
  if (!run.base) el.removeAttribute("baseline"); else el.setAttribute("baseline", run.base === "sup" ? "30000" : "-25000");
  if (!run.cap) el.removeAttribute("cap"); else el.setAttribute("cap", run.cap);
  if (run.spc === undefined) el.removeAttribute("spc"); else el.setAttribute("spc", String(Math.round(run.spc * 100)));
  const ORDER = ["ln", "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill", "effectLst", "effectDag", "highlight", "uLnTx", "uLn", "uFillTx", "uFill", "latin", "ea", "cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst"];
  const oldColor = readRPr(el).color;
  if (!same(oldColor, run.color)) {
    removeKids(el, ["noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"]);
    if (run.color) insertOrdered(el, frag(doc, `<a:solidFill>${colorXml(run.color)}</a:solidFill>`), ORDER);
  }
  const oldHl = kid(el, "highlight") ? readColor(kid(el, "highlight")) : undefined;
  if (!same(oldHl, run.hl)) { removeKids(el, ["highlight"]); if (run.hl) insertOrdered(el, frag(doc, `<a:highlight>${colorXml(run.hl)}</a:highlight>`), ORDER); }
  const lat = kid(el, "latin");
  if ((lat ? attr(lat, "typeface") : undefined) !== run.font) {
    removeKids(el, ["latin", "ea", "cs"]);
    if (run.font) { insertOrdered(el, frag(doc, `<a:latin typeface="${escXml(run.font)}"/>`), ORDER); insertOrdered(el, frag(doc, `<a:cs typeface="${escXml(run.font)}"/>`), ORDER); }
  }
  return el;
}
function pPrXml(doc, para) {
  const el = para._x ? frag(doc, para._x) : frag(doc, "<a:pPr/>");
  const set = (a, v) => { if (v === undefined || v === null) el.removeAttribute(a); else el.setAttribute(a, String(v)); };
  set("algn", para.algn); set("marL", para.marL); set("indent", para.indent); set("lvl", para.lvl || undefined);
  const ORDER = ["lnSpc", "spcBef", "spcAft", "buClrTx", "buClr", "buSzTx", "buSzPct", "buSzPts", "buFontTx", "buFont", "buNone", "buAutoNum", "buChar", "buBlip", "tabLst", "defRPr", "extLst"];
  const old = readLvl(el)?.p || {};
  const spc = (v) => (typeof v === "number" ? `<a:spcPts val="${Math.round(v * 100)}"/>` : v?.pct !== undefined ? `<a:spcPct val="${Math.round(v.pct * 1000)}"/>` : `<a:spcPts val="${Math.round((v?.pts || 0) * 100)}"/>`);
  if (!same(old.lnSpc, para.lnSpc)) { removeKids(el, ["lnSpc"]); if (para.lnSpc !== undefined) insertOrdered(el, frag(doc, `<a:lnSpc>${typeof para.lnSpc === "number" ? `<a:spcPct val="${Math.round(para.lnSpc * 1000)}"/>` : spc(para.lnSpc)}</a:lnSpc>`), ORDER); }
  if (!same(old.spcBef, para.spcBef)) { removeKids(el, ["spcBef"]); if (para.spcBef !== undefined) insertOrdered(el, frag(doc, `<a:spcBef>${spc(para.spcBef)}</a:spcBef>`), ORDER); }
  if (!same(old.spcAft, para.spcAft)) { removeKids(el, ["spcAft"]); if (para.spcAft !== undefined) insertOrdered(el, frag(doc, `<a:spcAft>${spc(para.spcAft)}</a:spcAft>`), ORDER); }
  if (!same(old.bullet, para.bullet)) {
    removeKids(el, ["buNone", "buAutoNum", "buChar", "buBlip", ...(para.bullet === undefined || para.bullet === "none" ? ["buFont", "buClr", "buSzPct"] : [])]);
    if (para.bullet === "none") insertOrdered(el, frag(doc, "<a:buNone/>"), ORDER);
    else if (para.bullet?.char) {
      if (!kid(el, "buFont")) insertOrdered(el, frag(doc, '<a:buFont typeface="Arial" panose="020B0604020202020204" pitchFamily="34" charset="0"/>'), ORDER);
      insertOrdered(el, frag(doc, `<a:buChar char="${escXml(para.bullet.char)}"/>`), ORDER);
    } else if (para.bullet?.num) {
      removeKids(el, ["buFont"]);
      insertOrdered(el, frag(doc, `<a:buFont typeface="+mj-lt"/>`), ORDER);
      insertOrdered(el, frag(doc, `<a:buAutoNum type="${escXml(para.bullet.num)}"${para.bullet.start > 1 ? ` startAt="${para.bullet.start}"` : ""}/>`), ORDER);
    }
    if (para.bullet && para.bullet !== "none" && (para.marL === undefined || para.indent === undefined) && !(para.lvl > 0)) {

      if (para.marL === undefined) el.setAttribute("marL", "342900");
      if (para.indent === undefined) el.setAttribute("indent", "-342900");
    }
  }
  return el;
}
function parasXml(doc, paras, parent) {
  for (const para of paras.length ? paras : [{ runs: [] }]) {
    const p = doc.createElementNS(NS.a, "a:p");
    const pPr = pPrXml(doc, para);
    if (pPr.attributes.length || pPr.children.length) p.appendChild(pPr);
    for (const run of para.runs) {
      if (run.br) { const br = doc.createElementNS(NS.a, "a:br"); br.appendChild(rPrXml(doc, run, run._x)); p.appendChild(br); continue; }
      if (run.t === "" && para.runs.length > 1) continue;
      const r = doc.createElementNS(NS.a, run.fld ? "a:fld" : "a:r");
      if (run.fld) { r.setAttribute("id", run._fid || `{${crypto.randomUUID?.().toUpperCase() || "B6F15528-21DE-4FAA-801E-634DDDAF4B2B"}}`); r.setAttribute("type", run.fld); }
      r.appendChild(rPrXml(doc, run, run._x));
      const t = doc.createElementNS(NS.a, "a:t"); t.textContent = run.t; r.appendChild(t);
      p.appendChild(r);
    }
    const end = para._end ? frag(doc, para._end) : frag(doc, '<a:endParaRPr lang="ru-RU" dirty="0"/>');
    if (para.endSz !== undefined) end.setAttribute("sz", String(Math.round(para.endSz * 100)));
    else if (!para._end) { const first = para.runs.find((r) => r.sz); if (first) end.setAttribute("sz", String(Math.round(first.sz * 100))); }
    p.appendChild(end);
    parent.appendChild(p);
  }
}
function writeTextBody(doc, txBody, text, old) {
  let bp = kid(txBody, "bodyPr");
  if (!bp) { bp = frag(doc, "<a:bodyPr/>"); txBody.insertBefore(bp, txBody.firstChild); }
  const set = (a, v) => { if (v === undefined || v === null) bp.removeAttribute(a); else bp.setAttribute(a, String(v)); };
  if (!same(old?.anchor, text.anchor)) set("anchor", text.anchor);
  for (const k of ["lIns", "tIns", "rIns", "bIns"]) if (!same(old?.[k], text[k])) set(k, text[k]);
  if (!same(old?.wrap, text.wrap)) set("wrap", text.wrap === "none" ? "none" : text.wrap === undefined ? undefined : "square");
  if (!same(old?.vert, text.vert)) set("vert", text.vert);
  if (!same(old?.autofit, text.autofit)) {
    removeKids(bp, ["noAutofit", "normAutofit", "spAutoFit"]);
    const ORDER = ["prstTxWarp", "noAutofit", "normAutofit", "spAutoFit", "scene3d", "sp3d", "flatTx", "extLst"];
    if (text.autofit?.norm) insertOrdered(bp, frag(doc, `<a:normAutofit${text.autofit.scale && text.autofit.scale < 100 ? ` fontScale="${Math.round(text.autofit.scale * 1000)}"` : ""}${text.autofit.lnRed ? ` lnSpcReduction="${Math.round(text.autofit.lnRed * 1000)}"` : ""}/>`), ORDER);
    else if (text.autofit?.shape) insertOrdered(bp, frag(doc, "<a:spAutoFit/>"), ORDER);
  }
  if (!kid(txBody, "lstStyle")) bp.after(frag(doc, "<a:lstStyle/>"));
  if (!same(old?.paras, text.paras)) { removeKids(txBody, ["p"]); parasXml(doc, text.paras, txBody); }
}

function readFill(spPr, rels) {
  if (!spPr) return undefined;
  for (const c of spPr.children) {
    if (c.localName === "noFill") return null;
    if (c.localName === "solidFill") return { color: readColor(c) };
    if (c.localName === "gradFill") {
      const stops = deepAll(c, "gs").map((g) => ({ pos: num(g, "pos", 0) / 1000, color: readColor(g) }));
      const lin = kid(c, "lin");
      return { grad: { stops, ang: lin ? num(lin, "ang", 0) / 60000 : 90, path: !!kid(c, "path") } };
    }
    if (c.localName === "blipFill") { const b = kid(c, "blip"); return { image: rels?.path(rid(b)) || null }; }
    if (c.localName === "pattFill") return { color: readColor(kid(c, "fgClr")) || "#888888", patt: true };
    if (c.localName === "grpFill") return { group: true };
  }
  return undefined;
}
function fillXml(fill) {
  if (fill === null) return "<a:noFill/>";
  if (!fill) return "";
  if (fill.grad) return `<a:gradFill rotWithShape="1"><a:gsLst>${fill.grad.stops.map((s) => `<a:gs pos="${Math.round(s.pos * 1000)}">${colorXml(s.color)}</a:gs>`).join("")}</a:gsLst><a:lin ang="${Math.round((fill.grad.ang || 0) * 60000)}" scaled="0"/></a:gradFill>`;
  if (fill.color) return `<a:solidFill>${colorXml(fill.color)}</a:solidFill>`;
  return "";
}
function readLine(spPr) {
  const ln = kid(spPr, "ln");
  if (!ln) return undefined;
  const line = {};
  if (ln.hasAttribute("w")) line.w = num(ln, "w");
  if (kid(ln, "noFill")) return { ...line, none: true };
  const f = kid(ln, "solidFill"); if (f) line.color = readColor(f);
  const d = kid(ln, "prstDash"); if (d && attr(d, "val") !== "solid") line.dash = attr(d, "val");
  const he = kid(ln, "headEnd"), te = kid(ln, "tailEnd");
  if (he && attr(he, "type") && attr(he, "type") !== "none") line.head = attr(he, "type");
  if (te && attr(te, "type") && attr(te, "type") !== "none") line.tail = attr(te, "type");
  return line;
}
function lineXml(doc, line, base) {
  const el = base ? frag(doc, base) : frag(doc, "<a:ln/>");
  if (line.w !== undefined) el.setAttribute("w", String(Math.round(line.w))); else if (!base) el.removeAttribute("w");
  removeKids(el, ["noFill", "solidFill", "gradFill", "pattFill", "prstDash", "custDash", "headEnd", "tailEnd"]);
  const ORDER = ["noFill", "solidFill", "gradFill", "pattFill", "prstDash", "custDash", "round", "bevel", "miter", "headEnd", "tailEnd", "extLst"];
  if (line.none) insertOrdered(el, frag(doc, "<a:noFill/>"), ORDER);
  else if (line.color) insertOrdered(el, frag(doc, `<a:solidFill>${colorXml(line.color)}</a:solidFill>`), ORDER);
  if (line.dash) insertOrdered(el, frag(doc, `<a:prstDash val="${escXml(line.dash)}"/>`), ORDER);
  if (line.head) insertOrdered(el, frag(doc, `<a:headEnd type="${escXml(line.head)}"/>`), ORDER);
  if (line.tail) insertOrdered(el, frag(doc, `<a:tailEnd type="${escXml(line.tail)}"/>`), ORDER);
  return el;
}
const SPPR_ORDER = ["xfrm", "custGeom", "prstGeom", "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill", "ln", "effectLst", "effectDag", "scene3d", "sp3d", "extLst"];

function readCustGeom(cg) {
  const out = [];
  for (const p of deepAll(cg, "path")) {
    const w = num(p, "w", 0), h = num(p, "h", 0);
    let d = "";
    for (const c of p.children) {
      const pts = kids(c, "pt").map((pt) => [num(pt, "x", 0), num(pt, "y", 0)]);
      if (c.localName === "moveTo") d += `M${pts[0]} `;
      else if (c.localName === "lnTo") d += `L${pts[0]} `;
      else if (c.localName === "cubicBezTo") d += `C${pts.join(" ")} `;
      else if (c.localName === "quadBezTo") d += `Q${pts.join(" ")} `;
      else if (c.localName === "close") d += "Z ";
    }
    out.push({ w, h, d: d.trim(), fill: attr(p, "fill") !== "none", stroke: attr(p, "stroke") !== "0" });
  }
  return out;
}

function xfrmOf(node) {
  const n = node.localName;
  if (n === "graphicFrame") return kid(node, "xfrm");
  if (n === "grpSp") return path(node, "grpSpPr", "xfrm");
  return path(node, "spPr", "xfrm");
}
function readXfrm(x, el) {
  if (!x) return false;
  const off = kid(x, "off"), ext = kid(x, "ext");
  el.x = num(off, "x", 0); el.y = num(off, "y", 0); el.w = num(ext, "cx", 0); el.h = num(ext, "cy", 0);
  el.rot = num(x, "rot", 0) / 60000;
  if (bool(x, "flipH")) el.flipH = true;
  if (bool(x, "flipV")) el.flipV = true;
  return true;
}
function nvOf(node) { return Array.from(node.children).find((c) => c.localName.startsWith("nv")); }

export function readElement(node, ctx) {
  const n = node.localName;
  const el = { id: uid(), kind: "raw" };
  const nv = nvOf(node), cNvPr = kid(nv, "cNvPr");
  if (cNvPr) { el.nvId = num(cNvPr, "id", 0); el.name = attr(cNvPr, "name", ""); if (attr(cNvPr, "descr")) el.descr = attr(cNvPr, "descr"); if (bool(cNvPr, "hidden")) el.hidden = true; }
  const phEl = deep(kid(nv, "nvPr"), "ph");
  if (phEl) el.ph = { type: attr(phEl, "type", ""), idx: attr(phEl, "idx", "") };
  if (n === "AlternateContent") {
    const fb = kid(node, "Fallback") || kid(node, "Choice");
    const inner = fb && fb.firstElementChild ? readElement(fb.firstElementChild, ctx) : null;
    Object.assign(el, { kind: "raw", label: inner?.kind || "object", x: inner?.x || 0, y: inner?.y || 0, w: inner?.w || 0, h: inner?.h || 0, rot: inner?.rot || 0 });
    el._preview = inner; el._xml = serialize(node); el.name = inner?.name || "";
    return finish(el);
  }
  const hasX = readXfrm(xfrmOf(node), el);
  if (!hasX && el.ph) el.xfrmInherited = true;
  if (n === "sp" || n === "cxnSp") {
    el.kind = "shape";
    const spPr = kid(node, "spPr");
    const pg = kid(spPr, "prstGeom");
    if (pg) { el.geom = attr(pg, "prst", "rect"); const av = kids(kid(pg, "avLst"), "gd"); if (av.length) el.adj = av.map((g) => attr(g, "fmla", "")); }
    else if (kid(spPr, "custGeom")) { el.geom = "custom"; el._cust = readCustGeom(kid(spPr, "custGeom")); }
    else if (n === "cxnSp") el.geom = "line";
    const fill = readFill(spPr, ctx.rels); if (fill !== undefined) el.fill = fill;
    const line = readLine(spPr); if (line !== undefined) el.line = line;
    const st = kid(node, "style");
    if (st) {
      const fr = kid(st, "fillRef"), lr = kid(st, "lnRef"), fo = kid(st, "fontRef");
      el._st = { fill: fr && num(fr, "idx", 0) > 0 ? readColor(fr) : null, line: lr && num(lr, "idx", 0) > 0 ? readColor(lr) : null,
        lineW: lr ? [0, 9525, 12700, 19050][num(lr, "idx", 0)] || 12700 : 0, font: fo ? readColor(fo) : null };
    }
    if (kid(kid(nv, "cNvSpPr"), "spLocks") || bool(kid(nv, "cNvSpPr"), "txBox")) el.txBox = bool(kid(nv, "cNvSpPr"), "txBox") || undefined;
    const tb = kid(node, "txBody");
    if (tb) el.text = readText(tb, ctx.rels);
  } else if (n === "pic") {
    el.kind = "pic";
    const bf = kid(node, "blipFill"), blip = kid(bf, "blip"), sr = kid(bf, "srcRect");
    el.pic = { media: ctx.rels?.path(rid(blip)) || null };
    if (sr && (sr.attributes.length)) el.pic.crop = { l: num(sr, "l", 0) / 1000, t: num(sr, "t", 0) / 1000, r: num(sr, "r", 0) / 1000, b: num(sr, "b", 0) / 1000 };
    const spPr = kid(node, "spPr");
    const pg = kid(spPr, "prstGeom"); if (pg && attr(pg, "prst") !== "rect") el.geom = attr(pg, "prst");
    const line = readLine(spPr); if (line !== undefined) el.line = line;
    if (el.pic.media && ctx.deck) ctx.deck.media.set(el.pic.media, ctx.deck.media.get(el.pic.media) || { path: el.pic.media });
    if (deep(kid(nv, "nvPr"), "videoFile") || deep(kid(nv, "nvPr"), "audioFile")) el.media = true;
  } else if (n === "graphicFrame") {
    const gd = deep(node, "graphicData"), uri = attr(gd, "uri", "");
    if (uri.endsWith("/table")) { el.kind = "table"; el.table = readTable(deep(gd, "tbl"), ctx); }
    else if (uri.endsWith("/chart")) {
      const cpath = ctx.rels?.path(rid(deep(gd, "chart"), "id"));
      el.kind = "chart"; el._chartPath = cpath; el.chart = ctx.charts?.get(cpath) || { type: "bar", cats: [], series: [], unsupported: true };
    } else { el.kind = "raw"; el.label = uri.includes("diagram") ? "smartart" : uri.includes("ole") ? "ole" : "object"; }
  } else if (n === "grpSp") {
    el.kind = "group";
    const x = path(node, "grpSpPr", "xfrm");
    el._ch = x ? { x: num(kid(x, "chOff"), "x", 0), y: num(kid(x, "chOff"), "y", 0), w: num(kid(x, "chExt"), "cx", 0), h: num(kid(x, "chExt"), "cy", 0) } : { x: 0, y: 0, w: el.w, h: el.h };
    el.children = [];
    for (const c of node.children) if (!["nvGrpSpPr", "grpSpPr", "extLst"].includes(c.localName)) el.children.push(readElement(c, ctx));
  } else {
    el.label = n;
  }
  el._xml = serialize(node);
  return finish(el);
}
function finish(el) { el._o = JSON.parse(sigOf(el)); el._sig = sigOf(el); return el; }

function readTable(tbl, ctx) {
  const t = { cols: kids(kid(tbl, "tblGrid"), "gridCol").map((g) => num(g, "w", 914400)), rows: [] };
  const pr = kid(tbl, "tblPr");
  if (pr) { t.firstRow = bool(pr, "firstRow", false); t.bandRow = bool(pr, "bandRow", false); t.style = kid(pr, "tableStyleId")?.textContent || ""; t._pr = serialize(pr); }
  for (const tr of kids(tbl, "tr")) {
    const row = { h: num(tr, "h", 370840), cells: [] };
    for (const tc of kids(tr, "tc")) {
      const cell = { text: readText(kid(tc, "txBody"), ctx.rels) || { paras: [{ runs: [] }] } };
      delete cell.text._lst;
      for (const k of ["gridSpan", "rowSpan"]) if (tc.hasAttribute(k)) cell[k] = num(tc, k, 1);
      if (bool(tc, "hMerge")) cell.hMerge = true;
      if (bool(tc, "vMerge")) cell.vMerge = true;
      const tcPr = kid(tc, "tcPr");
      if (tcPr) { const f = readFill(tcPr, null); if (f !== undefined) cell.fill = f; if (tcPr.hasAttribute("anchor")) cell.anchor = attr(tcPr, "anchor"); cell._x = serialize(tcPr); }
      row.cells.push(cell);
    }
    t.rows.push(row);
  }
  return t;
}
function tableXml(doc, t) {
  const tbl = frag(doc, "<a:tbl/>");
  const pr = t._pr ? frag(doc, t._pr) : frag(doc, '<a:tblPr><a:tableStyleId>{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}</a:tableStyleId></a:tblPr>');
  pr.setAttribute("firstRow", t.firstRow ? "1" : "0"); pr.setAttribute("bandRow", t.bandRow ? "1" : "0");
  tbl.appendChild(pr);
  const grid = frag(doc, "<a:tblGrid/>");
  for (const w of t.cols) grid.appendChild(frag(doc, `<a:gridCol w="${Math.round(w)}"/>`));
  tbl.appendChild(grid);
  for (const row of t.rows) {
    const tr = frag(doc, `<a:tr h="${Math.round(row.h)}"/>`);
    for (const cell of row.cells) {
      const tc = frag(doc, "<a:tc/>");
      for (const k of ["gridSpan", "rowSpan"]) if (cell[k] > 1) tc.setAttribute(k, String(cell[k]));
      if (cell.hMerge) tc.setAttribute("hMerge", "1");
      if (cell.vMerge) tc.setAttribute("vMerge", "1");
      const body = frag(doc, "<a:txBody><a:bodyPr/><a:lstStyle/></a:txBody>");
      parasXml(doc, cell.text?.paras || [], body);
      tc.appendChild(body);
      const tcPr = cell._x ? frag(doc, cell._x) : frag(doc, "<a:tcPr/>");
      if (cell.anchor) tcPr.setAttribute("anchor", cell.anchor); else tcPr.removeAttribute("anchor");
      removeKids(tcPr, ["noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"]);
      const fx = fillXml(cell.fill);
      if (fx) insertOrdered(tcPr, frag(doc, fx), ["lnL", "lnR", "lnT", "lnB", "lnTlToBr", "lnBlToTr", "cell3D", "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill", "headers", "extLst"]);
      tc.appendChild(tcPr);
      tr.appendChild(tc);
    }
    tbl.appendChild(tr);
  }
  return tbl;
}

function readChart(doc) {
  const plot = deep(doc, "plotArea");
  if (!plot) return null;
  const kinds = { barChart: "bar", bar3DChart: "bar", lineChart: "line", line3DChart: "line", pieChart: "pie", pie3DChart: "pie", doughnutChart: "doughnut", areaChart: "area" };
  const ch = Array.from(plot.children).find((c) => kinds[c.localName]);
  if (!ch) return { type: "bar", cats: [], series: [], unsupported: true };
  let type = kinds[ch.localName];
  if (type === "bar" && attr(kid(ch, "barDir"), "val") === "col") type = "col";
  const series = [], cache = (el) => deepAll(el, "pt").map((p) => ({ i: num(p, "idx", 0), v: kid(p, "v")?.textContent ?? "" }));
  let cats = [];
  for (const ser of kids(ch, "ser")) {
    const name = deep(kid(ser, "tx"), "v")?.textContent || "";
    const catPts = cache(kid(ser, "cat")); if (catPts.length && !cats.length) { cats = []; for (const p of catPts) cats[p.i] = p.v; }
    const vals = []; for (const p of cache(kid(ser, "val"))) vals[p.i] = Number(p.v) || 0;
    const color = readColor(path(ser, "spPr", "solidFill"));
    series.push({ name, vals: Array.from(vals, (v) => v || 0), ...(color ? { color } : {}) });
  }
  const title = deepAll(kid(deep(doc, "chart"), "title"), "t").map((t) => t.textContent).join("");
  return { type, cats: Array.from(cats, (c) => c ?? ""), series, ...(title ? { title } : {}), stacked: attr(kid(ch, "grouping"), "val") === "stacked" || undefined };
}
export function chartXml(chart, workbookId = "") {
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const n = Math.max(chart.cats.length, ...chart.series.map((s) => s.vals.length));
  const catRef = `<c:cat><c:strRef><c:f>Sheet1!$A$2:$A$${n + 1}</c:f><c:strCache><c:ptCount val="${n}"/>${chart.cats.map((c, i) => `<c:pt idx="${i}"><c:v>${escXml(c)}</c:v></c:pt>`).join("")}</c:strCache></c:strRef></c:cat>`;
  const col = (i) => excelColumn(i + 2);
  const ser = chart.series.map((s, i) => `<c:ser><c:idx val="${i}"/><c:order val="${i}"/><c:tx><c:strRef><c:f>Sheet1!$${col(i)}$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>${escXml(s.name)}</c:v></c:pt></c:strCache></c:strRef></c:tx>`
    + (s.color && chart.type !== "pie" && chart.type !== "doughnut" ? `<c:spPr><a:solidFill>${colorXml(s.color)}</a:solidFill></c:spPr>` : "")
    + (chart.type === "line" ? '<c:marker><c:symbol val="circle"/><c:size val="6"/></c:marker>' : "")
    + catRef + `<c:val><c:numRef><c:f>Sheet1!$${col(i)}$2:$${col(i)}$${n + 1}</c:f><c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="${n}"/>${s.vals.map((v, j) => `<c:pt idx="${j}"><c:v>${number(v)}</c:v></c:pt>`).join("")}</c:numCache></c:numRef></c:val>`
    + (chart.type === "line" ? '<c:smooth val="0"/>' : "") + "</c:ser>").join("");
  const axes = '<c:axId val="111111111"/><c:axId val="222222222"/>';
  let plot;
  if (chart.type === "pie") plot = `<c:pieChart><c:varyColors val="1"/>${ser}<c:firstSliceAng val="0"/></c:pieChart>`;
  else if (chart.type === "doughnut") plot = `<c:doughnutChart><c:varyColors val="1"/>${ser}<c:firstSliceAng val="0"/><c:holeSize val="55"/></c:doughnutChart>`;
  else {
    const body = chart.type === "line" ? `<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>${ser}<c:marker val="1"/>${axes}</c:lineChart>`
      : chart.type === "area" ? `<c:areaChart><c:grouping val="${chart.stacked ? "stacked" : "standard"}"/><c:varyColors val="0"/>${ser}${axes}</c:areaChart>`
      : `<c:barChart><c:barDir val="${chart.type === "bar" ? "bar" : "col"}"/><c:grouping val="${chart.stacked ? "stacked" : "clustered"}"/><c:varyColors val="0"/>${ser}<c:gapWidth val="80"/>${chart.stacked ? '<c:overlap val="100"/>' : ""}${axes}</c:barChart>`;
    const pos = chart.type === "bar" ? ["l", "b"] : ["b", "l"];
    plot = body + `<c:catAx><c:axId val="111111111"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="${pos[0]}"/><c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="222222222"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>`
      + `<c:valAx><c:axId val="222222222"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="${pos[1]}"/><c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="D9D9D9"/></a:solidFill></a:ln></c:spPr></c:majorGridlines><c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="111111111"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>`;
  }
  const title = chart.title ? `<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>${escXml(chart.title)}</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>` : '<c:autoTitleDeleted val="1"/>';
  return XML_HEAD + `<c:chartSpace xmlns:c="${NS.c}" xmlns:a="${NS.a}" xmlns:r="${NS.r}"><c:roundedCorners val="0"/><c:chart>${title}<c:plotArea><c:layout/>${plot}</c:plotArea>`
    + `<c:legend><c:legendPos val="${chart.type === "pie" || chart.type === "doughnut" ? "r" : "b"}"/><c:overlay val="0"/></c:legend><c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>`
    + `<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="1400"><a:solidFill><a:schemeClr val="tx1"><a:lumMod val="65000"/><a:lumOff val="35000"/></a:schemeClr></a:solidFill></a:defRPr></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>${workbookId ? `<c:externalData r:id="${escXml(workbookId)}"><c:autoUpdate val="0"/></c:externalData>` : ""}</c:chartSpace>`;
}

export function newElementXml(el, rIdFor) {
  const id = el.nvId || 2, name = escXml(el.name || "");
  const xf = `<a:xfrm${el.rot ? ` rot="${Math.round(el.rot * 60000)}"` : ""}${el.flipH ? ' flipH="1"' : ""}${el.flipV ? ' flipV="1"' : ""}><a:off x="${Math.round(el.x)}" y="${Math.round(el.y)}"/><a:ext cx="${Math.round(el.w)}" cy="${Math.round(el.h)}"/></a:xfrm>`;
  if (el.kind === "pic") {
    const c = el.pic.crop;
    return `<p:pic><p:nvPicPr><p:cNvPr id="${id}" name="${name}"${el.descr ? ` descr="${escXml(el.descr)}"` : ""}/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${rIdFor(el.pic.media)}"/>`
      + (c ? `<a:srcRect l="${Math.round(c.l * 1000)}" t="${Math.round(c.t * 1000)}" r="${Math.round(c.r * 1000)}" b="${Math.round(c.b * 1000)}"/>` : "")
      + `<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>${xf}<a:prstGeom prst="${escXml(el.geom || "rect")}"><a:avLst/></a:prstGeom></p:spPr></p:pic>`;
  }
  if (el.kind === "table") return `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${id}" name="${name}"/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="${Math.round(el.x)}" y="${Math.round(el.y)}"/><a:ext cx="${Math.round(el.w)}" cy="${Math.round(el.h)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl/></a:graphicData></a:graphic></p:graphicFrame>`;
  if (el.kind === "chart") return `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${id}" name="${name}"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="${Math.round(el.x)}" y="${Math.round(el.y)}"/><a:ext cx="${Math.round(el.w)}" cy="${Math.round(el.h)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart xmlns:c="${NS.c}" r:id=""/></a:graphicData></a:graphic></p:graphicFrame>`;
  if (el.kind === "group") return `<p:grpSp><p:nvGrpSpPr><p:cNvPr id="${id}" name="${name}"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="${Math.round(el.x)}" y="${Math.round(el.y)}"/><a:ext cx="${Math.round(el.w)}" cy="${Math.round(el.h)}"/><a:chOff x="${Math.round(el._ch.x)}" y="${Math.round(el._ch.y)}"/><a:chExt cx="${Math.round(el._ch.w)}" cy="${Math.round(el._ch.h)}"/></a:xfrm></p:grpSpPr></p:grpSp>`;
  const isLine = ["line", "straightConnector1", "bentConnector3", "curvedConnector3"].includes(el.geom);
  if (isLine) return `<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="${id}" name="${name}"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr><p:spPr>${xf}<a:prstGeom prst="${escXml(el.geom)}"><a:avLst/></a:prstGeom></p:spPr></p:cxnSp>`;
  const ph = el.ph ? `<p:ph${el.ph.type ? ` type="${escXml(el.ph.type)}"` : ""}${el.ph.idx ? ` idx="${escXml(el.ph.idx)}"` : ""}/>` : "";
  return `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="${name}"/><p:cNvSpPr${el.txBox ? ' txBox="1"' : ""}>${el.ph ? '<a:spLocks noGrp="1"/>' : ""}</p:cNvSpPr><p:nvPr>${ph}</p:nvPr></p:nvSpPr><p:spPr>${el.xfrmInherited ? "" : xf}${el.ph && !el.geom ? "" : `<a:prstGeom prst="${escXml(el.geom || "rect")}"><a:avLst/></a:prstGeom>`}</p:spPr>`
    + `<p:txBody><a:bodyPr${el.txBox ? ' wrap="square" rtlCol="0"' : ""}>${el.txBox ? "<a:spAutoFit/>" : ""}</a:bodyPr><a:lstStyle/><a:p><a:endParaRPr lang="ru-RU"/></a:p></p:txBody></p:sp>`;
}

function writeElement(doc, el, ctx) {
  const o = el._o || {};
  let node;
  if (el._xml && el._sig === sigOf(el)) { node = frag(doc, el._xml); ctx.remap?.(node, el); }
  else {
    const isNew = !el._xml;
    node = frag(doc, isNew ? newElementXml(el, (m) => ctx.relFor(m, el)) : el._xml);
    if (!isNew) ctx.remap?.(node, el);
    const base = isNew ? { kind: el.kind, x: el.x, y: el.y, w: el.w, h: el.h, rot: el.rot, flipH: el.flipH, flipV: el.flipV, geom: el.geom, xfrmInherited: el.xfrmInherited, name: el.name } : o;
    const nv = nvOf(node), cNvPr = kid(nv, "cNvPr");
    if (cNvPr && !same(base.name, el.name)) cNvPr.setAttribute("name", el.name || "");
    if (cNvPr && !same(o.descr, el.descr)) { if (el.descr) cNvPr.setAttribute("descr", el.descr); else cNvPr.removeAttribute("descr"); }
    if (cNvPr && !same(o.hidden, el.hidden)) { if (el.hidden) cNvPr.setAttribute("hidden", "1"); else cNvPr.removeAttribute("hidden"); }
    if (el.kind !== "raw" || true) {
      const xKeys = ["x", "y", "w", "h", "rot", "flipH", "flipV", "xfrmInherited"];
      if (xKeys.some((k) => !same(base[k], el[k]))) patchXfrm(doc, node, el);
    }
    if (el.kind === "shape" || el.kind === "pic") {
      let spPr = kid(node, "spPr");
      if (!spPr) { spPr = frag(doc, "<p:spPr/>"); nv.after(spPr); }
      if (!same(base.geom, el.geom) && el.geom && el.geom !== "custom") {
        removeKids(spPr, ["prstGeom", "custGeom"]);
        insertOrdered(spPr, frag(doc, `<a:prstGeom prst="${escXml(el.geom)}"><a:avLst/></a:prstGeom>`), SPPR_ORDER);
      }
      if (!same(isNew ? undefined : o.fill, el.fill)) {
        removeKids(spPr, ["noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"]);
        const fx = fillXml(el.fill);
        if (fx) insertOrdered(spPr, frag(doc, fx), SPPR_ORDER);
      }
      if (!same(isNew ? undefined : o.line, el.line)) {
        const oldLn = kid(spPr, "ln");
        const keep = oldLn ? serialize(oldLn) : null;
        removeKids(spPr, ["ln"]);
        if (el.line) insertOrdered(spPr, lineXml(doc, el.line, keep), SPPR_ORDER);
      }
    }
    if (el.kind === "shape" && el.text && !same(isNew ? undefined : o.text, el.text)) {
      let tb = kid(node, "txBody");
      if (!tb) { tb = frag(doc, "<p:txBody><a:bodyPr/><a:lstStyle/></p:txBody>"); node.appendChild(tb); }
      writeTextBody(doc, tb, el.text, isNew ? { paras: null } : o.text);
    }
    if (el.kind === "pic" && !same(isNew ? null : o.pic, el.pic)) {
      const bf = kid(node, "blipFill"), blip = kid(bf, "blip");
      if (!same(o.pic?.media, el.pic.media)) blip.setAttributeNS(NS.r, "r:embed", ctx.relFor(el.pic.media, el));
      removeKids(bf, ["srcRect"]);
      const c = el.pic.crop;
      if (c) blip.after(frag(doc, `<a:srcRect l="${Math.round(c.l * 1000)}" t="${Math.round(c.t * 1000)}" r="${Math.round(c.r * 1000)}" b="${Math.round(c.b * 1000)}"/>`));
    }
    if (el.kind === "table" && !same(isNew ? null : o.table, el.table)) {
      const gd = deep(node, "graphicData"); removeKids(gd, ["tbl"]); gd.appendChild(tableXml(doc, el.table));
    }
    if (el.kind === "chart" && (isNew || !same(o.chart, el.chart))) {
      el._chartPath = ctx.writeChart(el);
      const c = deep(node, "chart"); if (c) c.setAttributeNS(NS.r, "r:id", ctx.relFor(el._chartPath, el, REL.chart));
    }
    if (el.kind === "group") {
      for (const c of Array.from(node.children)) if (!["nvGrpSpPr", "grpSpPr", "extLst"].includes(c.localName)) c.remove();
      const ext = kid(node, "extLst");
      for (const ch of el.children) { const cn = writeElement(doc, ch, ctx); ext ? node.insertBefore(cn, ext) : node.appendChild(cn); }
    }
  }

  const cNvPr = kid(nvOf(node), "cNvPr");
  if (cNvPr) {
    let id = num(cNvPr, "id", 0);
    if (!id || ctx.ids.has(id)) { id = ctx.nextId(); cNvPr.setAttribute("id", String(id)); }
    ctx.ids.add(id); el.nvId = id;
  }
  if (el.kind === "group" && el._xml && el._sig === sigOf(el)) {

    for (const c of deepAll(node, "cNvPr")) ctx.ids.add(num(c, "id", 0));
  }
  return node;
}
function patchXfrm(doc, node, el) {
  const n = node.localName;
  let holder, x;
  if (n === "graphicFrame") { x = kid(node, "xfrm"); if (!x) { x = frag(doc, "<p:xfrm/>"); nvOf(node).after(x); } }
  else if (n === "grpSp") { holder = kid(node, "grpSpPr"); x = kid(holder, "xfrm"); if (!x) { x = frag(doc, "<a:xfrm/>"); holder.insertBefore(x, holder.firstChild); } }
  else {
    holder = kid(node, "spPr"); if (!holder) { holder = frag(doc, "<p:spPr/>"); nvOf(node).after(holder); }
    x = kid(holder, "xfrm");
    if (el.xfrmInherited) { x?.remove(); return; }
    if (!x) { x = frag(doc, "<a:xfrm/>"); holder.insertBefore(x, holder.firstChild); }
  }
  if (el.rot) x.setAttribute("rot", String(Math.round(el.rot * 60000) % 21600000)); else x.removeAttribute("rot");
  if (el.flipH) x.setAttribute("flipH", "1"); else x.removeAttribute("flipH");
  if (el.flipV) x.setAttribute("flipV", "1"); else x.removeAttribute("flipV");
  let off = kid(x, "off"), ext = kid(x, "ext");
  if (!off) { off = frag(doc, '<a:off x="0" y="0"/>'); x.insertBefore(off, x.firstChild); }
  if (!ext) { ext = frag(doc, '<a:ext cx="0" cy="0"/>'); off.after(ext); }
  off.setAttribute("x", String(Math.round(el.x))); off.setAttribute("y", String(Math.round(el.y)));
  ext.setAttribute("cx", String(Math.max(0, Math.round(el.w)))); ext.setAttribute("cy", String(Math.max(0, Math.round(el.h))));
  if (n === "grpSp") {
    if (!kid(x, "chOff")) x.appendChild(frag(doc, `<a:chOff x="${Math.round(el._ch.x)}" y="${Math.round(el._ch.y)}"/>`));
    if (!kid(x, "chExt")) x.appendChild(frag(doc, `<a:chExt cx="${Math.round(el._ch.w)}" cy="${Math.round(el._ch.h)}"/>`));
  }
}

function readBg(cSld, rels) {
  const bg = kid(cSld, "bg");
  if (!bg) return undefined;
  const pr = kid(bg, "bgPr");
  if (pr) { const f = readFill(pr, rels); return f === null ? { none: true } : f || undefined; }
  const ref = kid(bg, "bgRef");
  if (ref) return { ref: num(ref, "idx", 1001), color: readColor(ref) };
  return undefined;
}
function bgXml(bg, relFor) {
  if (!bg) return "";
  if (bg.ref) return `<p:bg><p:bgRef idx="${bg.ref}">${colorXml(bg.color)}</p:bgRef></p:bg>`;
  if (bg.image) return `<p:bg><p:bgPr><a:blipFill dpi="0" rotWithShape="1"><a:blip r:embed="${relFor(bg.image)}"/><a:srcRect/><a:stretch><a:fillRect/></a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>`;
  return `<p:bg><p:bgPr>${bg.none ? "<a:noFill/>" : fillXml(bg)}<a:effectLst/></p:bgPr></p:bg>`;
}
const TRANS = ["fade", "push", "wipe", "cover", "pull", "split", "zoom", "dissolve", "cut", "random", "circle", "diamond", "plus", "wedge", "wheel", "randomBar", "blinds", "checker", "comb", "strips", "newsflash"];
function readTransition(sld) {
  let tr = kid(sld, "transition");
  if (!tr) for (const ac of kids(sld, "AlternateContent")) { const fb = kid(ac, "Fallback"); tr = kid(fb, "transition") || deep(ac, "transition"); if (tr) break; }
  if (!tr) return undefined;
  const t = { spd: attr(tr, "spd", "med") };
  const dur = tr.getAttributeNS(NS.p14, "dur") || attr(tr, "p14:dur"); if (dur) t.dur = Number(dur);
  if (tr.hasAttribute("advTm")) t.advTm = num(tr, "advTm");
  if (attr(tr, "advClick") === "0") t.noClick = true;
  const fx = tr.firstElementChild;
  t.type = fx ? fx.localName : "none";
  if (fx?.hasAttribute("dir")) t.dir = attr(fx, "dir");
  if (fx?.hasAttribute("thruBlk")) t.thruBlk = bool(fx, "thruBlk");
  return t;
}
function transitionXml(t) {
  if (!t || t.type === "none" && !t.advTm) return "";
  const spd = t.dur ? (t.dur <= 500 ? "fast" : t.dur >= 1000 ? "slow" : "med") : t.spd || "med";
  const fx = !t.type || t.type === "none" ? "" : `<p:${t.type}${t.dir ? ` dir="${escXml(t.dir)}"` : ""}/>`;
  return `<p:transition spd="${spd}"${t.noClick ? ' advClick="0"' : ""}${t.advTm ? ` advTm="${Math.round(t.advTm)}"` : ""}>${fx}</p:transition>`;
}
const ANIM_IDS = { 1: "appear", 2: "fly", 10: "fade", 22: "wipe", 23: "zoom", 53: "zoom", 16: "split", 9: "dissolve" };
const ANIM_PRESET = { appear: 1, fly: 2, fade: 10, wipe: 22, zoom: 53 };
const FLY_DIR = { 4: "b", 8: "l", 2: "r", 1: "t" };
function readTiming(sld) {
  const out = new Map();
  const timing = kid(sld, "timing");
  if (!timing) return out;
  let order = 0, lastClick = -1;
  for (const ctn of deepAll(timing, "cTn")) {
    const cls = attr(ctn, "presetClass");
    if (!cls) continue;
    const tgt = deep(ctn, "spTgt"); if (!tgt) continue;
    const spid = num(tgt, "spid", 0);
    const node = attr(ctn, "nodeType", "clickEffect");
    const trig = node === "withEffect" ? "with" : node === "afterEffect" ? "after" : "click";
    if (trig === "click") lastClick = order;
    const durs = deepAll(ctn, "cTn").map((c) => num(c, "dur", 0)).filter((d) => d > 1);
    const pid = num(ctn, "presetID", 0);
    const type = cls === "entr" ? ANIM_IDS[pid] || "fade" : cls === "exit" ? "exit" : cls === "emph" ? "emph" : "other";
    if (out.has(spid)) continue;
    out.set(spid, { type, trig, dur: durs[0] || 500, order: order++, ...(type === "fly" ? { dir: FLY_DIR[num(ctn, "presetSubtype", 4)] || "b" } : {}), ...(cls !== "entr" ? { cls, keep: true } : {}) });
  }
  return out;
}
function timingXml(anims) {

  if (!anims.length) return "";
  let id = 3; const groups = [];
  for (const a of anims) {
    if (a.trig === "click" || !groups.length) groups.push([[a]]);
    else if (a.trig === "with") groups[groups.length - 1][groups[groups.length - 1].length - 1].push(a);
    else groups[groups.length - 1].push([a]);
  }
  const effect = (a, node) => {
    const dur = Math.max(1, Math.round(a.dur || 500));
    const tgt = `<p:tgtEl><p:spTgt spid="${a.spid}"/></p:tgtEl>`;
    const set = `<p:set><p:cBhvr><p:cTn id="${id++}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>${tgt}<p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set>`;
    let body = set;
    if (a.type === "fade") body += `<p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="${id++}" dur="${dur}"/>${tgt}</p:cBhvr></p:animEffect>`;
    if (a.type === "wipe") body += `<p:animEffect transition="in" filter="wipe(${{ b: "up", t: "down", l: "right", r: "left" }[a.dir || "l"] || "right"})"><p:cBhvr><p:cTn id="${id++}" dur="${dur}"/>${tgt}</p:cBhvr></p:animEffect>`;
    const anim = (attrName, from, to) => `<p:anim calcmode="lin" valueType="num"><p:cBhvr additive="base"><p:cTn id="${id++}" dur="${dur}" fill="hold"/>${tgt}<p:attrNameLst><p:attrName>${attrName}</p:attrName></p:attrNameLst></p:cBhvr><p:tavLst><p:tav tm="0"><p:val><p:strVal val="${from}"/></p:val></p:tav><p:tav tm="100000"><p:val><p:strVal val="${to}"/></p:val></p:tav></p:tavLst></p:anim>`;
    if (a.type === "fly") {
      const d = a.dir || "b";
      body += d === "l" ? anim("ppt_x", "0-#ppt_w/2", "#ppt_x") + anim("ppt_y", "#ppt_y", "#ppt_y") : d === "r" ? anim("ppt_x", "1+#ppt_w/2", "#ppt_x") + anim("ppt_y", "#ppt_y", "#ppt_y")
        : d === "t" ? anim("ppt_x", "#ppt_x", "#ppt_x") + anim("ppt_y", "0-#ppt_h/2", "#ppt_y") : anim("ppt_x", "#ppt_x", "#ppt_x") + anim("ppt_y", "1+#ppt_h/2", "#ppt_y");
    }
    if (a.type === "zoom") body += anim("ppt_w", "0", "#ppt_w") + anim("ppt_h", "0", "#ppt_h") + `<p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="${id++}" dur="${dur}"/>${tgt}</p:cBhvr></p:animEffect>`;
    const sub = a.type === "fly" ? { b: 4, l: 8, r: 2, t: 1 }[a.dir || "b"] : a.type === "wipe" ? { b: 4, l: 8, r: 2, t: 1 }[a.dir || "l"] : 0;
    return { dur: a.type === "appear" ? 1 : dur, xml: (cid) => `<p:par><p:cTn id="${cid}" presetID="${ANIM_PRESET[a.type] || 10}" presetClass="entr" presetSubtype="${sub}" fill="hold"${a.text ? ' grpId="0"' : ""} nodeType="${node}"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>${body}</p:childTnLst></p:cTn></p:par>` };
  };
  let seq = "";
  for (const click of groups) {
    const outerId = id++;
    let inner = "", delay = 0, first = true;
    for (const step of click) {
      const stepId = id++;
      let pars = "", maxDur = 0;
      step.forEach((a, k) => { const node = first && k === 0 ? "clickEffect" : k > 0 ? "withEffect" : "afterEffect"; const cid = id++; const e = effect(a, node); pars += e.xml(cid); maxDur = Math.max(maxDur, e.dur); });
      inner += `<p:par><p:cTn id="${stepId}" fill="hold"><p:stCondLst><p:cond delay="${delay}"/></p:stCondLst><p:childTnLst>${pars}</p:childTnLst></p:cTn></p:par>`;
      delay += maxDur; first = false;
    }
    seq += `<p:par><p:cTn id="${outerId}" fill="hold"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst><p:childTnLst>${inner}</p:childTnLst></p:cTn></p:par>`;
  }
  const bld = anims.filter((a) => a.text).map((a) => `<p:bldP spid="${a.spid}" grpId="0" animBg="1"/>`).join("");
  return `<p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst><p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>${seq}</p:childTnLst></p:cTn>`
    + `<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst><p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq></p:childTnLst></p:cTn></p:par></p:tnLst>${bld ? `<p:bldLst>${bld}</p:bldLst>` : ""}</p:timing>`;
}

export function slideAnims(slide) {
  return slide.elements.filter((e) => e.anim && !e.anim.keep).sort((a, b) => (a.anim.order ?? 0) - (b.anim.order ?? 0)).map((el) => ({ el, anim: el.anim }));
}

function readTheme(xml) {
  const doc = parseXml(xml);
  const cs = deep(doc, "clrScheme"), colors = {};
  for (const k of SCHEME_KEYS) { const c = readColor(kid(cs, k)); colors[k] = typeof c === "string" ? c : c?.c || "#000000"; }
  const fs = deep(doc, "fontScheme");
  const fonts = { major: attr(path(fs, "majorFont", "latin"), "typeface", "Calibri Light"), minor: attr(path(fs, "minorFont", "latin"), "typeface", "Calibri") };
  const bgStyles = kids(deep(doc, "bgFillStyleLst")).map((f) => readFill({ children: [f] }, null));
  return { colors, fonts, name: attr(doc.documentElement, "name", ""), bgStyles };
}
function readClrMap(el) { if (!el) return { ...DEFAULT_CLRMAP }; const m = {}; for (const a of el.attributes) m[a.name] = a.value; return { ...DEFAULT_CLRMAP, ...m }; }

function phAutofit(bp) { if (!bp) return undefined; if (kid(bp, "normAutofit")) return true; if (kid(bp, "noAutofit") || kid(bp, "spAutoFit")) return false; return undefined; }
function readPlaceholders(spTree) {
  const out = [];
  for (const sp of kids(spTree)) {
    const nv = nvOf(sp); const ph = deep(kid(nv, "nvPr"), "ph");
    if (!ph) continue;
    const box = {}; readXfrm(xfrmOf(sp), box);
    const tb = kid(sp, "txBody");
    out.push({ type: attr(ph, "type", ""), idx: attr(ph, "idx", ""), box: box.w ? box : null, lst: readLstStyle(kid(tb, "lstStyle")), anchor: attr(kid(tb, "bodyPr"), "anchor"),
      prompt: tb ? plainText({ paras: readParas(tb) }) : "", autofit: phAutofit(kid(tb, "bodyPr")),
      ins: ["lIns", "tIns", "rIns", "bIns"].reduce((o, k) => { const b = kid(tb, "bodyPr"); if (b?.hasAttribute(k)) o[k] = num(b, k); return o; }, {}),
      fill: readFill(kid(sp, "spPr"), null), line: readLine(kid(sp, "spPr")) });
  }
  return out;
}
function decorElements(spTree, ctx) {
  const out = [];
  for (const node of kids(spTree)) {
    if (["nvGrpSpPr", "grpSpPr", "extLst"].includes(node.localName)) continue;
    if (deep(kid(nvOf(node), "nvPr"), "ph")) continue;
    try { out.push(readElement(node, ctx)); } catch {  }
  }
  return out;
}

export class Deck {
  constructor() {
    this.slides = []; this.layouts = []; this.masters = []; this.media = new Map(); this.charts = new Map();
    this.w = 12192000; this.h = 6858000; this.zip = null; this.format = "pptx"; this.defaultStyle = []; this.notesMaster = null;
    this.urls = new Map();
  }
  get theme() { return this.masters[0]?.theme || { colors: {}, fonts: {}, clrMap: DEFAULT_CLRMAP }; }
  layout(path) { return this.layouts.find((l) => l.path === path) || this.layouts[0]; }
  layoutOf(slide) { return this.layout(slide.layout); }
  masterOf(slide) { const l = this.layoutOf(slide); return this.masters.find((m) => m.path === l?.master) || this.masters[0]; }
  themeOf(slide) { const m = this.masterOf(slide); return m ? { ...m.theme, clrMap: m.clrMap } : this.theme; }

  phChain(slide, el) {
    if (!el.ph) return [];
    const l = this.layoutOf(slide), m = this.masterOf(slide);
    const t = el.ph.type || (el.ph.idx ? "body" : "");
    const match = (list) => (el.ph.idx && list.find((p) => p.idx === el.ph.idx && (!el.ph.type || p.type === el.ph.type || (!p.type && t === "body"))))
      || list.find((p) => (p.type || "body") === t || (t === "ctrTitle" && p.type === "title") || (t === "subTitle" && (p.type || "body") === "body"))
      || (el.ph.idx ? list.find((p) => p.idx === el.ph.idx) : null);
    const lp = l ? match(l.placeholders) : null;
    const mt = t === "ctrTitle" ? "title" : t === "subTitle" || t === "obj" || !t ? "body" : t;
    const mp = m ? m.placeholders.find((p) => (p.type || "body") === mt) || (["title", "body"].includes(mt) ? null : null) : null;
    return [lp, mp].filter(Boolean);
  }
  boxOf(slide, el) {
    if (!el.xfrmInherited) return { x: el.x, y: el.y, w: el.w, h: el.h };
    for (const p of this.phChain(slide, el)) if (p.box) return { x: p.box.x, y: p.box.y, w: p.box.w, h: p.box.h };
    return { x: el.x || 0, y: el.y || 0, w: el.w || this.w / 2, h: el.h || this.h / 4 };
  }

  levelStyles(slide, el) {
    const m = this.masterOf(slide);
    const t = el.ph ? el.ph.type || "body" : "";
    const base = !el.ph ? this.defaultStyle : t === "title" || t === "ctrTitle" ? m?.txStyles.title : ["body", "subTitle", "obj", ""].includes(t) || !t ? m?.txStyles.body : m?.txStyles.other;
    const chain = this.phChain(slide, el).reverse();
    const out = [];
    for (let i = 0; i < 9; i++) {
      const s = { p: { algn: "l", marL: 0, indent: 0 }, r: { sz: 18, color: "scheme:tx1", font: t === "title" || t === "ctrTitle" ? "+mj-lt" : "+mn-lt" } };
      const add = (lv) => { if (!lv) return; Object.assign(s.p, lv.p); Object.assign(s.r, lv.r); };
      add(base?.[i]);
      if (!el.ph && el._st?.font) s.r.color = el._st.font;
      for (const p of chain) add(p.lst?.[i]);
      add(el.text?._lst?.[i]);
      out.push(s);
    }
    return out;
  }
  phInfo(slide, el) { return this.phChain(slide, el)[0] || null; }
  font(name, slide) {
    const th = slide ? this.themeOf(slide) : this.theme;
    if (!name) return th.fonts?.minor;
    if (name.startsWith("+mj")) return th.fonts?.major;
    if (name.startsWith("+mn")) return th.fonts?.minor;
    return name;
  }
  color(c, slide, ph) { return cssColor(c, slide ? this.themeOf(slide) : this.theme, ph); }
  async mediaUrl(key) {
    if (!key) return "";
    if (this.urls.has(key)) return this.urls.get(key);
    const m = this.media.get(key);
    let bytes = m?.bytes;
    if (!bytes && this.zip?.has(key)) bytes = await this.zip.bytes(key);
    if (!bytes) return "";
    const ext = (key.split(".").pop() || "").toLowerCase();
    const type = m?.type || { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", svg: "image/svg+xml", webp: "image/webp", bmp: "image/bmp", emf: "image/emf", wmf: "image/wmf", tif: "image/tiff", tiff: "image/tiff" }[ext] || "application/octet-stream";
    const url = URL.createObjectURL(new Blob([bytes], { type }));
    this.urls.set(key, url);
    return url;
  }
  mediaUrlSync(key) { return this.urls.get(key) || ""; }

  async mediaData(key) {
    if (!key) return null;
    const m = this.media.get(key);
    let bytes = m?.bytes;
    if (!bytes && this.zip?.has(key)) bytes = await this.zip.bytes(key);
    if (!bytes) return null;
    const ext = (key.split(".").pop() || "").toLowerCase();
    const type = m?.type || { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", svg: "image/svg+xml", webp: "image/webp", bmp: "image/bmp", tif: "image/tiff", tiff: "image/tiff" }[ext] || "application/octet-stream";
    return { bytes, type };
  }
  async preloadMedia() {
    const keys = new Set(this.media.keys());
    const walk = (els) => { for (const e of els) { if (e.pic?.media) keys.add(e.pic.media); if (e.fill?.image) keys.add(e.fill.image); if (e.children) walk(e.children); } };
    for (const s of this.slides) { walk(s.elements); if (s.bg?.image) keys.add(s.bg.image); }
    for (const l of [...this.layouts, ...this.masters]) { walk(l.decor || []); if (l.bg?.image) keys.add(l.bg.image); }
    await Promise.all([...keys].map((k) => this.mediaUrl(k).catch(() => "")));
  }
  addMedia(bytes, type) {
    const ext = { "image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif", "image/webp": "png", "image/svg+xml": "svg", "image/bmp": "bmp" }[type] || "png";
    const key = `new:${uid()}.${ext}`;
    this.media.set(key, { bytes, type });
    this.urls.set(key, URL.createObjectURL(new Blob([bytes], { type })));
    return key;
  }
  destroy() { for (const u of this.urls.values()) URL.revokeObjectURL(u); this.urls.clear(); }
}

export async function openPptx(buffer) {
  const zip = await ZipArchive.read(buffer);
  const deck = new Deck();
  deck.zip = zip;
  const rootRels = new Rels("", await zip.text("_rels/.rels"));
  const presPath = rootRels.ofType(REL.office)[0] ? resolve("", rootRels.ofType(REL.office)[0].target) : "ppt/presentation.xml";
  deck.presPath = presPath;
  const pres = parseXml(await zip.text(presPath));
  const presRels = new Rels(presPath, await zip.text(relsPathOf(presPath)));
  deck.presRels = presRels;
  const sz = deep(pres, "sldSz"); if (sz) { deck.w = num(sz, "cx", deck.w); deck.h = num(sz, "cy", deck.h); }
  deck.defaultStyle = readLstStyle(deep(pres, "defaultTextStyle"));

  const chartCtx = async (relsObj) => {
    for (const r of relsObj.ofType(REL.chart)) {
      const p = relsObj.path(r.id); if (!p || deck.charts.has(p) || !zip.has(p)) continue;
      try { deck.charts.set(p, readChart(parseXml(await zip.text(p)))); } catch { deck.charts.set(p, { type: "bar", cats: [], series: [], unsupported: true }); }
    }
  };

  for (const mid of kids(deep(pres, "sldMasterIdLst"), "sldMasterId")) {
    const mpath = presRels.path(rid(mid, "id")); if (!mpath || !zip.has(mpath)) continue;
    const mdoc = parseXml(await zip.text(mpath));
    const mrels = new Rels(mpath, await zip.text(relsPathOf(mpath)));
    const tpath = mrels.ofType(REL.theme)[0] ? mrels.path(mrels.ofType(REL.theme)[0].id) : null;
    const theme = tpath && zip.has(tpath) ? readTheme(await zip.text(tpath)) : { colors: {}, fonts: { major: "Calibri", minor: "Calibri" } };
    const tx = deep(mdoc, "txStyles");
    const master = { path: mpath, themePath: tpath, theme, clrMap: readClrMap(deep(mdoc, "clrMap")), bg: readBg(deep(mdoc, "cSld"), mrels),
      placeholders: readPlaceholders(deep(mdoc, "spTree")),
      txStyles: { title: readLstStyle(kid(tx, "titleStyle")), body: readLstStyle(kid(tx, "bodyStyle")), other: readLstStyle(kid(tx, "otherStyle")) } };
    master.decor = decorElements(deep(mdoc, "spTree"), { rels: mrels, deck });
    deck.masters.push(master);
    for (const lid of kids(deep(mdoc, "sldLayoutIdLst"), "sldLayoutId")) {
      const lpath = mrels.path(rid(lid, "id")); if (!lpath || !zip.has(lpath)) continue;
      const ldoc = parseXml(await zip.text(lpath));
      const lrels = new Rels(lpath, await zip.text(relsPathOf(lpath)));
      deck.layouts.push({ path: lpath, master: mpath, type: attr(ldoc.documentElement, "type", "cust"), name: attr(deep(ldoc, "cSld"), "name", ""),
        bg: readBg(deep(ldoc, "cSld"), lrels), placeholders: readPlaceholders(deep(ldoc, "spTree")), decor: decorElements(deep(ldoc, "spTree"), { rels: lrels, deck }),
        showMasterSp: attr(ldoc.documentElement, "showMasterSp") !== "0" });
    }
  }
  const nm = kid(deep(pres, "notesMasterIdLst"), "notesMasterId");
  if (nm) deck.notesMaster = presRels.path(rid(nm, "id"));

  for (const sid of kids(deep(pres, "sldIdLst"), "sldId")) {
    const spath = presRels.path(rid(sid, "id")); if (!spath || !zip.has(spath)) continue;
    const xml = await zip.text(spath);
    const doc = parseXml(xml);
    const rels = new Rels(spath, await zip.text(relsPathOf(spath)));
    await chartCtx(rels);
    const slide = readSlide(doc, rels, spath, deck);
    slide.sldId = num(sid, "id", 0); slide.presRel = rid(sid, "id");
    const npath = rels.ofType(REL.notes)[0] ? rels.path(rels.ofType(REL.notes)[0].id) : null;
    if (npath && zip.has(npath)) {
      slide._notesPath = npath;
      const ndoc = parseXml(await zip.text(npath));
      const body = kids(deep(ndoc, "spTree"), "sp").find((sp) => attr(deep(kid(nvOf(sp), "nvPr"), "ph"), "type") === "body");
      slide.notes = body ? plainText({ paras: readParas(kid(body, "txBody")) }) : "";
    } else slide.notes = "";
    markSlide(slide);
    deck.slides.push(slide);
  }
  return deck;
}

function readSlide(doc, rels, spath, deck) {
  const sld = doc.documentElement, cSld = kid(sld, "cSld");
  const lr = rels.ofType(REL.layout)[0];
  const slide = { id: uid(), path: spath, layout: lr ? rels.path(lr.id) : deck.layouts[0]?.path, elements: [], _xml: null };
  if (attr(sld, "show") === "0") slide.hidden = true;
  if (attr(sld, "showMasterSp") === "0") slide.noMasterSp = true;
  const bg = readBg(cSld, rels); if (bg !== undefined) slide.bg = bg;
  const tr = readTransition(sld); if (tr) slide.transition = tr;
  const ctx = { rels, deck, charts: deck.charts };
  const anims = readTiming(sld);
  for (const node of kids(kid(cSld, "spTree"))) {
    if (["nvGrpSpPr", "grpSpPr", "extLst"].includes(node.localName)) continue;
    const el = readElement(node, ctx);
    if (el.nvId && anims.has(el.nvId)) el.anim = anims.get(el.nvId);
    setSrc(el, spath);
    slide.elements.push(el);
  }
  slide._hasTiming = !!kid(sld, "timing");
  return slide;
}
function setSrc(el, p) { el._src = p; if (el.children) el.children.forEach((c) => setSrc(c, p)); }
function srcsOf(els, out = new Set()) { for (const e of els) { if (e._src) out.add(e._src); if (e.children) srcsOf(e.children, out); } return out; }
function slideSig(s) { return JSON.stringify({ e: s.elements.map((e) => e._sig === sigOf(e) ? e._sig.length + ":" + e.nvId : sigOf(e)), b: s.bg, h: !!s.hidden, t: s.transition, l: s.layout, a: s.elements.map((e) => e.anim || null) }); }
export function markSlide(slide) { slide._sig = slideSig(slide); slide._notes0 = slide.notes; slide._anim0 = JSON.stringify(slide.elements.map((e) => [e.nvId, e.anim || null])); }

export async function newDeck(opts) {
  const files = newPackage(opts);
  const zip = new ZipArchive();
  for (const [k, v] of Object.entries(files)) zip.set(k, v);
  const deck = await openPptx(await zip.write());
  deck.isNew = true;
  return deck;
}

export function makeSlide(deck, layoutPath) {
  const layout = deck.layout(layoutPath);
  const slide = { id: uid(), path: null, layout: layout?.path, elements: [], notes: "", _new: true };
  let n = 2;
  for (const p of layout?.placeholders || []) {
    if (["dt", "ftr", "sldNum", "hdr"].includes(p.type)) continue;
    const t = p.type;
    slide.elements.push({ id: uid(), kind: "shape", nvId: n, name: `${t === "title" || t === "ctrTitle" ? "Title" : t === "subTitle" ? "Subtitle" : "Content Placeholder"} ${n - 1}`,
      ph: { type: t || (p.idx ? "" : "body"), idx: p.idx || "" }, xfrmInherited: true, rot: 0, text: { paras: [{ runs: [] }] } });
    n++;
  }
  return slide;
}

export async function savePptx(deck, opts = {}) {
  const zip = deck.zip;
  const ct = parseXml(await zip.text("[Content_Types].xml"));
  const ctRoot = ct.documentElement;
  const overrides = () => kids(ctRoot, "Override");
  const addOverride = (part, type) => {
    if (overrides().some((o) => attr(o, "PartName") === "/" + part)) return;
    const o = ct.createElementNS(NS.ct, "Override"); o.setAttribute("PartName", "/" + part); o.setAttribute("ContentType", type); ctRoot.appendChild(o);
  };
  const delOverride = (part) => { for (const o of overrides()) if (attr(o, "PartName") === "/" + part) o.remove(); };
  const addDefault = (ext, type) => { if (!kids(ctRoot, "Default").some((d) => (attr(d, "Extension") || "").toLowerCase() === ext)) { const d = ct.createElementNS(NS.ct, "Default"); d.setAttribute("Extension", ext); d.setAttribute("ContentType", type); ctRoot.insertBefore(d, ctRoot.firstChild); } };
  const pres = parseXml(await zip.text(deck.presPath));
  const presRels = new Rels(deck.presPath, await zip.text(relsPathOf(deck.presPath)));
  const used = new Set(zip.names());
  const freeName = (dir, stem, ext) => { let i = 1; while (used.has(`${dir}/${stem}${i}.${ext}`)) i++; const p = `${dir}/${stem}${i}.${ext}`; used.add(p); return p; };

  const mediaPath = (key) => {
    if (!key?.startsWith("new:")) return key;
    const m = deck.media.get(key);
    if (!m.path) {
      const ext = key.split(".").pop();
      m.path = freeName("ppt/media", "image", ext);
      zip.set(m.path, m.bytes);
      addDefault(ext, m.type || "image/png");
    }
    return m.path;
  };
  for (const [key, m] of deck.media) if (key.startsWith("new:") && m.path && !zip.has(m.path)) zip.set(m.path, m.bytes);

  const srcRels = new Map();
  for (const sl of deck.slides) for (const src of srcsOf(sl.elements)) if (src !== sl.path && !srcRels.has(src) && zip.has(src)) srcRels.set(src, new Rels(src, await zip.text(relsPathOf(src))));

  const keep = new Set(deck.slides.map((s) => s.path).filter(Boolean));
  for (const r of presRels.ofType(REL.slide)) {
    const p = presRels.path(r.id);
    if (keep.has(p)) continue;
    const srels = new Rels(p, await zip.text(relsPathOf(p)));
    for (const nr of srels.ofType(REL.notes)) { const np = srels.path(nr.id); zip.delete(np); zip.delete(relsPathOf(np)); delOverride(np); used.delete(np); }
    zip.delete(p); zip.delete(relsPathOf(p)); delOverride(p); presRels.remove(r.id); used.delete(p);
  }
  let notesMaster = deck.notesMaster;
  const ensureNotesMaster = () => {
    if (notesMaster) return notesMaster;
    notesMaster = "ppt/notesMasters/notesMaster1.xml";
    const theme = freeName("ppt/theme", "theme", "xml");
    zip.set(notesMaster, notesMasterXml());
    zip.set(relsPathOf(notesMaster), NOTES_MASTER_RELS.replace("../theme/theme2.xml", relative(notesMaster, theme)));
    zip.set(theme, themeXml({ en: "Notes", colors: deck.theme.colors, fonts: [deck.theme.fonts.major || "Calibri", deck.theme.fonts.minor || "Calibri"] }));
    addOverride(notesMaster, CT.notesMaster); addOverride(theme, CT.theme);
    const id = presRels.add(REL.notesMaster, notesMaster);
    const lst = frag(pres, `<p:notesMasterIdLst><p:notesMasterId r:id="${id}"/></p:notesMasterIdLst>`);
    const after = deep(pres, "sldMasterIdLst");
    after.after(lst);
    deck.notesMaster = notesMaster;
    return notesMaster;
  };
  const chartJobs = [];
  let maxSldId = Math.max(255, ...deck.slides.map((s) => s.sldId || 0));
  for (const slide of deck.slides) {
    const sigNow = slideSig(slide);
    const notesChanged = slide.notes !== slide._notes0 && !(slide._new && !slide.notes);
    const isNew = !slide.path || slide._dupOf;
    if (!isNew && sigNow === slide._sig && !notesChanged && !opts.forceAll) continue;
    if (!slide.path || slide._dupOf) {
      const src = slide._dupOf;
      slide.path = freeName("ppt/slides", "slide", "xml");
      addOverride(slide.path, CT.slide);
      let rels;
      if (src?.path && zip.has(relsPathOf(src.path))) {
        rels = new Rels(slide.path, null);
        const srcRels = new Rels(src.path, await zip.text(relsPathOf(src.path)));
        for (const r of srcRels.items) if (r.type !== REL.notes) rels.items.push({ ...r, target: r.external ? r.target : relative(slide.path, srcRels.path(r.id)) });
        rels.dirty = true;
      } else rels = new Rels(slide.path, null);
      rels.items = rels.items.filter((r) => r.type !== REL.layout);
      rels.add(REL.layout, slide.layout || deck.layouts[0].path);
      zip.set(relsPathOf(slide.path), rels.xml());
      if (!slide._dupXml) slide._dupXml = emptySlideXml(null);
      zip.set(slide.path, slide._dupXml);
      delete slide._dupOf; delete slide._dupXml; slide._new = false; slide._notesPath = null;
      if (slide.notes) slide._notes0 = null;
      slide._layout0 = null;
    }
    const doc = parseXml(await zip.text(slide.path));
    const rels = new Rels(slide.path, await zip.text(relsPathOf(slide.path)));

    const lr = rels.ofType(REL.layout)[0];
    if (lr && rels.path(lr.id) !== slide.layout && slide.layout) rels.retarget(lr, relative(slide.path, slide.layout));
    const sld = doc.documentElement, cSld = kid(sld, "cSld"), tree = kid(cSld, "spTree");
    const ids = new Set([1]);
    const ctx = {
      remap: (node, el) => {
        const from = el._src ? srcRels.get(el._src) : null;
        if (!from || el._src === slide.path) return;
        for (const n of [node, ...node.getElementsByTagName("*")]) for (const a of Array.from(n.attributes)) {
          if (a.namespaceURI !== NS.r) continue;
          const r = from.byId(a.value); if (!r) continue;
          n.setAttributeNS(NS.r, a.name, r.external ? rels.add(r.type, r.target, true) : rels.add(r.type, from.path(r.id)));
        }
      },
      ids,
      nextId: () => { let i = 2; while (ids.has(i)) i++; return i; },
      relFor: (key, el, type) => {
        if (type === REL.chart) return rels.add(REL.chart, key);
        return rels.add(REL.image, mediaPath(key));
      },
      writeChart: (el) => {
        let p = el._chartPath && !el._chartShared ? el._chartPath : null;
        if (p && deck.slides.some((s) => s !== slide && s.elements.some((e) => e !== el && e._chartPath === p))) p = null;
        const ownChart = !!p;
        if (!p) { p = freeName("ppt/charts", "chart", "xml"); addOverride(p, CT.chart); }
        const packageType = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package";
        const previous = new Rels(p, null);
        const workbook = ownChart && el._chartWorkbook && zip.has(el._chartWorkbook) ? el._chartWorkbook : freeName("ppt/embeddings", "MemuNoteWorkbook", "xlsx");
        el._chartWorkbook = workbook;
        const workbookId = previous.add(packageType, workbook);
        zip.set(p, chartXml(el.chart, workbookId));
        zip.set(relsPathOf(p), previous.xml());
        addDefault("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
        chartJobs.push(chartWorkbook(el.chart).then(bytes => zip.set(workbook, bytes)));
        deck.charts.set(p, clone(el.chart));
        return p;
      },
    };

    const walkIds = (els) => { for (const e of els) { if (e._xml && e._sig === sigOf(e) && e.nvId) ids.add(e.nvId); } };
    walkIds(slide.elements);
    const reserved = new Set(ids);
    for (const c of Array.from(tree.children)) if (!["nvGrpSpPr", "grpSpPr"].includes(c.localName) && c.localName !== "extLst") c.remove();
    const ext = kid(tree, "extLst");
    for (const el of slide.elements) {
      if (el._xml && el._sig === sigOf(el) && el.nvId && reserved.has(el.nvId)) { reserved.delete(el.nvId); ids.delete(el.nvId); }
      const node = writeElement(doc, el, ctx);
      ext ? tree.insertBefore(node, ext) : tree.appendChild(node);
    }

    if (!same(slide._bg0 === undefined ? readBg(cSld, rels) : slide._bg0, slide.bg)) {
      removeKids(cSld, ["bg"]);
      if (slide.bg) cSld.insertBefore(frag(doc, bgXml(slide.bg, (k) => rels.add(REL.image, mediaPath(k)))), cSld.firstChild);
    }
    if (slide.hidden) sld.setAttribute("show", "0"); else sld.removeAttribute("show");

    const trNow = readTransition(sld);
    if (!same(trNow, slide.transition)) {
      for (const c of Array.from(sld.children)) if (c.localName === "transition" || (c.localName === "AlternateContent" && deep(c, "transition"))) c.remove();
      const x = transitionXml(slide.transition);
      if (x) insertOrdered(sld, frag(doc, x), ["cSld", "clrMapOvr", "transition", "AlternateContent", "timing", "extLst"]);
    }

    const animNow = JSON.stringify(slide.elements.map((e) => [e.nvId, e.anim || null]));
    if (animNow !== slide._anim0) {
      const list = slideAnims(slide).filter((a) => a.el.nvId).map(({ el, anim }) => ({ spid: el.nvId, ...anim, text: !!(el.text && plainText(el.text).trim()) }));
      removeKids(sld, ["timing"]);
      const x = timingXml(list);
      if (x) insertOrdered(sld, frag(doc, x), ["cSld", "clrMapOvr", "transition", "AlternateContent", "timing", "extLst"]);
    }
    if (!kid(sld, "clrMapOvr")) cSld.after(frag(doc, "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"));

    if (slide.notes !== slide._notes0) {
      let np = slide._notesPath;
      if (!np && slide.notes) {
        ensureNotesMaster();
        np = freeName("ppt/notesSlides", "notesSlide", "xml");
        const nrels = new Rels(np, null); nrels.add(REL.notesMaster, notesMaster); nrels.add(REL.slide, slide.path);
        zip.set(relsPathOf(np), nrels.xml());
        zip.set(np, XML_HEAD + `<p:notes xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>`
          + `<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>`
          + `<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ru-RU"/></a:p></p:txBody></p:sp>`
          + `</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>`);
        addOverride(np, CT.notes);
        rels.add(REL.notes, np);
        slide._notesPath = np;
      }
      if (np) {
        const ndoc = parseXml(await zip.text(np));
        let body = kids(deep(ndoc, "spTree"), "sp").find((sp) => attr(deep(kid(nvOf(sp), "nvPr"), "ph"), "type") === "body");
        if (!body) { body = frag(ndoc, '<p:sp><p:nvSpPr><p:cNvPr id="9" name="Notes Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'); deep(ndoc, "spTree").appendChild(body); }
        let tb = kid(body, "txBody"); if (!tb) { tb = frag(ndoc, "<p:txBody><a:bodyPr/><a:lstStyle/></p:txBody>"); body.appendChild(tb); }
        removeKids(tb, ["p"]);
        parasXml(ndoc, String(slide.notes || "").split("\n").map((line) => ({ runs: [{ t: line }] })), tb);
        zip.set(np, serialize(ndoc));
      }
    }
    zip.set(slide.path, serialize(doc));
    zip.set(relsPathOf(slide.path), rels.xml());
    for (const el of slide.elements) { refreshEl(el); setSrc(el, slide.path); }
    slide._bg0 = clone(slide.bg);
    markSlide(slide);
  }

  const lst = deep(pres, "sldIdLst") || (() => { const l = frag(pres, "<p:sldIdLst/>"); (deep(pres, "notesMasterIdLst") || deep(pres, "sldMasterIdLst")).after(l); return l; })();
  while (lst.firstChild) lst.firstChild.remove();
  for (const slide of deck.slides) {
    let rId = presRels.items.find((r) => r.type === REL.slide && presRels.path(r.id) === slide.path)?.id;
    if (!rId) rId = presRels.add(REL.slide, slide.path);
    slide.presRel = rId;
    if (!slide.sldId) slide.sldId = ++maxSldId;
    const e = pres.createElementNS(NS.p, "p:sldId"); e.setAttribute("id", String(slide.sldId)); e.setAttributeNS(NS.r, "r:id", rId);
    lst.appendChild(e);
  }
  if (!lst.children.length) lst.remove();
  const sz = deep(pres, "sldSz"); if (sz) { sz.setAttribute("cx", String(deck.w)); sz.setAttribute("cy", String(deck.h)); }
  await setXmlKeep(zip, deck.presPath, serialize(pres));
  zip.set(relsPathOf(deck.presPath), presRels.xml());

  if (deck._layoutScale && (deck._layoutScale.kx !== 1 || deck._layoutScale.ky !== 1)) {
    const { kx, ky } = deck._layoutScale;
    for (const part of [...deck.masters.map((m) => m.path), ...deck.layouts.map((l) => l.path)]) {
      if (!zip.has(part)) continue;
      const d = parseXml(await zip.text(part));
      const tree = deep(d, "spTree");
      for (const off of deepAll(tree, "off")) { off.setAttribute("x", String(Math.round(num(off, "x", 0) * kx))); off.setAttribute("y", String(Math.round(num(off, "y", 0) * ky))); }
      for (const ext of deepAll(tree, "ext")) if (ext.hasAttribute("cx")) { ext.setAttribute("cx", String(Math.round(num(ext, "cx", 0) * kx))); ext.setAttribute("cy", String(Math.round(num(ext, "cy", 0) * ky))); }
      zip.set(part, serialize(d));
    }
    deck._layoutScale = null;
  }

  for (const m of deck.masters) if (m._themeDirty && m.themePath) { zip.set(m.themePath, patchTheme(await zip.text(m.themePath), m.theme)); m._themeDirty = false; }
  for (const d of ["png", "jpeg", "jpg", "gif"]) if ([...deck.media.keys()].some((k) => k.endsWith("." + d))) addDefault(d, d === "jpg" ? "image/jpeg" : "image/" + d);
  if ([...zip.names()].some((n) => n.endsWith(".svg"))) addDefault("svg", "image/svg+xml");

  const MAIN = { pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml", pptm: "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
    ppsx: "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml", potx: "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml" };
  if (opts.ext && MAIN[opts.ext]) {
    const ov = overrides().find((o) => attr(o, "PartName") === "/" + deck.presPath);
    if (ov) ov.setAttribute("ContentType", MAIN[opts.ext]);
    if (opts.ext !== "pptm") for (const r of presRels.items.filter((x) => /vbaProject$/.test(x.type))) {
      const p = presRels.path(r.id); presRels.remove(r.id); if (p) { zip.delete(p); delOverride(p); }
      zip.set(relsPathOf(deck.presPath), presRels.xml());
    }
  }
  await Promise.all(chartJobs);
  await setXmlKeep(zip, "[Content_Types].xml", serialize(ct));
  deck.presRels = presRels;
  await reloadXmlAfterSave(deck);
  return zip.write();
}

async function setXmlKeep(zip, path, text) {
  if (zip.has(path)) {
    const strip = (x) => x.replace(/^\uFEFF?<\?xml[^>]*\?>\s*/, "");
    try { if (strip(await zip.text(path)) === strip(text)) return; } catch {  }
  }
  zip.set(path, text);
}
function refreshEl(el) { el._o = JSON.parse(sigOf(el)); el._sig = sigOf(el); if (el.children) el.children.forEach(refreshEl); }

export async function reloadXmlAfterSave(deck) {
  for (const slide of deck.slides) {
    if (!slide.path) continue;
    const doc = parseXml(await deck.zip.text(slide.path));
    const nodes = kids(deep(doc, "spTree")).filter((n) => !["nvGrpSpPr", "grpSpPr", "extLst"].includes(n.localName));
    slide.elements.forEach((el, i) => { if (nodes[i]) { el._xml = serialize(nodes[i]); syncKids(el, nodes[i]); } });
  }
}
function syncKids(el, node) {
  if (el.kind !== "group" || !el.children) return;
  const nodes = kids(node).filter((n) => !["nvGrpSpPr", "grpSpPr", "extLst"].includes(n.localName));
  el.children.forEach((c, i) => { if (nodes[i]) { c._xml = serialize(nodes[i]); syncKids(c, nodes[i]); } });
}
function patchTheme(xml, theme) {
  const doc = parseXml(xml);
  const cs = deep(doc, "clrScheme");
  for (const k of SCHEME_KEYS) {
    const el = kid(cs, k); if (!el || !theme.colors[k]) continue;
    while (el.firstChild) el.firstChild.remove();
    el.appendChild(frag(doc, `<a:srgbClr val="${theme.colors[k].replace("#", "")}"/>`));
  }
  const fs = deep(doc, "fontScheme");
  const mj = path(fs, "majorFont", "latin"), mn = path(fs, "minorFont", "latin");
  if (mj && theme.fonts.major) mj.setAttribute("typeface", theme.fonts.major);
  if (mn && theme.fonts.minor) mn.setAttribute("typeface", theme.fonts.minor);
  return serialize(doc);
}

export async function duplicateSlide(deck, slide) {
  const copy = JSON.parse(JSON.stringify(slide, (k, v) => (k === "_preview" ? undefined : v)));
  copy.id = uid(); copy.path = null; copy.sldId = 0; copy.presRel = null; copy._notesPath = null; copy._notes0 = null;
  const reid = (els) => els.forEach((e) => { e.id = uid(); if (e.children) reid(e.children); });
  reid(copy.elements);
  if (slide.path) { copy._dupOf = { path: slide.path }; copy._dupXml = await deck.zip.text(slide.path); }
  else copy._new = true;
  copy._sig = null;
  return copy;
}
