

import { api, t, toast, basename, dirname, modal, esc, I18N } from "../core.js";
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
async function toDataUrl(blob) {
  return new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => res(""); r.readAsDataURL(blob); });
}

export async function slideCanvas(deck, i, width, dataUrls) {
  if (document.fonts?.ready) await Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 2500))]);
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
  const img = await new Promise((res, rej) => {
    const im = new Image(), timer = setTimeout(() => rej(new Error("Slide rendering timed out")), 15000);
    im.onload = () => {clearTimeout(timer);res(im);};
    im.onerror = () => {clearTimeout(timer);rej(new Error("Could not render the slide"));};
    im.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  });
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
const blobOf = (c, type, q) => new Promise((resolve,reject) => c.toBlob(blob => blob ? resolve(blob) : reject(new Error("The browser could not encode the slide")), type, q));
const L = (ru,en) => I18N.lang === "ru" ? ru : en;

export function parseSlideRange(value, count) {
  const source=String(value || "").trim().replace(/[–—]/g,"-");
  if(!source)return Array.from({length:count},(_,i)=>i);
  const result=new Set();
  for(const part of source.split(",")) {
    const match=/^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$/.exec(part);
    if(!match)throw new Error(L("Диапазон: например 1–3, 5","Use a range such as 1–3, 5"));
    const first=Number(match[1]),last=Number(match[2] || match[1]);
    if(first<1 || last<first || last>count)throw new Error(L("Номер слайда вне презентации","Slide number is outside the presentation"));
    for(let i=first;i<=last;i++)result.add(i-1);
  }
  return [...result].sort((a,b)=>a-b);
}

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
  const ctrl=new AbortController(), previous=document.activeElement;
  const el = document.createElement("div"); el.className = "loading-overlay sl-export-progress"; el.innerHTML = `<div class="spinner"></div><span role="status"></span><button class="btn">${esc(t("cancel"))}</button>`;
  el.querySelector("span").textContent = text; document.body.append(el);
  const cancel=()=>ctrl.abort();el.querySelector("button").onclick=cancel;el.querySelector("button").focus();
  const keys=e=>{if(e.key==="Escape"){e.preventDefault();e.stopPropagation();cancel();}};document.addEventListener("keydown",keys,true);
  return { signal:ctrl.signal, set: (s) => { el.querySelector("span").textContent = s; }, done: () => {document.removeEventListener("keydown",keys,true);el.remove();if(previous?.isConnected)previous.focus({preventScroll:true});} };
}

export async function exportPptx(view) {
  const path=await savePath(view,"pptx","PowerPoint");if(!path)return;
  view.pushIfPending();
  const {openPptx,savePptx}=await import("./pptx.js");

  const copy=await openPptx(await view.bytes());
  try {await write(path,await savePptx(copy,{ext:"pptx"}),"application/vnd.openxmlformats-officedocument.presentationml.presentation");}
  finally {copy.destroy();}
  toast(t("sl_exported",{name:basename(path)}),{kind:"ok"});
}

export async function exportOptions(view) {
  const options=await modal({title:L("Экспорт презентации в PDF","Export presentation to PDF"),wide:true,body:`<div class="sl-studio"><p class="sl-studio-intro">${esc(L("Слайды сохранят оформление. Заметки можно добавить отдельными страницами.","Keep the slide appearance and optionally add speaker notes as separate pages."))}</p><label class="field"><span>${esc(L("Слайды","Slides"))}</span><input class="input" id="sl-export-range" placeholder="${esc(L("Все слайды или 1–3, 5","All slides, or 1–3, 5"))}"></label><label class="field"><span>${esc(L("Качество","Quality"))}</span><select class="input" id="sl-export-width"><option value="1280">${esc(L("Компактно · 1280 px","Compact · 1280 px"))}</option><option value="1920" selected>${esc(L("Стандарт · 1920 px","Standard · 1920 px"))}</option><option value="2560">${esc(L("Для печати · 2560 px","High resolution · 2560 px"))}</option></select></label><label class="check"><input type="checkbox" id="sl-export-hidden">${esc(L("Включить скрытые слайды","Include hidden slides"))}</label><label class="check"><input type="checkbox" id="sl-export-notes">${esc(L("Добавить страницы с заметками докладчика","Add speaker notes pages"))}</label><p class="muted small">${esc(L("PDF сохраняет слайды как изображения. Для дальнейшего редактирования используйте PPTX.","PDF pages contain rendered slide images. Use PPTX for further editing."))}</p></div>`,buttons:[{label:t("cancel"),value:null},{label:L("Экспортировать","Export"),primary:true,value:b=>({range:b.querySelector("#sl-export-range").value,width:+b.querySelector("#sl-export-width").value,includeHidden:b.querySelector("#sl-export-hidden").checked,notes:b.querySelector("#sl-export-notes").checked})}]});
  if(options)await exportPdf(view,options);
}

function notesCanvases(text, title) {
  const width=1240,height=1754,margin=90,lineHeight=42,fontSize=29;
  const measure=document.createElement("canvas").getContext("2d");measure.font=`${fontSize}px "Segoe UI",sans-serif`;
  const lines=[];
  for(const paragraph of String(text).split(/\r?\n/)) {
    let line="";
    for(const word of paragraph.split(/\s+/)) {
      if(measure.measureText(line+(line?" ":"")+word).width<=width-2*margin){line+=(line?" ":"")+word;continue;}
      if(line){lines.push(line);line="";}
      for(const char of word){if(measure.measureText(line+char).width>width-2*margin){lines.push(line);line="";}line+=char;}
    }
    lines.push(line);lines.push("");
  }
  const perPage=Math.floor((height-margin-180)/lineHeight),pages=[];
  for(let start=0;start<lines.length;start+=perPage){const canvas=document.createElement("canvas");canvas.width=width;canvas.height=height;const ctx=canvas.getContext("2d");ctx.fillStyle="#fff";ctx.fillRect(0,0,width,height);ctx.fillStyle="#171717";ctx.font='600 32px "Segoe UI",sans-serif';ctx.fillText(title,margin,margin, width-2*margin);ctx.font=`${fontSize}px "Segoe UI",sans-serif`;lines.slice(start,start+perPage).forEach((line,i)=>ctx.fillText(line,margin,165+i*lineHeight));pages.push(canvas);}
  return pages;
}

export async function exportPdf(view, options = {}) {
  if(view.editing)view.endEdit();view.pushIfPending();
  const selected=parseSlideRange(options.range,view.deck.slides.length).filter(i=>options.includeHidden || !view.deck.slides[i].hidden);
  if(!selected.length)throw new Error(L("В выбранном диапазоне нет видимых слайдов","The range contains no visible slides"));
  const path = await savePath(view, "pdf", "PDF"); if (!path) return;
  const deck = view.deck, p = progress(t("sl_exporting"));
  try {
    const { PDFDocument } = await import("../../vendor/pdflib/pdflib.js");
    const doc = await PDFDocument.create();
    doc.setTitle(view.tab.name.replace(/\.\w+$/, "")); doc.setCreator("MemuNote"); doc.setProducer("MemuNote");
    const urls = await mediaDataUrls(deck);
    const list = selected;
    const pw = deck.w / 12700, ph = deck.h / 12700;
    for (const [k, i] of list.entries()) {
      if(p.signal.aborted)return;
      p.set(t("sl_exporting_n", { n: k + 1, total: list.length }));
      const c = await slideCanvas(deck, i, [1280,1920,2560].includes(options.width)?options.width:1920, urls);
      const img = await doc.embedJpg(new Uint8Array(await (await blobOf(c, "image/jpeg", 0.92)).arrayBuffer()));
      const page = doc.addPage([pw, ph]);
      page.drawImage(img, { x: 0, y: 0, width: pw, height: ph });
      if(options.notes && deck.slides[i].notes?.trim()) {
        const title=L(`Заметки к слайду ${i+1}`,`Speaker notes for slide ${i+1}`);
        for(const notes of notesCanvases(deck.slides[i].notes,title)) {
          if(p.signal.aborted)return;
          const image=await doc.embedPng(new Uint8Array(await (await blobOf(notes,"image/png")).arrayBuffer()));
          doc.addPage([595.28,841.89]).drawImage(image,{x:0,y:0,width:595.28,height:841.89});
        }
      }
    }
    if(p.signal.aborted)return;
    const bytes=await doc.save();
    if(p.signal.aborted)return;
    await write(path, bytes, "application/pdf");
    toast(t("sl_exported", { name: basename(path) }), { kind: "ok" });
  } finally { p.done(); }
}
export async function exportPng(view, all) {
  if(view.editing)view.endEdit();view.pushIfPending();
  const path = await savePath(view, "png", "PNG"); if (!path) return;
  const deck = view.deck, p = progress(t("sl_exporting"));
  try {
    const urls = await mediaDataUrls(deck);
    const list = all ? deck.slides.map((s, i) => i).filter((i) => !deck.slides[i].hidden) : [view.cur];
    const stem = path.replace(/\.png$/i, ""), sep = path.includes("\\") ? "\\" : "/";
    for (const [k, i] of list.entries()) {
      if(p.signal.aborted)return;
      p.set(t("sl_exporting_n", { n: k + 1, total: list.length }));
      const c = await slideCanvas(deck, i, 1920, urls);
      const out = list.length > 1 ? `${stem}-${String(i + 1).padStart(String(deck.slides.length).length, "0")}.png` : path;
      const bytes=new Uint8Array(await (await blobOf(c, "image/png")).arrayBuffer());
      if(p.signal.aborted)return;
      await write(out, bytes, "image/png");
    }
    toast(list.length > 1 ? t("sl_exported_n", { n: list.length, dir: dirname(path).split(sep).pop() }) : t("sl_exported", { name: basename(path) }), { kind: "ok" });
  } finally { p.done(); }
}
