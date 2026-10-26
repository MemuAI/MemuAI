

import { cssColor, applyMods } from "./xml.js";
import { plainText } from "./pptx.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const PT = 12700;

export function geomPath(g, w, h, adj) {
  const m = Math.min(w, h);
  const P = (pts) => "M" + pts.map((p) => p.map((v) => +v.toFixed(2)).join(" ")).join(" L") + " Z";
  switch (g) {
    case "ellipse": case "flowChartConnector": return `M0 ${h / 2} A${w / 2} ${h / 2} 0 1 0 ${w} ${h / 2} A${w / 2} ${h / 2} 0 1 0 0 ${h / 2} Z`;
    case "roundRect": case "flowChartAlternateProcess": { const r = m * (adjVal(adj, 0, 16667) / 100000); return `M${r} 0 H${w - r} A${r} ${r} 0 0 1 ${w} ${r} V${h - r} A${r} ${r} 0 0 1 ${w - r} ${h} H${r} A${r} ${r} 0 0 1 0 ${h - r} V${r} A${r} ${r} 0 0 1 ${r} 0 Z`; }
    case "triangle": case "flowChartExtract": return P([[w / 2, 0], [w, h], [0, h]]);
    case "rtTriangle": return P([[0, 0], [w, h], [0, h]]);
    case "diamond": case "flowChartDecision": return P([[w / 2, 0], [w, h / 2], [w / 2, h], [0, h / 2]]);
    case "parallelogram": case "flowChartInputOutput": { const d = m * 0.25; return P([[d, 0], [w, 0], [w - d, h], [0, h]]); }
    case "trapezoid": { const d = m * 0.25; return P([[d, 0], [w - d, 0], [w, h], [0, h]]); }
    case "pentagon": return P([[w / 2, 0], [w, h * 0.38], [w * 0.81, h], [w * 0.19, h], [0, h * 0.38]]);
    case "hexagon": { const d = m * 0.25; return P([[d, 0], [w - d, 0], [w, h / 2], [w - d, h], [d, h], [0, h / 2]]); }
    case "octagon": { const d = m * 0.29; return P([[d, 0], [w - d, 0], [w, d], [w, h - d], [w - d, h], [d, h], [0, h - d], [0, d]]); }
    case "homePlate": { const d = m * 0.5; return P([[0, 0], [w - d, 0], [w, h / 2], [w - d, h], [0, h]]); }
    case "chevron": { const d = m * 0.5; return P([[0, 0], [w - d, 0], [w, h / 2], [w - d, h], [0, h], [d, h / 2]]); }
    case "plus": case "mathPlus": { const a = m * 0.25, x0 = w / 2 - a, x1 = w / 2 + a, y0 = h / 2 - a, y1 = h / 2 + a; return `M${x0} 0 H${x1} V${y0} H${w} V${y1} H${x1} V${h} H${x0} V${y1} H0 V${y0} H${x0} Z`; }
    case "star5": { const pts = []; for (let i = 0; i < 10; i++) { const r = i % 2 ? 0.38 : 0.5, a = -Math.PI / 2 + i * Math.PI / 5; pts.push([w / 2 + Math.cos(a) * w * r, h * 0.52 + Math.sin(a) * h * r * 1.05]); } return P(pts); }
    case "star6": { const pts = []; for (let i = 0; i < 12; i++) { const r = i % 2 ? 0.29 : 0.5, a = -Math.PI / 2 + i * Math.PI / 6; pts.push([w / 2 + Math.cos(a) * w * r, h / 2 + Math.sin(a) * h * r]); } return P(pts); }
    case "rightArrow": { const t = h * 0.25, d = Math.min(w, h * 0.5); return P([[0, t], [w - d, t], [w - d, 0], [w, h / 2], [w - d, h], [w - d, h - t], [0, h - t]]); }
    case "leftArrow": { const t = h * 0.25, d = Math.min(w, h * 0.5); return P([[w, t], [d, t], [d, 0], [0, h / 2], [d, h], [d, h - t], [w, h - t]]); }
    case "upArrow": { const t = w * 0.25, d = Math.min(h, w * 0.5); return P([[t, h], [t, d], [0, d], [w / 2, 0], [w, d], [w - t, d], [w - t, h]]); }
    case "downArrow": { const t = w * 0.25, d = Math.min(h, w * 0.5); return P([[t, 0], [t, h - d], [0, h - d], [w / 2, h], [w, h - d], [w - t, h - d], [w - t, 0]]); }
    case "leftRightArrow": { const t = h * 0.25, d = Math.min(w / 2, h * 0.5); return P([[0, h / 2], [d, 0], [d, t], [w - d, t], [w - d, 0], [w, h / 2], [w - d, h], [w - d, h - t], [d, h - t], [d, h]]); }
    case "wedgeRectCallout": case "wedgeRoundRectCallout": return `M0 0 H${w} V${h * 0.78} H${w * 0.42} L${w * 0.2} ${h} L${w * 0.25} ${h * 0.78} H0 Z`;
    case "snip1Rect": { const d = m * 0.17; return P([[0, 0], [w - d, 0], [w, d], [w, h], [0, h]]); }
    case "round2SameRect": { const r = m * 0.17; return `M${r} 0 H${w - r} A${r} ${r} 0 0 1 ${w} ${r} V${h} H0 V${r} A${r} ${r} 0 0 1 ${r} 0 Z`; }
    case "heart": return `M${w / 2} ${h * 0.25} C${w * 0.5} ${-h * 0.1} ${-w * 0.1} ${h * 0.05} ${w * 0.03} ${h * 0.38} C${w * 0.12} ${h * 0.62} ${w * 0.4} ${h * 0.78} ${w / 2} ${h} C${w * 0.6} ${h * 0.78} ${w * 0.88} ${h * 0.62} ${w * 0.97} ${h * 0.38} C${w * 1.1} ${h * 0.05} ${w * 0.5} ${-h * 0.1} ${w / 2} ${h * 0.25} Z`;
    case "cloud": case "cloudCallout": return `M${w * 0.2} ${h * 0.85} C${-w * 0.05} ${h * 0.85} ${-w * 0.02} ${h * 0.45} ${w * 0.17} ${h * 0.45} C${w * 0.12} ${h * 0.1} ${w * 0.5} ${h * 0.02} ${w * 0.55} ${h * 0.25} C${w * 0.65} ${0} ${w * 0.95} ${h * 0.12} ${w * 0.85} ${h * 0.42} C${w * 1.05} ${h * 0.5} ${w * 1.02} ${h * 0.9} ${w * 0.78} ${h * 0.88} C${w * 0.7} ${h * 1.02} ${w * 0.35} ${h * 1.02} ${w * 0.2} ${h * 0.85} Z`;
    case "donut": { const t = m * 0.25; return `M0 ${h / 2} A${w / 2} ${h / 2} 0 1 0 ${w} ${h / 2} A${w / 2} ${h / 2} 0 1 0 0 ${h / 2} Z M${t} ${h / 2} A${w / 2 - t} ${h / 2 - t} 0 1 1 ${w - t} ${h / 2} A${w / 2 - t} ${h / 2 - t} 0 1 1 ${t} ${h / 2} Z`; }
    case "can": case "flowChartMagneticDisk": { const r = h * 0.1; return `M0 ${r} A${w / 2} ${r} 0 0 1 ${w} ${r} V${h - r} A${w / 2} ${r} 0 0 1 0 ${h - r} Z M0 ${r} A${w / 2} ${r} 0 0 0 ${w} ${r}`; }
    case "flowChartTerminator": { const r = h / 2; return `M${r} 0 H${w - r} A${r} ${r} 0 0 1 ${w - r} ${h} H${r} A${r} ${r} 0 0 1 ${r} 0 Z`; }
    case "line": case "straightConnector1": case "bentConnector3": case "curvedConnector3": return `M0 0 L${w} ${h}`;
    default: return `M0 0 H${w} V${h} H0 Z`;
  }
}
function adjVal(adj, i, dflt) { const f = adj?.[i]; const m = /val\s+(-?\d+)/.exec(f || ""); return m ? Number(m[1]) : dflt; }
export const isLineGeom = (g) => ["line", "straightConnector1", "bentConnector3", "curvedConnector3"].includes(g);

const ROMAN = [["m", 1000], ["cm", 900], ["d", 500], ["cd", 400], ["c", 100], ["xc", 90], ["l", 50], ["xl", 40], ["x", 10], ["ix", 9], ["v", 5], ["iv", 4], ["i", 1]];
function roman(n) { let s = ""; for (const [r, v] of ROMAN) while (n >= v) { s += r; n -= v; } return s; }
export function autoNum(type, n) {
  const alpha = (k) => { let s = ""; k--; do { s = String.fromCharCode(97 + (k % 26)) + s; k = Math.floor(k / 26) - 1; } while (k >= 0); return s; };
  const base = type.startsWith("alphaLc") ? alpha(n) : type.startsWith("alphaUc") ? alpha(n).toUpperCase() : type.startsWith("romanLc") ? roman(n) : type.startsWith("romanUc") ? roman(n).toUpperCase() : String(n);
  if (type.endsWith("ParenBoth")) return `(${base})`;
  if (type.endsWith("ParenR")) return `${base})`;
  if (type.endsWith("Plain")) return base;
  return base + ".";
}

export function textHtml(text, styles, ctx) {
  const { s, font, color, scale = 1, lnRed = 0 } = ctx;
  const counters = [];
  let html = "";
  const paras = text?.paras?.length ? text.paras : [{ runs: [] }];
  let pi = -1;
  for (const para of paras) {
    pi++;
    const lvl = Math.max(0, Math.min(8, para.lvl || 0));
    const st = styles[lvl] || styles[0];
    const P = { ...st.p, ...stripUndef(para) };
    const R0 = st.r;
    const marL = (P.marL || 0) * s, indent = (P.indent || 0) * s;
    const firstRun = para.runs.find((r) => !r.br) || {};
    const szPt = (firstRun.sz ?? para.endSz ?? R0.sz ?? 18) * scale;
    let lh = "1.2";
    if (typeof P.lnSpc === "number") lh = String(Math.max(0.5, (P.lnSpc / 100) * 1.2 * (1 - lnRed / 100)));
    else if (P.lnSpc?.pts) lh = (P.lnSpc.pts * PT * s * scale).toFixed(2) + "px";
    const bef = typeof P.spcBef === "number" ? P.spcBef * PT * s * scale : P.spcBef?.pct ? (P.spcBef.pct / 100) * szPt * PT * s : 0;
    const aft = typeof P.spcAft === "number" ? P.spcAft * PT * s * scale : 0;
    const algn = { l: "left", ctr: "center", r: "right", just: "justify", dist: "justify" }[P.algn] || "left";
    const empty = !para.runs.some((r) => r.t && !r.br);
    let bullet = "";
    for (let i = lvl + 1; i < 9; i++) counters[i] = 0;
    if (P.bullet && P.bullet !== "none" && !empty) {
      const bc = P.buClr ? color(P.buClr) : null;
      const bsz = P.buSz ? P.buSz / 100 : 1;
      let ch;
      if (P.bullet.num) { counters[lvl] = (counters[lvl] || (P.bullet.start || 1) - 1) + 1; ch = autoNum(P.bullet.num, counters[lvl]); }
      else ch = mapBulletChar(P.bullet.char);
      const runStyle = runCss({ ...R0, ...stripUndef(firstRun), u: false, s: false }, ctx, true);
      bullet = `<span class="sl-bu"${ctx.edit ? ' contenteditable="false"' : ""} style="${runStyle}${bc ? `color:${bc};` : ""}font-size:${(szPt * bsz * PT * s).toFixed(2)}px;width:${Math.max(0, -indent).toFixed(2)}px;min-width:${(szPt * 0.6 * PT * s).toFixed(2)}px">${esc(ch)}</span>`;
    } else counters[lvl] = 0;
    let runs = "";
    let ri = -1;
    for (const r of para.runs) {
      ri++;
      if (r.br) { runs += "<br>"; continue; }
      const R = { ...R0, ...stripUndef(r) };
      if (r.href) { if (!r.color) R.color = "scheme:hlink"; if (r.u === undefined) R.u = true; }
      let t = esc(r.fld === "slidenum" && ctx.slideNo ? String(ctx.slideNo) : r.t);
      if (R.cap === "all") t = t.toUpperCase();
      if (!t && ctx.edit) continue;
      runs += `<span${ctx.edit ? ` data-r="${ri}"` : ""} style="${runCss(R, ctx)}">${t || ""}</span>`;
    }
    if (empty) runs = ctx.edit ? `<span data-r="0" style="${runCss({ ...R0, ...stripUndef(firstRun), sz: para.endSz ?? firstRun.sz ?? R0.sz }, ctx)}"><br></span>` : `<span style="${runCss({ ...R0, sz: para.endSz ?? firstRun.sz ?? R0.sz }, ctx)}">&#8203;</span>`;
    const pad = `padding-left:${Math.max(0, marL).toFixed(2)}px;text-indent:${(bullet ? Math.min(0, indent) : Math.max(indent, -marL)).toFixed(2)}px;`;
    html += `<div class="sl-p"${ctx.edit ? ` data-p="${pi}"` : ""} style="text-align:${algn};line-height:${lh};margin-top:${bef.toFixed(2)}px;margin-bottom:${aft.toFixed(2)}px;${pad}font-size:${(szPt * PT * s).toFixed(2)}px">${bullet}${runs}</div>`;
  }
  return html;
}
function mapBulletChar(c) { return { "§": "▪", "Ø": "➢", "ü": "✓", "Ÿ": "•", "q": "❑", "v": "❖", "n": "■", "l": "●", "o": "○", "Ú": "➢" }[c] || c || "•"; }
function stripUndef(o) { const r = {}; for (const k in o) if (o[k] !== undefined && k !== "runs" && !k.startsWith("_")) r[k] = o[k]; return r; }
function runCss(R, ctx, bulletOnly) {
  const { s, font, color, scale = 1 } = ctx;
  let css = `font-size:${((R.sz ?? 18) * scale * PT * s * (R.base ? 0.65 : 1)).toFixed(2)}px;`;
  const fam = font(R.font);
  if (fam) css += `font-family:'${fam.replace(/'/g, "")}',var(--sl-fallback);`;
  if (R.b) css += "font-weight:700;";
  if (R.i) css += "font-style:italic;";
  const deco = [R.u && "underline", R.s && "line-through"].filter(Boolean);
  if (deco.length && !bulletOnly) css += `text-decoration:${deco.join(" ")};`;
  if (R.color) css += `color:${color(R.color)};`;
  if (R.hl && !bulletOnly) css += `background:${color(R.hl)};`;
  if (R.base && !bulletOnly) css += `vertical-align:${R.base === "sup" ? "super" : "sub"};`;
  if (R.spc && !bulletOnly) css += `letter-spacing:${(R.spc * PT * s).toFixed(2)}px;`;
  if (R.cap === "small") css += "font-variant:small-caps;";
  return css;
}

export function chartSvg(chart, w, h, palette, fontPx, textColor) {
  const series = chart.series || [], cats = chart.cats || [];
  const col = (i) => series[i]?.color ? palette.color(series[i].color) : palette.accent(i);

  const W0 = w, H0 = h, kz = fontPx < 6 ? 6 / Math.max(0.3, fontPx) : 1; w *= kz; h *= kz;
  const fs = Math.max(6, fontPx * kz);
  let svg = `<svg class="sl-chart" viewBox="0 0 ${w} ${h}" width="${W0}" height="${H0}" style="font-size:${fs}px;font-family:inherit">`;
  const title = chart.title ? fs * 1.8 : 0;
  if (chart.title) svg += `<text x="${w / 2}" y="${fs * 1.3}" text-anchor="middle" fill="${textColor}" style="font-size:${fs * 1.25}px;font-weight:600">${esc(chart.title)}</text>`;
  if (chart.unsupported && !series.length) return svg + `<rect x="1" y="1" width="${w - 2}" height="${h - 2}" fill="none" stroke="${textColor}" stroke-dasharray="4 3" opacity=".4"/></svg>`;
  if (chart.type === "pie" || chart.type === "doughnut") {
    const vals = (series[0]?.vals || []).map((v) => Math.max(0, Number(v) || 0)), total = vals.reduce((a, b) => a + b, 0) || 1;
    const legendW = Math.min(w * 0.35, fs * 9);
    const cx = (w - legendW) / 2, cy = title + (h - title) / 2, r = Math.max(4, Math.min(cx, (h - title) / 2) - fs * 0.6);
    let a0 = -Math.PI / 2;
    vals.forEach((v, i) => {
      const a1 = a0 + (v / total) * Math.PI * 2;
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0), x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      svg += vals.length === 1 ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${palette.accent(i)}"/>` : `<path d="M${cx} ${cy} L${x0} ${y0} A${r} ${r} 0 ${large} 1 ${x1} ${y1} Z" fill="${palette.accent(i)}" stroke="var(--sl-chart-sep,#fff)" stroke-width="${Math.max(0.5, r / 90)}"/>`;
      a0 = a1;
    });
    if (chart.type === "doughnut") svg += `<circle cx="${cx}" cy="${cy}" r="${r * 0.55}" fill="var(--sl-chart-hole,#fff)"/>`;
    cats.forEach((c, i) => { const y = title + fs * 1.6 * i + fs; svg += `<rect x="${w - legendW + fs * 0.3}" y="${y - fs * 0.75}" width="${fs * 0.8}" height="${fs * 0.8}" rx="2" fill="${palette.accent(i)}"/><text x="${w - legendW + fs * 1.5}" y="${y}" fill="${textColor}">${esc(c)}</text>`; });
    return svg + "</svg>";
  }
  const horiz = chart.type === "bar";
  const n = Math.max(cats.length, ...series.map((s) => s.vals.length), 1);
  const stacked = !!chart.stacked;
  let max = 0, min = 0;
  for (let i = 0; i < n; i++) {
    if (stacked) { const sum = series.reduce((a, s) => a + Math.max(0, Number(s.vals[i]) || 0), 0); max = Math.max(max, sum); }
    else for (const s of series) { max = Math.max(max, Number(s.vals[i]) || 0); min = Math.min(min, Number(s.vals[i]) || 0); }
  }
  const step = niceStep((max - min) || 1);
  max = Math.ceil(max / step) * step || step; min = Math.floor(min / step) * step;
  const legend = series.length > 1 || series[0]?.name ? fs * 2 : 0;
  const L = horiz ? fs * 6 : fs * 3.2, T = title + fs * 0.5, B = (horiz ? fs * 1.8 : fs * 2) + legend, R = fs;
  const pw = Math.max(10, w - L - R), ph = Math.max(10, h - T - B);
  const val2 = (v) => (horiz ? L + ((v - min) / (max - min)) * pw : T + ph - ((v - min) / (max - min)) * ph);
  for (let v = min; v <= max + 1e-9; v += step) {
    const p = val2(v);
    svg += horiz ? `<line x1="${p}" y1="${T}" x2="${p}" y2="${T + ph}" stroke="${textColor}" opacity=".15"/><text x="${p}" y="${T + ph + fs * 1.3}" text-anchor="middle" fill="${textColor}" opacity=".75">${fmtNum(v)}</text>`
      : `<line x1="${L}" y1="${p}" x2="${L + pw}" y2="${p}" stroke="${textColor}" opacity=".15"/><text x="${L - fs * 0.4}" y="${p + fs * 0.35}" text-anchor="end" fill="${textColor}" opacity=".75">${fmtNum(v)}</text>`;
  }
  const band = (horiz ? ph : pw) / n;
  for (let i = 0; i < n; i++) {
    const c = cats[i] ?? "";
    svg += horiz ? `<text x="${L - fs * 0.4}" y="${T + band * (i + 0.5) + fs * 0.35}" text-anchor="end" fill="${textColor}">${esc(trunc(c, 14))}</text>`
      : `<text x="${L + band * (i + 0.5)}" y="${T + ph + fs * 1.3}" text-anchor="middle" fill="${textColor}">${esc(trunc(c, Math.max(4, Math.floor(band / (fs * 0.55)))))}</text>`;
  }
  if (chart.type === "line" || chart.type === "area") {
    series.forEach((s, k) => {
      const pts = Array.from({ length: n }, (_, i) => [L + band * (i + 0.5), val2(Number(s.vals[i]) || 0)]);
      if (chart.type === "area") svg += `<path d="M${pts[0][0]} ${val2(Math.max(min, 0))} L${pts.map((p) => p.join(" ")).join(" L")} L${pts[pts.length - 1][0]} ${val2(Math.max(min, 0))} Z" fill="${col(k)}" opacity=".55"/>`;
      svg += `<polyline points="${pts.map((p) => p.join(",")).join(" ")}" fill="none" stroke="${col(k)}" stroke-width="${Math.max(1.5, fs / 5)}" stroke-linejoin="round"/>`;
      if (chart.type === "line") for (const p of pts) svg += `<circle cx="${p[0]}" cy="${p[1]}" r="${Math.max(2, fs / 4)}" fill="${col(k)}"/>`;
    });
  } else {
    const gap = band * 0.25, inner = band - gap, k = stacked ? 1 : Math.max(1, series.length), bw = inner / k;
    for (let i = 0; i < n; i++) {
      let acc = 0;
      series.forEach((s, j) => {
        const v = Number(s.vals[i]) || 0;
        const from = stacked ? acc : Math.max(min, 0), to = stacked ? acc + Math.max(0, v) : v;
        acc = stacked ? to : acc;
        const a = val2(from), b = val2(to), off = band * i + gap / 2 + (stacked ? 0 : bw * j);
        if (horiz) svg += `<rect x="${Math.min(a, b)}" y="${T + off}" width="${Math.abs(b - a)}" height="${bw * 0.92}" fill="${col(j)}" rx="${Math.min(3, bw / 6)}"/>`;
        else svg += `<rect x="${L + off}" y="${Math.min(a, b)}" width="${bw * 0.92}" height="${Math.abs(b - a)}" fill="${col(j)}" rx="${Math.min(3, bw / 6)}"/>`;
      });
    }
  }
  if (legend) {
    let x = L; const y = h - fs * 0.6;
    series.forEach((s, k) => { svg += `<rect x="${x}" y="${y - fs * 0.75}" width="${fs * 0.8}" height="${fs * 0.8}" rx="2" fill="${col(k)}"/><text x="${x + fs * 1.1}" y="${y}" fill="${textColor}">${esc(trunc(s.name, 18))}</text>`; x += fs * 2.2 + textW(trunc(s.name, 18), fs); });
  }
  return svg + "</svg>";
}
function niceStep(range) { const raw = range / 5, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p; return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p; }
function fmtNum(v) { return Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(0) + "k" : String(+v.toFixed(2)); }
let _mc = null;

function textW(str, fs) { try { _mc = _mc || document.createElement("canvas").getContext("2d"); _mc.font = `${fs}px "Segoe UI", Calibri, Arial, sans-serif`; return _mc.measureText(String(str ?? "")).width; } catch { return String(str ?? "").length * fs * 0.62; } }
function trunc(s, n) { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

function fillCss(deck, slide, fill, styleColor) {
  if (fill === null) return "none";
  if (fill?.color) return deck.color(fill.color, slide);
  if (fill?.grad) return "url(#g)";
  if (fill?.image) return "none";
  if (fill === undefined && styleColor) return deck.color(styleColor, slide);
  return "none";
}
function gradDef(deck, slide, grad, id) {
  const a = ((grad.ang ?? 90) * Math.PI) / 180, x = Math.cos(a), y = Math.sin(a);
  const stops = grad.stops.map((st) => `<stop offset="${st.pos}%" stop-color="${deck.color(st.color, slide)}"/>`).join("");
  return grad.path ? `<radialGradient id="${id}">${stops}</radialGradient>` : `<linearGradient id="${id}" x1="${(0.5 - x / 2).toFixed(3)}" y1="${(0.5 - y / 2).toFixed(3)}" x2="${(0.5 + x / 2).toFixed(3)}" y2="${(0.5 + y / 2).toFixed(3)}">${stops}</linearGradient>`;
}
export function gradCss(deck, slide, grad) {
  return `linear-gradient(${(grad.ang ?? 90) + 90}deg, ${grad.stops.map((s) => `${deck.color(s.color, slide)} ${s.pos}%`).join(", ")})`;
}
let gid = 0;

export function renderElement(deck, slide, el, s, opts = {}) {
  const box = deck.boxOf(slide, el);
  const d = document.createElement("div");
  d.className = "sl-el sl-" + el.kind;
  d.dataset.id = el.id;
  const W = Math.max(0, box.w * s), H = Math.max(0, box.h * s);
  d.style.cssText = `left:${(box.x * s).toFixed(2)}px;top:${(box.y * s).toFixed(2)}px;width:${W.toFixed(2)}px;height:${H.toFixed(2)}px;`
    + (el.rot || el.flipH || el.flipV ? `transform:rotate(${el.rot || 0}deg)${el.flipH ? " scaleX(-1)" : ""}${el.flipV ? " scaleY(-1)" : ""};` : "");
  if (el.hidden && !opts.editing) d.style.display = "none";
  const ph = el.ph ? deck.phInfo(slide, el) : null;
  if (el.kind === "shape") {
    const lineG = isLineGeom(el.geom);
    const st = el._st || {};
    const fill = el.fill !== undefined ? el.fill : ph?.fill;
    const line = el.line !== undefined ? el.line : ph?.line;
    const fc = lineG ? "none" : fillCss(deck, slide, fill, st.fill);
    const lc = line?.none ? null : line?.color ? deck.color(line.color, slide) : line && line.w !== undefined && st.line ? deck.color(st.line, slide) : !line && st.line ? deck.color(st.line, slide) : line && !line.color && st.line ? deck.color(st.line, slide) : lineG && !line?.none ? deck.color(line?.color || "scheme:tx1", slide) : null;
    const lw = lc ? Math.max(0.6, ((line?.w ?? (st.lineW || 12700)) * s)) : 0;
    if (fc !== "none" || lc || fill?.grad || fill?.image || el.geom === "custom") {
      const id = "g" + (++gid);
      let defs = "";
      if (fill?.grad) defs += gradDef(deck, slide, fill.grad, id);
      if (line?.head || line?.tail) defs += `<marker id="${id}h" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="${Math.max(3, 9 - lw)}" markerHeight="${Math.max(3, 9 - lw)}" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="${lc}"/></marker>`;
      const dash = line?.dash ? ({ dash: `${lw * 4} ${lw * 3}`, sysDash: `${lw * 3} ${lw}`, sysDot: `${lw} ${lw}`, dot: `${lw} ${lw * 2}`, lgDash: `${lw * 8} ${lw * 3}`, dashDot: `${lw * 4} ${lw * 2} ${lw} ${lw * 2}` }[line.dash] || `${lw * 4} ${lw * 3}`) : "";
      let paths;
      if (el.geom === "custom" && el._cust?.length) paths = el._cust.map((p) => `<path d="${p.d}" transform="scale(${p.w ? W / p.w : 1} ${p.h ? H / p.h : 1})" vector-effect="non-scaling-stroke" fill="${p.fill ? (fill?.grad ? `url(#${id})` : fc) : "none"}" ${lc && p.stroke ? `stroke="${lc}" stroke-width="${lw}"` : ""} ${dash ? `stroke-dasharray="${dash}"` : ""}/>`).join("");
      else paths = `<path d="${geomPath(el.geom || "rect", W, H, el.adj)}" fill="${fill?.grad ? `url(#${id})` : fc}" ${lc ? `stroke="${lc}" stroke-width="${lw}"` : ""} ${dash ? `stroke-dasharray="${dash}"` : ""} ${line?.tail ? `marker-end="url(#${id}h)"` : ""} ${line?.head ? `marker-start="url(#${id}h)"` : ""} ${lineG ? 'stroke-linecap="round"' : ""}/>`;
      d.insertAdjacentHTML("beforeend", `<svg class="sl-geom" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" overflow="visible"><defs>${defs}</defs>${paths}</svg>`);
      if (fill?.image) d.insertAdjacentHTML("afterbegin", `<img class="sl-fillimg" src="${esc(deck.mediaUrlSync(fill.image))}" alt="" draggable="false">`);
    }
    if (el.text || el.ph) renderText(deck, slide, el, d, s, opts, ph);
  } else if (el.kind === "pic") {
    const c = el.pic.crop || { l: 0, t: 0, r: 0, b: 0 };
    const fw = 100 - c.l - c.r, fh = 100 - c.t - c.b;
    const src = deck.mediaUrlSync(el.pic.media);
    const radius = el.geom === "ellipse" ? "border-radius:50%;" : el.geom === "roundRect" ? `border-radius:${Math.min(W, H) * 0.16}px;` : "";
    const lc = el.line && !el.line.none && el.line.color ? `box-shadow:inset 0 0 0 ${Math.max(1, (el.line.w || 12700) * s)}px ${deck.color(el.line.color, slide)};` : "";
    d.innerHTML = `<div class="sl-crop" style="${radius}${lc}">${src ? `<img src="${esc(src)}" alt="${esc(el.descr || "")}" draggable="false" style="width:${(100 / (fw / 100)).toFixed(3)}%;height:${(100 / (fh / 100)).toFixed(3)}%;left:${(-c.l / fw * 100).toFixed(3)}%;top:${(-c.t / fh * 100).toFixed(3)}%">` : `<div class="sl-missing">${el.media ? "▶" : "?"}</div>`}</div>`;
    if (el.media) d.insertAdjacentHTML("beforeend", '<div class="sl-media-badge">▶</div>');
  } else if (el.kind === "table") {
    d.appendChild(renderTable(deck, slide, el, s, opts));
  } else if (el.kind === "chart") {
    const th = deck.themeOf(slide);
    const palette = { accent: (i) => cssColor("scheme:accent" + ((i % 6) + 1), th), color: (c) => cssColor(c, th) };
    const textColor = cssColor("scheme:tx1", th);
    const fontPx = Math.max(5, 14 * PT * s);
    d.innerHTML = chartSvg(el.chart, W, H, palette, fontPx, textColor);
    d.style.fontFamily = `'${deck.font("+mn-lt", slide)}',var(--sl-fallback)`;
  } else if (el.kind === "group") {
    const ch = el._ch || { x: 0, y: 0, w: el.w, h: el.h };
    const sx = ch.w ? (el.w / ch.w) : 1, sy = ch.h ? (el.h / ch.h) : 1;
    const inner = document.createElement("div");
    inner.className = "sl-group-inner";
    inner.style.cssText = `position:absolute;left:0;top:0;width:${(ch.w * s).toFixed(2)}px;height:${(ch.h * s).toFixed(2)}px;transform-origin:0 0;transform:scale(${sx},${sy}) translate(${(-ch.x * s).toFixed(2)}px,${(-ch.y * s).toFixed(2)}px)`;
    for (const c of el.children || []) inner.appendChild(renderElement(deck, slide, c, s, { ...opts, inGroup: true }));
    d.appendChild(inner);
  } else {
    if (el._preview) { const p = renderElement(deck, slide, { ...el._preview, x: el.x, y: el.y, w: el.w, h: el.h, rot: 0 }, s, opts); p.style.left = "0"; p.style.top = "0"; d.appendChild(p); }
    else d.innerHTML = `<div class="sl-raw">${esc(el.label || "object")}</div>`;
  }
  return d;
}

function renderText(deck, slide, el, d, s, opts, ph) {
  const text = el.text || { paras: [{ runs: [] }] };
  const styles = deck.levelStyles(slide, el);
  const empty = !plainText(text).trim();
  const isPh = !!el.ph;
  if (empty && isPh && !opts.editing) return;
  const ins = { lIns: 91440, tIns: 45720, rIns: 91440, bIns: 45720, ...(ph?.ins || {}) };
  for (const k of ["lIns", "tIns", "rIns", "bIns"]) if (text[k] !== undefined) ins[k] = text[k];
  const anchor = text.anchor || ph?.anchor || "t";
  const t = document.createElement("div");
  t.className = "sl-text" + (empty && isPh ? " sl-prompt" : "");
  t.dataset.anchor = anchor;
  const vert = text.vert && text.vert !== "horz";
  t.style.cssText = `padding:${(ins.tIns * s).toFixed(2)}px ${(ins.rIns * s).toFixed(2)}px ${(ins.bIns * s).toFixed(2)}px ${(ins.lIns * s).toFixed(2)}px;justify-content:${{ t: "flex-start", ctr: "center", b: "flex-end" }[anchor] || "flex-start"};`
    + (text.wrap === "none" ? "white-space:pre;" : "") + (vert ? `writing-mode:vertical-rl;${text.vert === "vert270" ? "transform:rotate(180deg);" : ""}` : "");

  const fit = text.autofit?.norm || (text.autofit === undefined && !!(el.ph ? deck.phChain(slide, el).find((p) => p.autofit !== undefined)?.autofit : false));
  const scale = text.autofit?.scale ? text.autofit.scale / 100 : 1;
  const ctx = { s, scale, lnRed: text.autofit?.lnRed || 0, font: (f) => deck.font(f, slide), color: (c) => deck.color(c, slide), slideNo: opts.slideNo };
  if (empty && isPh) {
    const prompt = opts.prompt?.(el, ph) || ph?.prompt || "";
    const paras = [{ runs: [{ t: prompt }] }];
    t.innerHTML = textHtml({ paras }, styles.map((x) => ({ p: { ...x.p, bullet: "none" }, r: x.r })), ctx);
  } else t.innerHTML = textHtml(text, styles, ctx);
  if (fit && scale === 1) t.dataset.fit = "1";
  d.appendChild(t);
}

export function fitTexts(root) {
  for (const t of root.querySelectorAll(".sl-text[data-fit='1']")) {
    if (t.scrollHeight <= t.clientHeight + 1) continue;
    let k = 1;
    const base = Array.from(t.querySelectorAll("[style*='font-size']"), (e) => [e, parseFloat(e.style.fontSize)]);
    for (let i = 0; i < 8 && t.scrollHeight > t.clientHeight + 1; i++) {
      k *= 0.9;
      for (const [e, v] of base) e.style.fontSize = (v * k).toFixed(2) + "px";
    }
  }
}

function renderTable(deck, slide, el, s, opts) {
  const t = el.table, styled = !!t.style;
  const table = document.createElement("table");
  table.className = "sl-table";
  const th = deck.themeOf(slide);
  const acc = cssColor("scheme:accent1", th);
  const tint1 = cssColor({ c: "scheme:accent1", mods: [{ n: "tint", v: 40000 }] }, th), tint2 = cssColor({ c: "scheme:accent1", mods: [{ n: "tint", v: 20000 }] }, th);
  const total = t.cols.reduce((a, b) => a + b, 0) || 1;
  table.innerHTML = `<colgroup>${t.cols.map((w) => `<col style="width:${(w / total * 100).toFixed(3)}%">`).join("")}</colgroup>`;
  const tbody = document.createElement("tbody");
  t.rows.forEach((row, ri) => {
    const tr = document.createElement("tr");
    tr.style.height = (row.h * s).toFixed(2) + "px";
    row.cells.forEach((cell, ci) => {
      if (cell.hMerge || cell.vMerge) return;
      const td = document.createElement("td");
      td.dataset.r = ri; td.dataset.c = ci;
      if (cell.gridSpan > 1) td.colSpan = cell.gridSpan;
      if (cell.rowSpan > 1) td.rowSpan = cell.rowSpan;
      const header = styled && t.firstRow && ri === 0;
      let bg = cell.fill === null ? "transparent" : cell.fill?.color ? deck.color(cell.fill.color, slide) : styled ? (header ? acc : t.bandRow ? ((ri - (t.firstRow ? 1 : 0)) % 2 ? tint2 : tint1) : tint2) : "transparent";
      td.style.background = bg;
      td.style.borderColor = styled ? "#fff" : cssColor("scheme:tx1", th);
      td.style.padding = `${(45720 * s).toFixed(2)}px ${(91440 * s).toFixed(2)}px`;
      td.style.verticalAlign = { ctr: "middle", b: "bottom" }[cell.anchor] || "top";
      const lv = Array.from({ length: 9 }, () => ({ p: { algn: "l" }, r: { sz: 18, color: header ? "scheme:lt1" : "scheme:tx1", font: "+mn-lt", b: header || undefined } }));
      td.innerHTML = textHtml(cell.text, lv, { s, font: (f) => deck.font(f, slide), color: (c) => deck.color(c, slide) });
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}

export function backgroundCss(deck, slide) {
  const l = deck.layoutOf(slide), m = deck.masterOf(slide);
  const bg = slide.bg ?? l?.bg ?? m?.bg;
  const th = deck.themeOf(slide);
  if (!bg) return cssColor("scheme:bg1", th) || "#fff";
  if (bg.none) return "transparent";
  if (bg.color && !bg.ref) return deck.color(bg.color, slide);
  if (bg.grad) return gradCss(deck, slide, bg.grad);
  if (bg.image) return `center/cover no-repeat url("${deck.mediaUrlSync(bg.image)}")`;
  if (bg.ref) {
    const style = th.bgStyles?.[(bg.ref - 1001)];
    if (style?.grad) return gradCss(deck, slide, { ...style.grad, stops: style.grad.stops.map((st) => ({ ...st, color: phColor(st.color, bg.color) })) });
    return deck.color(bg.color, slide);
  }
  return cssColor("scheme:bg1", th);
}
function phColor(c, ph) { if (typeof c === "object" && c?.c === "scheme:phClr") return { c: typeof ph === "string" ? ph : ph?.c, mods: [...(ph?.mods || []), ...c.mods] }; if (c === "scheme:phClr") return ph; return c; }

export function renderSlide(deck, slide, opts = {}) {
  const s = opts.scale;
  const root = document.createElement("div");
  root.className = "sl-slide";
  root.style.cssText = `width:${(deck.w * s).toFixed(2)}px;height:${(deck.h * s).toFixed(2)}px;background:${backgroundCss(deck, slide)}`;
  const l = deck.layoutOf(slide), m = deck.masterOf(slide);
  if (!slide.noMasterSp) {
    if (l?.showMasterSp !== false) for (const e of m?.decor || []) root.appendChild(renderElement(deck, slide, e, s, { slideNo: opts.slideNo })).classList.add("sl-decor");
    for (const e of l?.decor || []) root.appendChild(renderElement(deck, slide, e, s, { slideNo: opts.slideNo })).classList.add("sl-decor");
  }
  for (const el of slide.elements) {
    const node = renderElement(deck, slide, el, s, opts);
    if (opts.hide?.has(el.id)) node.style.visibility = "hidden";
    root.appendChild(node);
  }
  return root;
}
