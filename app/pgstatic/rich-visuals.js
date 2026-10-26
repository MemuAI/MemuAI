

"use strict";
const MemuRichVisuals = (() => {
  const MAX_RAW = 140000, MAX_NODES = 350, MAX_DEPTH = 8;
  const cache = new Map(), interactions = new Map();
  let serial = 0, sheet = null, returnFocus = null;
  const T = (ru,en) => (typeof PG_LANG !== "undefined" ? PG_LANG : document.documentElement.lang) === "en" ? en : ru;
  const E = v => String(v ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const text = (v,n=4000) => typeof v === "string" || typeof v === "number" ? String(v).slice(0,n) : "";
  const prefValue = (key, fallback) => typeof pref === "function" ? pref(key,fallback) : fallback;
  const enabled = () => prefValue("visualizations_enabled",true) !== false || (typeof S !== "undefined" && !!S.chatCaps?.visualize);
  const customEnabled = () => enabled() && prefValue("visualizations_custom_enabled",true) !== false;
  const imagesEnabled = () => enabled() && prefValue("visualizations_images_enabled",true) !== false;
  const cacheKey = () => `${enabled()}/${customEnabled()}/${imagesEnabled()}`;
  const enc = v => E(encodeURIComponent(JSON.stringify(v)));
  const decode = v => {try{return JSON.parse(decodeURIComponent(v || ""));}catch{return null;}};
  const list = (v,n=50) => Array.isArray(v) ? v.slice(0,n).filter(item=>item!==null && item!==undefined) : [];
  const number = v => typeof v === "number" && Number.isFinite(v) ? v : null;
  const fmt = (v,digits=2) => number(v)===null ? "—" : v.toLocaleString(T("ru-RU","en-US"),{maximumFractionDigits:digits});
  const tone = v => ["accent","muted","success","warning","danger","neutral"].includes(v) ? v : "neutral";
  const PATHS = {
    folder:'<path d="M3 7h7l2-3h7a2 2 0 0 1 2 2v12H3z"/>',
    brain:'<path d="M12 4c-3-3-7 0-6 3-4 0-4 6-1 7-2 4 3 7 7 4m0-14c3-3 7 0 6 3 4 0 4 6 1 7 2 4-3 7-7 4V4M8 8h4m4 4h-4m-5 3h5"/>',
    branch:'<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M6 7v10m0-5h7a5 5 0 0 0 5-5"/>',
    note:'<path d="M6 3h10l3 3v14H5V3h1m3 5h6m-6 4h7m-7 4h4"/>',
    check:'<path d="m5 12 5 5L20 6"/>', search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    sparkle:'<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3z"/>',
    arrow:'<path d="M12 4v16m-5-5 5 5 5-5"/>', star:'<path d="m12 2 3 6 7 1-5 5 1 8-6-4-6 4 1-8-5-5 7-1z"/>',
    code:'<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16"/>',
    user:'<circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 3"/>',
    image:'<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8" cy="9" r="1"/><path d="m3 17 6-6 6 6 3-3 3 3"/>',
    link:'<path d="m10 14 4-4m-6 6-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 12a4 4 0 0 0 6 0l5-5a4 4 0 0 0-6-6l-1 1"/>',
    close:'<path d="m6 6 12 12M18 6 6 18"/>', chart:'<path d="M3 3v18h18M7 16v-5m5 5V6m5 10v-8"/>'
  };
  const icon = name => `<span class="rv-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${PATHS[name] || PATHS.info}</svg></span>`;
  function safeUrl(v) {
    const value=text(v,3000).trim();
    if(!/^https?:\/\//i.test(value) || /[\s"'<>`\\]/.test(value))return "";
    try {const u=new URL(value);return !u.username && !u.password && !["localhost","127.0.0.1","::1"].includes(u.hostname)?u.href:"";}catch{return "";}
  }
  function imageUrl(v) {
    const value=text(typeof v === "object" ? v?.url : v,700000);
    if(/^data:image\/(?:png|jpeg|webp|gif|avif);base64,[a-z\d+/=\r\n]+$/i.test(value) && value.length<700000)return value;
    if(/^\/api\/pg\/media\/file\/[a-f\d]{64}\.(?:png|jpg|jpeg|webp|gif|avif)(?:\?profile_id=[\w.-]+)?$/i.test(value))return value;
    const remote=safeUrl(value);return remote?`/api/pg/media?url=${encodeURIComponent(remote)}&limit_mb=5`:"";
  }
  function inline(v) {
    return E(text(v)).replace(/\*\*([^*\n]+)\*\*/g,"<strong>$1</strong>").replace(/`([^`\n]+)`/g,"<code>$1</code>");
  }
  function entity(raw) {
    if(!raw || typeof raw!=="object")return {name:text(raw)};
    return {id:text(raw.id,80),name:text(raw.name || raw.title,180),subtitle:text(raw.subtitle,240),summary:text(raw.summary,2400),
      description:text(raw.description,5000),image:raw.image,images:list(raw.images,6),url:safeUrl(raw.url),
      facts:list(raw.facts,15).map(f=>({label:text(f?.label,100),value:text(f?.value,500)})),
      sources:list(raw.sources,8).map(s=>({title:text(s?.title || s?.label,160),url:safeUrl(s?.url)})).filter(s=>s.url),
      related:list(raw.related,6).map(r=>({name:text(r?.name || r?.title,120),summary:text(r?.summary,1200),url:safeUrl(r?.url)})),
      badge:text(raw.badge,100),tone:tone(raw.tone),value:text(raw.value,100),detail:text(raw.detail,500)};
  }
  const TYPES = ["text","heading","stack","columns","grid","row","step","flow","branch","ranking","comparison","cards","card","entity","timeline","stat","metrics","badge","callout","list","table","image","chart","tabs","accordion","calculator","divider","code","quote","progress"];
  function validate(value) {
    const errors=[];let count=0;
    if(!value || typeof value!=="object" || Array.isArray(value))return {ok:false,errors:["Expected a JSON object"]};
    if(value.type==="custom" || typeof value.html==="string")return {ok:typeof value.html==="string" && value.html.length<=60000 && !!text(value.summary).trim(),errors:typeof value.html!=="string" || value.html.length>60000 || !text(value.summary).trim()?["Custom visuals require HTML up to 60000 characters and a readable summary"]:[],nodes:1};
    function walk(node,depth,path) {
      if(depth>MAX_DEPTH || ++count>MAX_NODES){if(errors.length<8)errors.push(`${path}: too many nested blocks`);return;}
      if(!node || typeof node!=="object"){errors.push(`${path}: expected a block object`);return;}
      const kind=node.type || "text";
      if(!TYPES.includes(kind))errors.push(`${path}: unsupported type ${String(kind).slice(0,60)}`);
      if(kind==="calculator")try{calculate(text(node.formula,300),Object.fromEntries(list(node.inputs,8).map(i=>[text(i.id,40),number(i.value) ?? number(i.min) ?? 0])));}catch(e){errors.push(`${path}: ${e.message}`);}
      if(kind==="ranking")for(const item of list(node.items,60))if(number(item.value)===null)errors.push(`${path}: ranking value must be a finite number`);
      for(const key of ["blocks","children","steps","columns"])for(const [i,child] of list(node[key],50).entries())walk(child,depth+1,`${path}.${key}[${i}]`);
      if(["branch","tabs","accordion","cards","comparison","timeline","metrics"].includes(kind))for(const [i,item] of list(node.items || node.branches,40).entries())for(const [j,child] of list(item?.blocks,40).entries())walk(child,depth+1,`${path}.items[${i}].blocks[${j}]`);
    }
    const root=value.type==="text" && (value.blocks || value.children)?{...value,type:"stack"}:value;
    walk(root,0,"visual");
    return {ok:!errors.length,errors:errors.slice(0,8),nodes:count};
  }
  function schema() {
    return {version:2,protocol:"memuai-visual",root:{type:"text",title:"optional",summary:"concise copyable explanation",blocks:"array of blocks",entities:"optional entity definitions"},
      types:TYPES,common:{type:"one of types",title:"optional",text:"plain text; **bold** and `code` supported",tone:"neutral|accent|muted|success|warning|danger",icon:"folder|brain|branch|note|check|search|info|sparkle|star|code|user|clock|image|chart"},
      shapes:{flow:{steps:[{icon:"folder",title:"Step",text:"Description"},{type:"branch",title:"Condition?",branches:[{label:"Yes",blocks:[{type:"step",title:"Action"}]}]}]},ranking:{max:10,items:[{name:"Option",value:9,detail:"Evidence"}],sort:"value|name"},comparison:{items:[{name:"A",value:"Result",text:"Evidence",badge:"optional",blocks:[]}]},
        entity:{id:"unique-id",name:"Term",summary:"factual description",image:"public URL or {url,alt}",facts:[{label:"Field",value:"Value"}],sources:[{title:"Source",url:"https://..."}]},
        chart:{chart:"bar|line|pie|donut",unit:"optional",data:[{label:"Label",value:4}]},table:{columns:["Column"],rows:[["Value"]]},calculator:{inputs:[{id:"n",label:"Count",min:0,max:20,step:1,value:4}],formula:"n*2",unit:"optional"},
        columns:{columns:[{type:"stack",blocks:[]}]},tabs:{items:[{label:"Tab",blocks:[]}]},accordion:{items:[{title:"Details",blocks:[]}]},cards:{items:[{title:"Title",text:"Description",image:"optional",url:"optional",entity:"optional entity object"}]},
        timeline:{items:[{title:"Event",date:"Date",text:"Description"}]},list:{items:["Text"]},metrics:{items:[{label:"Count",value:"42",detail:"Context"}]},progress:{min:0,max:100,value:40,label:"Progress"}},
      inline_terms:"Define entities in a visual's entities array or a memuai-entity JSON fence. Refer outside code with [[entity:unique-id|Visible term]]. The panel uses only supplied facts, images and sources.",
      custom:{type:"custom",html:"self-contained HTML fragment, no external requests",summary:"required concise copyable explanation"},limits:{maxChars:MAX_RAW,maxNodes:MAX_NODES,maxDepth:MAX_DEPTH},
      rules:["Do not invent values, image URLs or sources.","Use native blocks for explanations and comparisons. Custom HTML is for interactions native blocks cannot express.","Keep ordinary surrounding prose. Place blocks anywhere they help.","Use visual_validate to check a complex block before emitting it."]};
  }
  function calculate(formula,values) {
    const src=String(formula || "").trim();if(!src || src.length>300)throw new Error("Provide a short arithmetic formula");
    const tokens=src.match(/(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|[a-zA-Z_][a-zA-Z_\d]*|\*\*|[+*/%(),-]/g) || [];
    if(tokens.join("")!==src.replace(/\s/g,"") || tokens.length>120)throw new Error("Only arithmetic and named inputs are allowed");
    let at=0,depth=0;const functions={min:Math.min,max:Math.max,abs:Math.abs,round:Math.round,floor:Math.floor,ceil:Math.ceil,sqrt:Math.sqrt,pow:Math.pow};
    const primary=()=>{
      if(++depth>24)throw new Error("Formula is too deeply nested");let value,t=tokens[at++];
      if(t==="(" && tokens[at]!==")"){value=add();if(tokens[at++]!==")")throw new Error("Missing closing parenthesis");}
      else if(t==="+" || t==="-")value=(t==="-"?-1:1)*primary();
      else if(/^\d|^\./.test(t || ""))value=Number(t);
      else if(Object.hasOwn(functions,t) && tokens[at]==="(") {at++;const args=[add()];while(tokens[at]===","){at++;args.push(add());}if(tokens[at++]!==")" || args.length>8)throw new Error("Invalid function arguments");value=functions[t](...args);}
      else if(Object.hasOwn(values,t))value=values[t];
      else throw new Error("Unknown input or operator");
      depth--;return value;
    };
    const power=()=>{let v=primary();if(tokens[at]==="**"){at++;const exponent=primary();if(Math.abs(exponent)>20)throw new Error("Exponent is too large");v=Math.pow(v,exponent);}return v;};
    const multiply=()=>{let v=power();while(["*","/","%"].includes(tokens[at])){const op=tokens[at++],next=power();v=op==="*"?v*next:op==="/"?v/next:v%next;}return v;};
    const add=()=>{let v=multiply();while(["+","-"].includes(tokens[at])){const op=tokens[at++],next=multiply();v=op==="+"?v+next:v-next;}return v;};
    const result=add();if(at!==tokens.length || !Number.isFinite(result) || Math.abs(result)>1e100)throw new Error("The result is not finite");return result;
  }
  function imageMarkup(value,alt="",cls="") {
    const src=imagesEnabled()?imageUrl(value):"";if(!src)return "";
    return `<img class="rv-image ${cls}" src="${E(src)}" alt="${E(text(typeof value==="object"?value.alt || alt:alt,240))}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
  }
  function entityButton(value,label,cls="") {
    const item=entity(value);return `<button type="button" class="rv-term ${cls}" data-rv-entity="${enc(item)}" aria-haspopup="dialog">${E(label || item.name || T("Подробнее","Details"))}</button>`;
  }
  function entityCard(value) {
    const item=entity(value);
    if(!enabled())return `<div class="rv-plain">${E(plain({...item,type:"entity"}))}</div>`;
    return `<article class="rv-entity-card">${imageMarkup(item.image || item.images[0],item.name)}<div><h4>${entityButton(item,item.name)}</h4>${item.subtitle?`<p class="rv-muted">${E(item.subtitle)}</p>`:""}${item.value?`<strong class="rv-value">${E(item.value)}</strong>`:""}${item.summary?`<p>${inline(item.summary)}</p>`:""}${item.detail?`<small class="rv-muted">${E(item.detail)}</small>`:""}${item.badge?`<span class="rv-badge ${item.tone}">${E(item.badge)}</span>`:""}</div></article>`;
  }
  function rankingRows(node,order="value") {
    const items=list(node.items,60).map((v,i)=>({...v,_index:i}));
    items.sort(order==="name"?(a,b)=>text(a.name || a.label).localeCompare(text(b.name || b.label),T("ru","en")):(a,b)=>(number(b.value) ?? -Infinity)-(number(a.value) ?? -Infinity) || a._index-b._index);
    const max=Math.max(1,number(node.max) ?? Math.max(1,...items.map(v=>number(v.value) ?? 0)));
    return items.map(item=>`<div class="rv-ranking-row"><div><strong>${E(text(item.name || item.label,200))}</strong><span>${E(number(item.value)===null?"—":`${fmt(item.value)}${node.max?"/"+fmt(max):node.unit?" "+text(node.unit,30):""}`)}</span></div><div class="rv-track" role="meter" aria-label="${E(text(item.name || item.label,200))}" aria-valuemin="0" aria-valuemax="${max}"${number(item.value)!==null?` aria-valuenow="${Math.max(0,Math.min(max,item.value))}"`:""}><i style="width:${Math.max(0,Math.min(100,100*(number(item.value) ?? 0)/max))}%"></i></div>${item.detail || item.text?`<p>${inline(item.detail || item.text)}</p>`:""}</div>`).join("");
  }
  function dataTable(rows,columns) {
    return `<div class="rv-table-wrap"><table><thead><tr>${columns.map(c=>`<th scope="col">${E(text(c,180))}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map((_,i)=>`<td>${inline(Array.isArray(row)?row[i]:"")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  function chart(node) {
    const data=list(node.data,60).filter(d=>d && number(d.value)!==null),unit=text(node.unit,40);
    if(!data.length)return `<p class="rv-muted">${E(T("Нет числовых данных","No numeric data"))}</p>`;
    const labels=data.map(d=>text(d.label,80)),values=data.map(d=>d.value);let kind=node.chart || "bar";
    if(["pie","donut"].includes(kind) && values.some(v=>v<0))kind="bar";
    let drawing="";
    if(["pie","donut"].includes(kind)) {
      const total=values.reduce((a,b)=>a+Math.max(0,b),0);let offset=0;
      const segments=total?data.map((d,i)=>{const part=Math.max(0,d.value)/total*100,circle=`<circle cx="100" cy="100" r="${kind==="pie"?40:65}" pathLength="100" fill="none" stroke="var(--rv-color-${i%6})" stroke-width="${kind==="pie"?80:24}" stroke-dasharray="${part} ${100-part}" stroke-dashoffset="${-offset}" transform="rotate(-90 100 100)"/>`;offset+=part;return circle;}).join(""):"";
      drawing=`<div class="rv-pie"><svg viewBox="0 0 200 200" role="img" aria-label="${E(text(node.title,180))}">${segments}</svg><ul>${data.map((d,i)=>`<li><i style="background:var(--rv-color-${i%6})"></i><span>${E(labels[i])}</span><strong>${fmt(d.value)}${E(unit)}</strong></li>`).join("")}</ul></div>`;
    } else {
      const width=640,height=230,left=48,right=20,top=18,bottom=35,min=Math.min(0,...values),max=Math.max(1,...values),span=max-min;
      const x=i=>left+(i+.5)*(width-left-right)/data.length,y=v=>height-bottom-(v-min)/span*(height-top-bottom);
      const grid=[min,(min+max)/2,max].map(v=>`<line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" class="rv-chart-grid"/><text x="${left-8}" y="${y(v)+4}" text-anchor="end">${E(fmt(v))}</text>`).join("");
      const marks=kind==="line"?`<polyline fill="none" stroke="var(--accent)" stroke-width="3" points="${values.map((v,i)=>`${x(i)},${y(v)}`).join(" ")}"/>`+values.map((v,i)=>`<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="var(--accent)"><title>${E(labels[i])}: ${fmt(v)}${E(unit)}</title></circle>`).join(""):
        values.map((v,i)=>{const w=Math.max(1,(width-left-right)/data.length*.68);return `<rect x="${x(i)-w/2}" y="${Math.min(y(0),y(v))}" width="${w}" height="${Math.max(1,Math.abs(y(v)-y(0)))}" rx="3" fill="var(--accent)"><title>${E(labels[i])}: ${fmt(v)}${E(unit)}</title></rect>`;}).join("");
      const names=labels.map((label,i)=>data.length<=12 || i%Math.ceil(data.length/10)===0?`<text x="${x(i)}" y="${height-10}" text-anchor="middle">${E(label.slice(0,13))}</text>`:"").join("");
      drawing=`<svg class="rv-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${E(text(node.title || T("Диаграмма","Chart"),180))}">${grid}${marks}${names}</svg>`;
    }
    return drawing+`<details class="rv-chart-data"><summary>${E(T("Таблица данных","Data table"))}</summary>${dataTable(data.map((d,i)=>[labels[i],fmt(d.value)+unit]),[T("Название","Label"),T("Значение","Value")])}</details>`;
  }
  function renderNode(node,ctx,depth=0) {
    if(depth>MAX_DEPTH || ++ctx.count>MAX_NODES)return `<p class="rv-muted">${E(T("Часть блоков скрыта: достигнут предел размера.","Some blocks were omitted at the size limit."))}</p>`;
    if(!node || typeof node!=="object")return "";
    const type=node.type || "text",key="n"+(ctx.index++),title=text(node.title,240),body=text(node.text || node.description),children=node.blocks || node.children || [];
    const inner=(blocks,d=depth+1)=>list(blocks,50).map(b=>renderNode(b,ctx,d)).join("");
    const heading=title?`<h4>${inline(title)}</h4>`:"";
    switch(type) {
      case "stack":return `<div class="rv-stack">${heading}${inner(children)}</div>`;
      case "heading":return `<h4 class="rv-heading">${inline(title || body)}</h4>`;
      case "columns":case "row":case "grid":return `<div class="rv-layout ${type}">${inner(node.columns || children)}</div>`;
      case "flow":return `<section class="rv-flow">${heading}${list(node.steps || children,30).map((step,i)=>`${i?`<div class="rv-flow-arrow">${icon("arrow")}</div>`:""}${renderNode({...step,type:step.type || "step"},ctx,depth+1)}`).join("")}</section>`;
      case "step":return `<div class="rv-step">${icon(node.icon || "note")}<strong>${inline(title || node.label)}</strong>${body?`<p class="rv-muted">${inline(body)}</p>`:""}${inner(children)}</div>`;
      case "branch":return `<section class="rv-branch"><div class="rv-step">${icon(node.icon || "branch")}<strong>${inline(title || node.label)}</strong>${body?`<p>${inline(body)}</p>`:""}</div><div class="rv-branches">${list(node.branches || node.items,6).map(b=>`<div><span class="rv-branch-label ${tone(b.tone)}">${E(text(b.label,100))}</span>${inner(b.blocks || [{type:"text",text:b.text || b.title || ""}])}</div>`).join("")}</div></section>`;
      case "ranking":return `<section class="rv-ranking" data-rv-ranking="${enc(node)}" data-rv-node="${key}"><header>${heading}<div class="rv-segments" role="group" aria-label="${E(T("Сортировка","Sort order"))}"><button type="button" data-rv-sort="value" aria-pressed="${node.sort!=="name"}">${E(T("По значению","By value"))}</button><button type="button" data-rv-sort="name" aria-pressed="${node.sort==="name"}">${E(T("По имени","By name"))}</button></div></header><div class="rv-ranking-body">${rankingRows(node,node.sort || "value")}</div></section>`;
      case "comparison":return `<section class="rv-comparison">${heading}<div class="rv-comparison-items">${list(node.items || node.columns,6).map(item=>`<article>${item.image?imageMarkup(item.image,item.name || item.title):icon(item.icon || "sparkle")}<h4>${inline(item.name || item.title)}</h4>${item.value?`<p class="rv-value">${E(text(item.value,160))}</p>`:""}${item.text || item.description?`<p class="rv-muted">${inline(item.text || item.description)}</p>`:""}${item.badge?`<span class="rv-badge ${tone(item.tone)}">${E(text(item.badge,160))}</span>`:""}${inner(item.blocks)}</article>`).join("")}</div>${inner(children)}</section>`;
      case "cards":return `<section>${heading}<div class="rv-cards">${list(node.items,24).map(c=>renderNode({...c,type:c.entity?"entity":"card",...(c.entity || {})},ctx,depth+1)).join("")}</div></section>`;
      case "card":return `<article class="rv-card">${imageMarkup(node.image,title)}${heading}${body?`<p>${inline(body)}</p>`:""}${inner(children)}${safeUrl(node.url)?`<a href="${E(safeUrl(node.url))}" target="_blank" rel="noopener noreferrer">${E(text(node.link_label || T("Открыть источник","Open source"),100))}</a>`:""}</article>`;
      case "entity":return entityCard(node.entity || node);
      case "timeline":return `<section>${heading}<ol class="rv-timeline">${list(node.items,50).map(item=>`<li><span>${E(text(item.date || item.label,100))}</span><div><strong>${inline(item.title)}</strong>${item.text?`<p>${inline(item.text)}</p>`:""}${inner(item.blocks)}</div></li>`).join("")}</ol></section>`;
      case "stat":return `<div class="rv-stat"><span class="rv-muted">${E(text(node.label || title,160))}</span><strong>${E(text(node.value,160))}</strong>${node.detail?`<small>${inline(node.detail)}</small>`:""}</div>`;
      case "metrics":return `<section>${heading}<div class="rv-metrics">${list(node.items,12).map(item=>renderNode({...item,type:"stat"},ctx,depth+1)).join("")}</div></section>`;
      case "badge":return `<span class="rv-badge ${tone(node.tone)}">${inline(body || node.label || title)}</span>`;
      case "callout":case "quote":return `<aside class="rv-callout ${tone(node.tone)}">${node.icon?icon(node.icon):""}<div>${heading}${body?`<p>${inline(body)}</p>`:""}${inner(children)}</div></aside>`;
      case "list":return `<section>${heading}<${node.ordered?"ol":"ul"} class="rv-list">${list(node.items,60).map(item=>`<li>${inline(typeof item==="object"?item.text || item.label:item)}</li>`).join("")}</${node.ordered?"ol":"ul"}></section>`;
      case "table":return `<section>${heading}${dataTable(list(node.rows,60),list(node.columns,12))}</section>`;
      case "image":return `<figure class="rv-figure">${imageMarkup(node.url || node.image,node.alt || title)}${node.caption || title?`<figcaption>${inline(node.caption || title)}</figcaption>`:""}</figure>`;
      case "chart":return `<section>${heading}${chart(node)}</section>`;
      case "tabs":return `<section class="rv-tabs" data-rv-node="${key}">${heading}<div class="rv-tab-list" role="tablist" aria-label="${E(title || T("Варианты","Options"))}">${list(node.items,8).map((item,i)=>`<button type="button" role="tab" data-rv-tab="${i}" aria-selected="${i===0}" tabindex="${i===0?0:-1}">${E(text(item.label || item.title,120))}</button>`).join("")}</div>${list(node.items,8).map((item,i)=>`<div role="tabpanel" data-rv-tab-panel="${i}"${i?" hidden":""}>${inner(item.blocks)}</div>`).join("")}</section>`;
      case "accordion":return `<section class="rv-accordion">${heading}${list(node.items,20).map(item=>`<details><summary>${inline(item.title || item.label)}</summary>${inner(item.blocks || [{type:"text",text:item.text}])}</details>`).join("")}</section>`;
      case "calculator": {
        const inputs=list(node.inputs,8).filter(i=>/^[a-zA-Z_][\w]{0,39}$/.test(i?.id || ""));
        const values=Object.fromEntries(inputs.map(i=>[i.id,number(i.value) ?? number(i.min) ?? 0]));let result="—";
        try{result=fmt(calculate(text(node.formula,300),values));}catch{}
        return `<section class="rv-calculator" data-rv-formula="${E(text(node.formula,300))}" data-rv-unit="${E(text(node.unit,50))}" data-rv-node="${key}">${heading}<div class="rv-calc-inputs">${inputs.map(i=>`<label><span>${E(text(i.label || i.id,120))}</span><input type="number" data-rv-input="${E(i.id)}" value="${values[i.id]}"${number(i.min)!==null?` min="${i.min}"`:""}${number(i.max)!==null?` max="${i.max}"`:""} step="${number(i.step) ?? 1}"></label>`).join("")}</div><output aria-live="polite">${E(result)} ${E(text(node.unit,50))}</output></section>`;
      }
      case "divider":return "<hr>";
      case "progress": {const min=number(node.min) ?? 0,max=Math.max(min+1,number(node.max) ?? 100),v=Math.max(min,Math.min(max,number(node.value) ?? min));return `<div class="rv-progress"><span>${E(text(node.label || title,180))}</span><strong>${fmt(v)} / ${fmt(max)}</strong><progress min="${min}" max="${max-min}" value="${v-min}" aria-label="${E(text(node.label || title,180))}"></progress></div>`;}
      case "code":return `<pre class="rv-code"><code>${E(body)}</code></pre>`;
      default:return `<div class="rv-text ${tone(node.tone)}">${heading}${body?`<p>${inline(body)}</p>`:""}${inner(children)}</div>`;
    }
  }
  function parse(raw) {
    if(typeof raw==="object" && raw)return raw;
    if(typeof raw!=="string" || raw.length>MAX_RAW)return null;
    try {const v=JSON.parse(raw);return v && typeof v==="object" && !Array.isArray(v)?v:null;}catch{return null;}
  }
  function plain(node,depth=0) {
    if(!node || typeof node!=="object" || depth>MAX_DEPTH)return "";
    const child=nodes=>list(nodes,60).map(n=>plain(n,depth+1)).filter(Boolean).join("\n\n");
    const heading=text(node.title || node.name,240),out=[];if(heading)out.push(heading);
    if(node.summary)out.push(text(node.summary,2400));
    if(node.text || node.description)out.push(text(node.text || node.description,5000));
    const type=node.type;
    if(type==="entity") {const e=entity(node.entity || node);return [e.name,e.value,e.subtitle,e.summary,e.description,...e.facts.map(f=>`${f.label}: ${f.value}`),...e.sources.map(s=>`${s.title}: ${s.url}`)].filter(Boolean).join("\n");}
    if(type==="ranking")out.push(...list(node.items,60).map(i=>`- ${text(i.name || i.label)}: ${fmt(i.value)}${node.max?"/"+fmt(node.max):""}${i.detail || i.text?" — "+text(i.detail || i.text,500):""}`));
    else if(type==="table")out.push([list(node.columns,12).map(c=>text(c)).join(" | "),...list(node.rows,60).map(row=>list(row,12).map(c=>text(c,300)).join(" | "))].join("\n"));
    else if(type==="chart")out.push(...list(node.data,60).map(d=>`${text(d.label)}: ${fmt(d.value)}${node.unit?" "+text(node.unit,40):""}`));
    else if(type==="list")out.push(...list(node.items,60).map(i=>"- "+text(typeof i==="object"?i.text || i.label:i,1000)));
    else if(type==="calculator") {try{out.push(`${fmt(calculate(node.formula,Object.fromEntries(list(node.inputs,8).map(i=>[i.id,number(i.value) ?? number(i.min) ?? 0]))))} ${text(node.unit,40)}`);}catch{}out.push(...list(node.inputs,8).map(i=>`${text(i.label || i.id)}: ${i.value ?? i.min ?? 0}`));}
    else if(node.value!==undefined)out.push(text(node.value,300));
    if(node.detail)out.push(text(node.detail,600));
    if(node.badge)out.push(text(node.badge,160));
    if(node.url && type==="image")out.push(text(node.caption || node.alt,400));
    const nested=node.steps || node.blocks || node.children || node.columns || (type==="branch"?node.branches:node.items);
    if(!["ranking","list","table","chart","calculator"].includes(type))out.push(child(list(nested,60).map(n=>typeof n==="object"?{...n,title:n.title || n.name || n.label}:null)));
    if(node.footer)out.push(text(node.footer,1000));
    return out.filter(Boolean).join("\n\n").replace(/\n{3,}/g,"\n\n").slice(0,12000);
  }
  function render(raw) {
    const value=parse(raw);if(!value)return "";
    if(value.type==="custom" || typeof value.html==="string")return null;
    const source=typeof raw==="string"?raw:JSON.stringify(raw);
    if(source.length>MAX_RAW)return `<p class="rv-muted">${E(T("Слишком большой блок","This block is too large"))}</p>`;
    if(!enabled())return `<div class="rv-plain">${E(plain(value))}</div>`;
    const key=cacheKey()+source;const kept=cache.get(key);if(kept)return kept;
    const root=value.type==="text" && (value.blocks || value.children)?{...value,type:"stack"}:value;
    const id="rv"+(++serial),body=renderNode(root,{count:0,index:0});
    const html=`<div class="memuai-visual rv-visual" data-rich-id="${id}" data-rich-source="${enc(value)}">${body}${value.footer?`<footer class="rv-footer">${inline(value.footer)}</footer>`:""}</div>`;
    cache.set(key,html);if(cache.size>100)cache.delete(cache.keys().next().value);return html;
  }
  function prepareText(raw) {
    const source=String(raw || ""),definitions=new Map(),tokens=[];let collected=0;
    const collect=(node,depth=0)=>{if(!node || typeof node!=="object" || depth>MAX_DEPTH || ++collected>MAX_NODES)return;if(node.type==="entity" && (node.id || node.entity?.id)){const item=entity(node.entity || node);definitions.set(item.id,item);}for(const key of ["entities","blocks","children","steps","columns","items","branches"])for(const item of list(node[key],60)){if(key==="entities" && item?.id)definitions.set(text(item.id,80),entity(item));collect(item,depth+1);}};
    source.replace(/```[ \t]*memuai[-_](visual|entity)[ \t]*\r?\n([\s\S]*?)```/gi,(_,kind,body)=>{const value=parse(body);if(value){if(kind.toLowerCase()==="entity" && value.id)definitions.set(text(value.id,80),entity(value));collect(value);}return _;});
    const transformed=source.replace(/(```[\s\S]*?```|`[^`\n]*`)|\[\[entity:([\w-]{1,80})(?:\|([^\]\n]{1,160}))?\]\]/g,(all,code,id,label)=>{
      if(code)return all;const item=definitions.get(id) || {id,name:label || id};
      const token="@@RVINLINE"+tokens.length+"@@";tokens.push(enabled()?entityButton(item,label || item.name):E(label || item.name));return token;
    });
    return {source:transformed,finish:html=>String(html).replace(/@@RVINLINE(\d+)@@/g,(all,i)=>tokens[+i] ?? all)};
  }
  function stripInline(raw) {return String(raw || "").replace(/(```[\s\S]*?```|`[^`\n]*`)|\[\[entity:([\w-]{1,80})(?:\|([^\]\n]{1,160}))?\]\]/g,(all,code,id,label)=>code || label || id);}
  function instanceKey(host) {
    const message=host.closest('[data-msg]'),scope=message || host.closest('#messages') || host.parentElement;
    const siblings=[...(scope?.querySelectorAll('.rv-visual') || [])];
    const profile=typeof S!=="undefined"?S.profileState?.active_id || "default":"default";
    const chat=typeof S!=="undefined"?S.currentId || "new":"new";
    return profile+":"+chat+":"+(message?.dataset.msg || "root")+":"+siblings.indexOf(host)+":"+host.dataset.richId;
  }
  function collectHostState(host) {
    const id=instanceKey(host);
    host.querySelectorAll("[data-rv-node]").forEach(node=>{
      const state=interactions.get(id+":"+node.dataset.rvNode);
      if(node.matches('.rv-tabs')) {const uid='rv-tab-'+(++serial);node.querySelectorAll(':scope > .rv-tab-list > button').forEach((button,i)=>{button.id=uid+'-'+i;button.setAttribute('aria-controls',uid+'-panel-'+i);});node.querySelectorAll(':scope > [data-rv-tab-panel]').forEach((panel,i)=>{panel.id=uid+'-panel-'+i;panel.setAttribute('aria-labelledby',uid+'-'+i);panel.tabIndex=0;});}
      if(!state)return;
      if(node.matches("[data-rv-ranking]"))applySort(node,state.sort || "value",false);
      if(node.matches(".rv-tabs"))selectTab(node,state.tab || 0,false);
      if(node.matches(".rv-calculator") && state.inputs){node.querySelectorAll("input[data-rv-input]").forEach(i=>{if(state.inputs[i.dataset.rvInput]!==undefined)i.value=state.inputs[i.dataset.rvInput];});updateCalculator(node,false);}
    });
  }
  function hydrate(root) {(root || document).querySelectorAll(".rv-visual[data-rich-id]").forEach(collectHostState);}
  function remember(node,patch) {const host=node.closest("[data-rich-id]");if(!host)return;const key=instanceKey(host)+":"+node.dataset.rvNode;interactions.set(key,{...(interactions.get(key) || {}),...patch});if(interactions.size>250)interactions.delete(interactions.keys().next().value);}
  function applySort(node,sort,save=true) {const data=decode(node.dataset.rvRanking);if(!data)return;node.querySelector(".rv-ranking-body").innerHTML=rankingRows(data,sort);node.querySelectorAll("[data-rv-sort]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.rvSort===sort)));if(save)remember(node,{sort});}
  function selectTab(node,index,save=true) {const buttons=[...node.querySelectorAll(":scope > .rv-tab-list > button")];if(index<0 || index>=buttons.length)return;buttons.forEach((b,i)=>{b.setAttribute("aria-selected",String(i===index));b.tabIndex=i===index?0:-1;});node.querySelectorAll(":scope > [data-rv-tab-panel]").forEach(p=>p.hidden=+p.dataset.rvTabPanel!==index);if(save)remember(node,{tab:index});}
  function updateCalculator(node,save=true) {const values={};let valid=true;node.querySelectorAll("[data-rv-input]").forEach(input=>{values[input.dataset.rvInput]=input.value===""?NaN:Number(input.value);if(!input.checkValidity())valid=false;});let result="—";try{if(valid)result=fmt(calculate(node.dataset.rvFormula,values));}catch{}node.querySelector("output").textContent=result+" "+(node.dataset.rvUnit || "");if(save)remember(node,{inputs:values});}
  function closeSheet() {if(!sheet)return;sheet.remove();sheet=null;if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true});returnFocus=null;}
  function openEntity(value,trigger) {
    closeSheet();const item=entity(value);returnFocus=trigger || document.activeElement;
    document.dispatchEvent(new CustomEvent("memu:side-sheet",{detail:{owner:"rich-visuals"}}));
    sheet=document.createElement("aside");sheet.className="rv-sheet";sheet.setAttribute("role","dialog");sheet.setAttribute("aria-label",item.name || T("Сведения","Details"));
    const pictures=[item.image,...item.images].filter(Boolean).slice(0,6);
    sheet.innerHTML=`<header><span>${E(T("Сведения из ответа","Details from this answer"))}</span><button type="button" data-rv-close aria-label="${E(T("Закрыть","Close"))}">${icon("close")}</button></header><div class="rv-sheet-body">${pictures.length?`<div class="rv-sheet-gallery">${pictures.map(p=>imageMarkup(p,item.name)).join("")}</div>`:""}<h2>${E(item.name)}</h2>${item.subtitle?`<p class="rv-muted">${E(item.subtitle)}</p>`:""}${item.badge?`<span class="rv-badge ${item.tone}">${E(item.badge)}</span>`:""}${item.value?`<strong class="rv-value">${E(item.value)}</strong>`:""}<p>${inline(item.summary || T("Описание этого понятия не было приложено к ответу.","This answer did not include a description for this term."))}</p>${item.description?`<p class="rv-description">${E(item.description)}</p>`:""}${item.facts.length?`<dl>${item.facts.map(f=>`<div><dt>${E(f.label)}</dt><dd>${E(f.value)}</dd></div>`).join("")}</dl>`:""}${item.sources.length?`<section><h3>${E(T("Источники","Sources"))}</h3>${item.sources.map(s=>`<a href="${E(s.url)}" target="_blank" rel="noopener noreferrer">${E(s.title || new URL(s.url).hostname)}${icon("link")}</a>`).join("")}</section>`:""}${item.url?`<a class="rv-source-link" href="${E(item.url)}" target="_blank" rel="noopener noreferrer">${E(T("Открыть сайт","Open website"))}${icon("link")}</a>`:""}${item.related.length?`<section><h3>${E(T("Связанные понятия","Related terms"))}</h3>${item.related.map(r=>entityButton(r,r.name)).join(" ")}</section>`:""}</div><footer>${E(T("Сводка предоставлена моделью в этом сообщении. Проверяйте значимые сведения по источникам.","The summary was supplied by the model in this message. Check important facts against its sources."))}</footer>`;
    document.body.appendChild(sheet);sheet.querySelector("[data-rv-close]").focus({preventScroll:true});
  }
  document.addEventListener("click",event=>{
    const target=event.target.closest?.("[data-rv-entity],[data-rv-sort],[data-rv-tab],[data-rv-close]");if(!target)return;
    if(target.hasAttribute("data-rv-close"))return closeSheet();
    if(target.dataset.rvEntity)return openEntity(decode(target.dataset.rvEntity),target);
    if(target.dataset.rvSort)return applySort(target.closest("[data-rv-ranking]"),target.dataset.rvSort);
    if(target.dataset.rvTab!==undefined)selectTab(target.closest(".rv-tabs"),+target.dataset.rvTab);
  });
  document.addEventListener("input",event=>{if(event.target.matches?.("[data-rv-input]"))updateCalculator(event.target.closest(".rv-calculator"));});
  document.addEventListener("keydown",event=>{
    if(event.key==="Escape" && sheet){event.preventDefault();event.stopPropagation();closeSheet();return;}
    const button=event.target.closest?.("[data-rv-tab]");if(!button || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
    const host=button.closest(".rv-tabs"),buttons=[...host.querySelectorAll(":scope > .rv-tab-list > button")],index=buttons.indexOf(button),next=event.key==="Home"?0:event.key==="End"?buttons.length-1:(index+(event.key==="ArrowRight"?1:-1)+buttons.length)%buttons.length;
    event.preventDefault();selectTab(host,next);buttons[next].focus();
  },true);
  document.addEventListener("memu:side-sheet",event=>{if(event.detail?.owner!=="rich-visuals")closeSheet();});
  document.addEventListener("memu:chat-open",closeSheet);
  return Object.freeze({render,parse,plain,validate,schema,prepareText,stripInline,entityCard,hydrate,cacheKey,enabled,customEnabled,imagesEnabled,openEntity,closeSheet,calculate});
})();
window.MemuRichVisuals=MemuRichVisuals;
