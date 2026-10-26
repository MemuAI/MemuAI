

import { XML_HEAD, escXml } from "./xml.js";

export const SIZES = { "16:9": [12192000, 6858000], "4:3": [9144000, 6858000], "16:10": [12192000, 7620000] };

const C = (dk1, lt1, dk2, lt2, a1, a2, a3, a4, a5, a6, hl = a1, fhl = a5) => ({ dk1, lt1, dk2, lt2, accent1: a1, accent2: a2, accent3: a3, accent4: a4, accent5: a5, accent6: a6, hlink: hl, folHlink: fhl });
export const THEMES = [
  { id: "memu", ru: "MemuAI", en: "MemuAI", dark: true, decor: "glow", fonts: ["Segoe UI Semibold", "Segoe UI"],
    colors: C("#16161A", "#F4F1EC", "#24242B", "#E9E4DC", "#EFA58B", "#8BB8EF", "#9BD5A4", "#F5C84C", "#C79BEF", "#7FD1D1") },
  { id: "clean", ru: "Чистая", en: "Clean", dark: false, decor: "bar", fonts: ["Segoe UI Semibold", "Segoe UI"],
    colors: C("#1F2328", "#FFFFFF", "#2E3A4A", "#EEF1F5", "#2F6FEB", "#E5534B", "#2DA44E", "#BF8700", "#8250DF", "#1B7C83") },
  { id: "paper", ru: "Бумага", en: "Paper", dark: false, decor: "rule", fonts: ["Georgia", "Calibri"],
    colors: C("#2B2622", "#FBF8F2", "#4A3F35", "#F1EADF", "#A84B30", "#4F7A5A", "#3E6A92", "#C08A2B", "#7A4E8C", "#5F7C7A") },
  { id: "ocean", ru: "Океан", en: "Ocean", dark: false, decor: "band", fonts: ["Segoe UI Semibold", "Segoe UI"],
    colors: C("#0E2A3B", "#FFFFFF", "#0B4F6C", "#E6F2F7", "#0B7A9E", "#01BAEF", "#20BF55", "#F2A541", "#5C6BC0", "#26A69A") },
  { id: "forest", ru: "Лес", en: "Forest", dark: false, decor: "band", fonts: ["Cambria", "Calibri"],
    colors: C("#1E2B20", "#FAFBF7", "#2F4A33", "#EAF0E4", "#3E7C4A", "#A3B86C", "#D9A441", "#5B8E7D", "#8C6B4F", "#4F6D7A") },
  { id: "sunset", ru: "Закат", en: "Sunset", dark: false, decor: "glow", fonts: ["Trebuchet MS", "Segoe UI"],
    colors: C("#2D1B2E", "#FFFAF5", "#5B2A45", "#FCEDE3", "#E4572E", "#F3A712", "#A8C686", "#669BBC", "#B5179E", "#29335C") },
  { id: "midnight", ru: "Полночь", en: "Midnight", dark: true, decor: "bar", fonts: ["Segoe UI Semibold", "Segoe UI"],
    colors: C("#0D1117", "#E6EDF3", "#161B22", "#C9D1D9", "#58A6FF", "#F778BA", "#3FB950", "#D29922", "#BC8CFF", "#39C5CF") },
  { id: "mono", ru: "Монохром", en: "Mono", dark: false, decor: "none", fonts: ["Arial", "Arial"],
    colors: C("#111111", "#FFFFFF", "#444444", "#F2F2F2", "#111111", "#666666", "#999999", "#BBBBBB", "#333333", "#777777") },
];
export const themeById = (id) => THEMES.find((t) => t.id === id) || THEMES[1];

const hex = (h) => h.replace("#", "").toUpperCase();

export function themeXml(theme, name = "MemuNote") {
  const c = theme.colors;
  const clr = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"]
    .map((k) => `<a:${k}><a:srgbClr val="${hex(c[k])}"/></a:${k}>`).join("");
  const [major, minor] = theme.fonts;
  return XML_HEAD + `<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="${escXml(name)}"><a:themeElements>`
    + `<a:clrScheme name="${escXml(theme.en || name)}">${clr}</a:clrScheme>`
    + `<a:fontScheme name="${escXml(theme.en || name)}"><a:majorFont><a:latin typeface="${escXml(major)}"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>`
    + `<a:minorFont><a:latin typeface="${escXml(minor)}"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>`
    + `<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>`
    + `<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:lumMod val="110000"/><a:satMod val="105000"/><a:tint val="67000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:lumMod val="105000"/><a:satMod val="103000"/><a:tint val="73000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill>`
    + `<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:satMod val="103000"/><a:lumMod val="102000"/><a:tint val="94000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:lumMod val="99000"/><a:satMod val="120000"/><a:shade val="78000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill></a:fillStyleLst>`
    + `<a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>`
    + `<a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>`
    + `<a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/><a:miter lim="800000"/></a:ln></a:lnStyleLst>`
    + `<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst><a:outerShdw blurRad="57150" dist="19050" dir="5400000" algn="ctr" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="63000"/></a:srgbClr></a:outerShdw></a:effectLst></a:effectStyle></a:effectStyleLst>`
    + `<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"><a:tint val="95000"/><a:satMod val="170000"/></a:schemeClr></a:solidFill>`
    + `<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="93000"/><a:satMod val="150000"/><a:shade val="98000"/><a:lumMod val="102000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="63000"/><a:satMod val="120000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill></a:bgFillStyleLst></a:fmtScheme>`
    + `</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>`;
}

const NSDECL = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"';
const GRP = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>';

function ph(id, name, type, idx, box, extra = {}) {
  const xfrm = box ? `<a:xfrm><a:off x="${box[0]}" y="${box[1]}"/><a:ext cx="${box[2]}" cy="${box[3]}"/></a:xfrm>` : "";
  const phAttrs = (type ? ` type="${type}"` : "") + (extra.sz ? ` sz="${extra.sz}"` : "") + (idx != null ? ` idx="${idx}"` : "");
  const body = `<a:bodyPr${extra.anchor ? ` anchor="${extra.anchor}"` : ""}${extra.autofit === false ? "" : ""}>${extra.norm ? "<a:normAutofit/>" : ""}</a:bodyPr>`;
  const lst = extra.lst ? `<a:lstStyle>${extra.lst}</a:lstStyle>` : "<a:lstStyle/>";
  const prompt = extra.prompt ? `<a:p><a:r><a:rPr lang="${extra.lang || "en-US"}"/><a:t>${escXml(extra.prompt)}</a:t></a:r></a:p>` : `<a:p><a:endParaRPr lang="en-US"/></a:p>`;
  return `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="${escXml(name)}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph${phAttrs}/></p:nvPr></p:nvSpPr>`
    + `<p:spPr>${xfrm}</p:spPr><p:txBody>${body}${lst}${prompt}</p:txBody></p:sp>`;
}

function geometry(w, h) {
  const k = w / 12192000, kh = h / 6858000, X = (v) => Math.round(v * k), Y = (v) => Math.round(v * kh);
  return {
    title: [X(838200), Y(365125), X(10515600), Y(1325563)], body: [X(838200), Y(1825625), X(10515600), Y(4351338)],
    ctrTitle: [X(1524000), Y(1122363), X(9144000), Y(2387600)], subTitle: [X(1524000), Y(3602038), X(9144000), Y(1655762)],
    left: [X(838200), Y(1825625), X(5181600), Y(4351338)], right: [X(6172200), Y(1825625), X(5181600), Y(4351338)],
    secTitle: [X(831850), Y(1709738), X(10515600), Y(2852737)], secBody: [X(831850), Y(4589463), X(10515600), Y(1500187)],
    dt: [X(838200), Y(6356350), X(2743200), Y(365125)], ftr: [X(4038600), Y(6356350), X(4114800), Y(365125)], num: [X(8610600), Y(6356350), X(2743200), Y(365125)],
    bigTitle: [X(838200), Y(2130425), X(10515600), Y(1470025)],
  };
}

export const LAYOUTS = [
  { key: "title", type: "title", en: "Title Slide", ru: "Титульный слайд", ph: [["ctrTitle", null, "ctrTitle"], ["subTitle", 1, "subTitle"]] },
  { key: "obj", type: "obj", en: "Title and Content", ru: "Заголовок и объект", ph: [["title", null, null], [null, 1, null]] },
  { key: "twoObj", type: "twoObj", en: "Two Content", ru: "Два объекта", ph: [["title", null, null], [null, 1, "left"], [null, 2, "right"]] },
  { key: "secHead", type: "secHead", en: "Section Header", ru: "Заголовок раздела", ph: [["title", null, "secTitle"], ["body", 1, "secBody"]] },
  { key: "titleOnly", type: "titleOnly", en: "Title Only", ru: "Только заголовок", ph: [["title", null, null]] },
  { key: "blank", type: "blank", en: "Blank", ru: "Пустой слайд", ph: [] },
  { key: "bigTitle", type: "tx", en: "Statement", ru: "Тезис", ph: [["title", null, "bigTitle"]] },
];

function decorXml(theme, w, h, startId) {
  const a = "accent1";
  if (theme.decor === "bar") return `<p:sp><p:nvSpPr><p:cNvPr id="${startId}" name="Accent bar"/><p:cNvSpPr/><p:nvPr userDrawn="1"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${Math.round(w * 0.012)}" cy="${h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:schemeClr val="${a}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>`;
  if (theme.decor === "band") return `<p:sp><p:nvSpPr><p:cNvPr id="${startId}" name="Accent band"/><p:cNvSpPr/><p:nvPr userDrawn="1"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="${Math.round(h * 0.965)}"/><a:ext cx="${w}" cy="${Math.round(h * 0.035)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:schemeClr val="${a}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>`;
  if (theme.decor === "rule") return `<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="${startId}" name="Rule"/><p:cNvCxnSpPr/><p:nvPr userDrawn="1"/></p:nvCxnSpPr><p:spPr><a:xfrm><a:off x="${Math.round(w * 0.0687)}" y="${Math.round(h * 0.255)}"/><a:ext cx="${Math.round(w * 0.12)}" cy="0"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom><a:ln w="28575"><a:solidFill><a:schemeClr val="${a}"/></a:solidFill></a:ln></p:spPr></p:cxnSp>`;
  if (theme.decor === "glow") return `<p:sp><p:nvSpPr><p:cNvPr id="${startId}" name="Glow"/><p:cNvSpPr/><p:nvPr userDrawn="1"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="${Math.round(w * 0.78)}" y="${Math.round(-h * 0.25)}"/><a:ext cx="${Math.round(w * 0.36)}" cy="${Math.round(w * 0.36)}"/></a:xfrm><a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom><a:solidFill><a:schemeClr val="${a}"><a:alpha val="22000"/></a:schemeClr></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>`;
  return "";
}

function lvlStyles(kind, theme) {
  const bullets = ["•", "–", "•", "–", "•", "•", "•", "•", "•"];
  let out = "";
  for (let i = 1; i <= 9; i++) {
    if (kind === "title") { if (i > 1) break; out += `<a:lvl1pPr algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:lnSpc><a:spcPct val="90000"/></a:lnSpc><a:spcBef><a:spcPct val="0"/></a:spcBef><a:buNone/><a:defRPr sz="4400" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/><a:cs typeface="+mj-cs"/></a:defRPr></a:lvl1pPr>`; continue; }
    const mar = 342900 + (i - 1) * 457200;
    if (kind === "body") {
      const sz = [2800, 2400, 2000, 1800, 1800, 1800, 1800, 1800, 1800][i - 1];
      out += `<a:lvl${i}pPr marL="${mar}" indent="${i === 1 ? -342900 : -285750}" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc><a:spcBef><a:spcPts val="${i === 1 ? 1000 : 500}"/></a:spcBef>`
        + `<a:buClr><a:schemeClr val="accent1"/></a:buClr><a:buFont typeface="Arial" panose="020B0604020202020204" pitchFamily="34" charset="0"/><a:buChar char="${bullets[i - 1]}"/><a:defRPr sz="${sz}" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl${i}pPr>`;
    } else {
      out += `<a:lvl${i}pPr marL="${(i - 1) * 457200}" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:defRPr sz="1800" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl${i}pPr>`;
    }
  }
  return out;
}

const CLRMAP_LIGHT = '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>';
const CLRMAP_DARK = '<p:clrMap bg1="dk1" tx1="lt1" bg2="dk2" tx2="lt2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>';

export function masterXml(theme, w, h, layoutCount) {
  const g = geometry(w, h);
  const sp = decorXml(theme, w, h, 7)
    + ph(2, "Title Placeholder 1", "title", null, g.title, { anchor: "ctr", norm: true, prompt: "Click to edit Master title style" })
    + ph(3, "Text Placeholder 2", "body", 1, g.body, { norm: true, prompt: "Click to edit Master text styles" })
    + ph(4, "Date Placeholder 3", "dt", 2, g.dt, { anchor: "ctr", lst: '<a:lvl1pPr algn="l"><a:defRPr sz="1200"><a:solidFill><a:schemeClr val="tx1"><a:tint val="75000"/></a:schemeClr></a:solidFill></a:defRPr></a:lvl1pPr>' })
    + ph(5, "Footer Placeholder 4", "ftr", 3, g.ftr, { anchor: "ctr", lst: '<a:lvl1pPr algn="ctr"><a:defRPr sz="1200"><a:solidFill><a:schemeClr val="tx1"><a:tint val="75000"/></a:schemeClr></a:solidFill></a:defRPr></a:lvl1pPr>' })
    + ph(6, "Slide Number Placeholder 5", "sldNum", 4, g.num, { anchor: "ctr", lst: '<a:lvl1pPr algn="r"><a:defRPr sz="1200"><a:solidFill><a:schemeClr val="tx1"><a:tint val="75000"/></a:schemeClr></a:solidFill></a:defRPr></a:lvl1pPr>' });
  const ids = Array.from({ length: layoutCount }, (_, i) => `<p:sldLayoutId id="${2147483649 + i}" r:id="rId${i + 1}"/>`).join("");
  return XML_HEAD + `<p:sldMaster ${NSDECL}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>${GRP}${sp}</p:spTree></p:cSld>`
    + (theme.dark ? CLRMAP_DARK : CLRMAP_LIGHT) + `<p:sldLayoutIdLst>${ids}</p:sldLayoutIdLst>`
    + `<p:txStyles><p:titleStyle>${lvlStyles("title", theme)}</p:titleStyle><p:bodyStyle>${lvlStyles("body", theme)}</p:bodyStyle><p:otherStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr>${lvlStyles("other", theme)}</p:otherStyle></p:txStyles></p:sldMaster>`;
}

export function layoutXml(def, theme, w, h, lang) {
  const g = geometry(w, h);
  let id = 2, sp = "";
  const prompts = lang === "ru" ? { title: "Заголовок слайда", ctrTitle: "Заголовок презентации", subTitle: "Подзаголовок", body: "Текст слайда" }
    : { title: "Click to add title", ctrTitle: "Click to add title", subTitle: "Click to add subtitle", body: "Click to add text" };
  for (const [type, idx, box] of def.ph) {
    const extra = { prompt: prompts[type] || prompts.body, lang: lang === "ru" ? "ru-RU" : "en-US" };
    if (type === "ctrTitle") Object.assign(extra, { anchor: "b", norm: true, lst: '<a:lvl1pPr algn="ctr"><a:defRPr sz="6000"/></a:lvl1pPr>' });
    if (type === "subTitle") Object.assign(extra, { norm: true, lst: '<a:lvl1pPr marL="0" indent="0" algn="ctr"><a:buNone/><a:defRPr sz="2400"><a:solidFill><a:schemeClr val="tx1"><a:lumMod val="75000"/><a:lumOff val="25000"/></a:schemeClr></a:solidFill></a:defRPr></a:lvl1pPr>' });
    if (def.key === "secHead" && type === "title") Object.assign(extra, { anchor: "b", norm: true, lst: '<a:lvl1pPr><a:defRPr sz="6000"/></a:lvl1pPr>' });
    if (def.key === "secHead" && type === "body") Object.assign(extra, { norm: true, lst: '<a:lvl1pPr marL="0" indent="0"><a:buNone/><a:defRPr sz="2400"><a:solidFill><a:schemeClr val="tx1"><a:lumMod val="65000"/><a:lumOff val="35000"/></a:schemeClr></a:solidFill></a:defRPr></a:lvl1pPr>' });
    if (def.key === "bigTitle") Object.assign(extra, { anchor: "ctr", norm: true, lst: '<a:lvl1pPr algn="ctr"><a:defRPr sz="5400"/></a:lvl1pPr>' });
    if (def.key === "twoObj") extra.lst = '<a:lvl1pPr><a:defRPr sz="2400"/></a:lvl1pPr><a:lvl2pPr><a:defRPr sz="2000"/></a:lvl2pPr>';
    const nm = (type === "ctrTitle" || type === "title" ? "Title " : type === "subTitle" ? "Subtitle " : "Content Placeholder ") + id;
    sp += ph(id++, nm, type, idx, box ? g[box] : null, { ...extra, sz: def.key === "twoObj" && idx ? "half" : undefined });
  }
  return XML_HEAD + `<p:sldLayout ${NSDECL} type="${def.type}" preserve="1"><p:cSld name="${escXml(def.en)}"><p:spTree>${GRP}${sp}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>`;
}

export function notesMasterXml() {
  return XML_HEAD + `<p:notesMaster ${NSDECL}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>${GRP}`
    + `<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="685800" y="1143000"/><a:ext cx="5486400" cy="3086100"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln w="12700"><a:solidFill><a:prstClr val="black"/></a:solidFill></a:ln></p:spPr></p:sp>`
    + `<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="685800" y="4400550"/><a:ext cx="5486400" cy="3600450"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr vert="horz" lIns="91440" tIns="45720" rIns="91440" bIns="45720" rtlCol="0"/><a:lstStyle/><a:p><a:pPr lvl="0"/><a:r><a:rPr lang="en-US"/><a:t>Click to edit Master text styles</a:t></a:r></a:p></p:txBody></p:sp>`
    + `</p:spTree></p:cSld>${CLRMAP_LIGHT}<p:notesStyle><a:lvl1pPr marL="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:defRPr sz="1200" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr></p:notesStyle></p:notesMaster>`;
}
export const NOTES_MASTER_RELS = XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme2.xml"/></Relationships>';

export function emptySlideXml(layoutDef, lang) {
  let id = 2, sp = "";
  for (const [type, idx] of layoutDef?.ph || []) {
    const phA = (type ? ` type="${type}"` : "") + (idx != null ? ` idx="${idx}"` : "");
    sp += `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="${type === "title" || type === "ctrTitle" ? "Title" : "Content"} ${id - 1}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph${phA}/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="${lang === "ru" ? "ru-RU" : "en-US"}"/></a:p></p:txBody></p:sp>`;
    id++;
  }
  return XML_HEAD + `<p:sld ${NSDECL}><p:cSld><p:spTree>${GRP}${sp}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>`;
}

const rels = (items) => XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
  + items.map(([id, type, target]) => `<Relationship Id="${id}" Type="http://schemas.openxmlformats.org/${type}" Target="${target}"/>`).join("") + "</Relationships>";

export function newPackage({ theme = THEMES[1], size = "16:9", lang = "ru", title = "" } = {}) {
  const [w, h] = SIZES[size] || SIZES["16:9"];
  const files = {};
  const n = LAYOUTS.length;
  files["[Content_Types].xml"] = XML_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    + '<Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="jpg" ContentType="image/jpeg"/><Default Extension="gif" ContentType="image/gif"/>'
    + '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    + '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
    + Array.from({ length: n }, (_, i) => `<Override PartName="/ppt/slideLayouts/slideLayout${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>`).join("")
    + '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
    + '<Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>'
    + '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    + '<Override PartName="/ppt/theme/theme2.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    + '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>'
    + '<Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>'
    + '<Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>'
    + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    + '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>';
  files["_rels/.rels"] = rels([["rId1", "officeDocument/2006/relationships/officeDocument", "ppt/presentation.xml"],
    ["rId2", "package/2006/relationships/metadata/core-properties", "docProps/core.xml"], ["rId3", "officeDocument/2006/relationships/extended-properties", "docProps/app.xml"]]);
  const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  files["docProps/core.xml"] = XML_HEAD + `<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>${escXml(title)}</dc:title><dc:creator>MemuNote</dc:creator><cp:lastModifiedBy>MemuNote</cp:lastModifiedBy><cp:revision>1</cp:revision><dcterms:created xsi:type="dcterms:W3CDTF">${now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">${now}</dcterms:modified></cp:coreProperties>`;
  files["docProps/app.xml"] = XML_HEAD + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>MemuNote</Application><PresentationFormat>On-screen Show</PresentationFormat><Slides>1</Slides></Properties>';
  const prels = [["rId1", "officeDocument/2006/relationships/slideMaster", "slideMasters/slideMaster1.xml"],
    ["rId2", "officeDocument/2006/relationships/notesMaster", "notesMasters/notesMaster1.xml"],
    ["rId3", "officeDocument/2006/relationships/presProps", "presProps.xml"], ["rId4", "officeDocument/2006/relationships/viewProps", "viewProps.xml"],
    ["rId5", "officeDocument/2006/relationships/theme", "theme/theme1.xml"], ["rId6", "officeDocument/2006/relationships/tableStyles", "tableStyles.xml"],
    ["rId7", "officeDocument/2006/relationships/slide", "slides/slide1.xml"]];
  files["ppt/_rels/presentation.xml.rels"] = rels(prels);
  files["ppt/presentation.xml"] = XML_HEAD + `<p:presentation ${NSDECL} saveSubsetFonts="1"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>`
    + `<p:notesMasterIdLst><p:notesMasterId r:id="rId2"/></p:notesMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId7"/></p:sldIdLst>`
    + `<p:sldSz cx="${w}" cy="${h}"/><p:notesSz cx="6858000" cy="9144000"/><p:defaultTextStyle><a:defPPr><a:defRPr lang="${lang === "ru" ? "ru-RU" : "en-US"}"/></a:defPPr></p:defaultTextStyle></p:presentation>`;
  files["ppt/presProps.xml"] = XML_HEAD + `<p:presentationPr ${NSDECL}/>`;
  files["ppt/viewProps.xml"] = XML_HEAD + `<p:viewPr ${NSDECL}><p:normalViewPr><p:restoredLeft sz="15620"/><p:restoredTop sz="94660"/></p:normalViewPr><p:gridSpacing cx="76200" cy="76200"/></p:viewPr>`;
  files["ppt/tableStyles.xml"] = XML_HEAD + '<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>';
  files["ppt/theme/theme1.xml"] = themeXml(theme, theme.en);
  files["ppt/theme/theme2.xml"] = themeXml(THEMES[1], "Notes");
  files["ppt/slideMasters/slideMaster1.xml"] = masterXml(theme, w, h, n);
  files["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = rels([...LAYOUTS.map((_, i) => [`rId${i + 1}`, "officeDocument/2006/relationships/slideLayout", `../slideLayouts/slideLayout${i + 1}.xml`]),
    [`rId${n + 1}`, "officeDocument/2006/relationships/theme", "../theme/theme1.xml"]]);
  LAYOUTS.forEach((def, i) => {
    files[`ppt/slideLayouts/slideLayout${i + 1}.xml`] = layoutXml(def, theme, w, h, lang);
    files[`ppt/slideLayouts/_rels/slideLayout${i + 1}.xml.rels`] = rels([["rId1", "officeDocument/2006/relationships/slideMaster", "../slideMasters/slideMaster1.xml"]]);
  });
  files["ppt/notesMasters/notesMaster1.xml"] = notesMasterXml();
  files["ppt/notesMasters/_rels/notesMaster1.xml.rels"] = NOTES_MASTER_RELS;
  files["ppt/slides/slide1.xml"] = emptySlideXml(LAYOUTS[0], lang);
  files["ppt/slides/_rels/slide1.xml.rels"] = rels([["rId1", "officeDocument/2006/relationships/slideLayout", "../slideLayouts/slideLayout1.xml"]]);
  return files;
}
