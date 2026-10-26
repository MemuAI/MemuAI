
import { esc, icon, I18N, modal, toast, t, countWords } from "../core.js";
import { THEMES } from "./templates.js";
import { plainText } from "./pptx.js";
import { renderSlide, fitTexts } from "./render.js";
import { ensureOnline, progressModal, streamTask, parseJsonLoose, linesToParas } from "./slides-ai.js";

const L = (ru, en) => I18N.lang === "ru" ? ru : en;
const walk = (elements, fn, depth = 0) => { for (const el of elements || []) { fn(el, depth); if (el.children) walk(el.children, fn, depth + 1); } };
const textOf = el => el.text ? plainText(el.text) : "";
export const STARTERS = [
  { id:"blank", ru:"С чистого листа", en:"Blank", description_ru:"Один титульный слайд", description_en:"One title slide" },
  { id:"pitch", ru:"Идея и решение", en:"Pitch", description_ru:"Проблема, решение, доказательства и следующий шаг", description_en:"Problem, solution, evidence and the next step" },
  { id:"report", ru:"Рабочий отчёт", en:"Report", description_ru:"Выводы, факты, риски и план действий", description_en:"Findings, evidence, risks and an action plan" },
  { id:"lesson", ru:"Урок или выступление", en:"Lesson or talk", description_ru:"Цель, объяснение, пример и практика", description_en:"Objective, explanation, example and practice" },
];
export function starterSpecs(id, lang = I18N.lang) {
  const x = (ru,en) => lang === "ru" ? ru : en;
  if (id === "pitch") return [
    {layout:"title",title:x("Название проекта","Project title"),subtitle:x("Что вы предлагаете и кому","What you offer and who it is for")},
    {layout:"content",title:x("Какую проблему решаем","The problem"),bullets:[x("Кто сталкивается с проблемой","Who experiences the problem"),x("Почему существующего подхода недостаточно","Why the current approach falls short"),x("Какими фактами это подтверждается","Evidence that supports the need")]},
    {layout:"two",title:x("Наше решение","Our solution"),left_title:x("Что предлагаем","What we offer"),right_title:x("Что получит пользователь","What the user gains"),left:[x("Опишите ключевую идею","Describe the core idea")],right:[x("Назовите конкретный результат","Name the concrete outcome")]},
    {layout:"table",title:x("Проверяем предположения","Validate assumptions"),table:[[x("Предположение","Assumption"),x("Доказательство","Evidence")],[x("Добавьте предположение","Add an assumption"),x("Добавьте источник или результат проверки","Add a source or test result")]]},
    {layout:"content",title:x("План действий","Action plan"),bullets:[x("Первый проверяемый результат","First verifiable result"),x("Ответственные и сроки","Owners and dates"),x("Как оценим успех","How success will be measured")]},
    {layout:"statement",title:x("Какой следующий шаг вы предлагаете?","What is the next step?"),subtitle:x("Сформулируйте конкретный запрос к аудитории","Make one concrete request of the audience")},
  ];
  if (id === "report") return [
    {layout:"title",title:x("Итоги периода","Period review"),subtitle:x("Команда, период и цель отчёта","Team, period and the purpose of this review")},
    {layout:"content",title:x("Главные выводы","Key findings"),bullets:[x("Что удалось и почему","What worked and why"),x("Что изменилось относительно плана","What changed against the plan"),x("Какое решение требуется","The decision needed")]},
    {layout:"table",title:x("Данные и источники","Evidence and sources"),table:[[x("Показатель","Measure"),x("Факт","Actual"),x("Источник","Source")],[x("Введите показатель","Enter a measure"),x("Введите проверенные данные","Enter verified data"),x("Укажите источник","Cite the source")]]},
    {layout:"two",title:x("Риски и возможности","Risks and opportunities"),left_title:x("Что мешает","Obstacles"),right_title:x("Что поможет","Opportunities"),left:[x("Риск и его влияние","Risk and its impact")],right:[x("Действие для снижения риска","Mitigation action")]},
    {layout:"content",title:x("Следующий период","Next period"),bullets:[x("Приоритет и ожидаемый результат","Priority and expected outcome"),x("Ответственные и срок","Owner and deadline"),x("Критерий проверки","Verification criterion")]},
  ];
  if (id === "lesson") return [
    {layout:"title",title:x("Тема выступления","Talk title"),subtitle:x("Для кого и чему научимся","Audience and learning objective")},
    {layout:"content",title:x("Что разберём","What we will cover"),bullets:[x("Главный вопрос","The main question"),x("Ключевое понятие","The key concept"),x("Применение на практике","Practical application")]},
    {layout:"statement",title:x("Объясните основную идею одной фразой","Explain the central idea in one sentence"),subtitle:x("Затем раскройте её в заметках докладчика","Expand on it in the speaker notes")},
    {layout:"two",title:x("Пример и объяснение","Example and explanation"),left_title:x("Пример","Example"),right_title:x("Как это работает","How it works"),left:[x("Добавьте конкретный пример","Add a concrete example")],right:[x("Покажите связь с основной идеей","Connect it to the central idea")]},
    {layout:"content",title:x("Попробуйте сами","Try it"),bullets:[x("Сформулируйте небольшое задание","Set a short exercise"),x("Укажите ограничения","State the constraints"),x("Опишите проверяемый результат","Describe a verifiable outcome")]},
    {layout:"content",title:x("Что запомнить","What to remember"),bullets:[x("Основной вывод","The main takeaway"),x("Когда это применять","When to use it"),x("Где узнать больше","Where to learn more")]},
  ];
  return [];
}

export function auditDeck(deck) {
  const issues = []; let words = 0, pictures = 0, charts = 0;
  const add = (slide, el, kind, title, detail, severity = "note") => issues.push({slide,element:el?.id || "",kind,title,detail,severity});
  deck.slides.forEach((slide,index) => {
    let slideWords = 0, hasTitle = false, visible = false;
    walk(slide.elements,(el,depth) => {
      const text = textOf(el).trim(); slideWords += countWords(text);
      if (text || ["pic","chart","table","raw"].includes(el.kind)) visible = true;
      if (text && ["title","ctrTitle"].includes(el.ph?.type)) hasTitle = true;
      if (el.kind === "pic") { pictures++; if (!String(el.descr || "").trim()) add(index,el,"alt",L("У изображения нет описания","Picture needs alternative text"),L("Добавьте замещающий текст в панели формата.","Add alternative text in the format panel.")); }
      if (el.kind === "chart") { charts++; if (!(el.chart?.series || []).length) add(index,el,"data",L("Нет данных диаграммы","Chart has no data"),L("Введите категории и ряды данных.","Enter categories and data series."),"warning"); }
      if (el.kind === "raw") add(index,el,"unsupported",L("Специальный объект Office","Special Office object"),L("Исходный объект сохраняется в PPTX, но его отображение в редакторе и PDF может отличаться.","The original object stays in PPTX; its editor and PDF appearance may differ."));
      if (!depth && Number.isFinite(el.w) && Number.isFinite(el.h)) {
        const b = deck.boxOf(slide,el);
        if (b.x < -10000 || b.y < -10000 || b.x + b.w > deck.w + 10000 || b.y + b.h > deck.h + 10000) add(index,el,"bounds",L("Объект выходит за слайд","Object extends beyond the slide"),L("Проверьте положение и размер объекта.","Check the object’s position and size."),"warning");
      }
      if (text) {
        const levels = deck.levelStyles(slide,el); let smallest = Infinity;
        for (const p of el.text.paras || []) for (const r of p.runs || []) if (String(r.t || "").trim()) smallest = Math.min(smallest,Number(r.sz ?? p.endSz ?? levels[p.lvl || 0]?.r?.sz ?? 18));
        if (smallest < 14) add(index,el,"font",L("Мелкий текст","Small text"),L(`Размер ${smallest} пт: проверьте читаемость на экране.`,`${smallest} pt: check readability on the presentation screen.`));
      }
    });
    words += slideWords;
    if (!visible) add(index,null,"empty",L("Пустой слайд","Empty slide"),L("Добавьте содержимое или удалите слайд.","Add content or remove the slide."),"warning");
    else if (!hasTitle) add(index,null,"title",L("Проверьте заголовок","Check the title"),L("Явный заголовок помогает аудитории следить за ходом мысли.","A clear title helps the audience follow the argument."));
    if (slideWords > 80) add(index,null,"density",L("Много текста на слайде","Text-heavy slide"),L(`${slideWords} слов: часть текста можно перенести в заметки.`,`${slideWords} words: consider moving detail into speaker notes.`));
    if (slide.hidden) add(index,null,"hidden",L("Скрытый слайд","Hidden slide"),L("По умолчанию он не попадёт в показ и экспорт PDF.","Excluded from the show and PDF export by default."));
  });
  return {issues,words,pictures,charts,minutes:Math.max(1,Math.ceil(words/110)),slides:deck.slides.length};
}

export async function reviewDialog(view, ai = false) {
  if (view.editing) view.endEdit();
  const deck = view.deck, report = auditDeck(deck);
  let issues = report.issues, summary = L("Проверьте оформление и структуру перед показом. Все рекомендации остаются под вашим контролем.","Review structure and readability before presenting. You choose which changes to make.");
  if (ai) {
    if (!(await ensureOnline())) return;
    const pm = progressModal(L("ИИ проверяет презентацию…","AI reviews the presentation…"));
    try {
      const slides = deck.slides.slice(0,50).map((s,i) => ({n:i+1,hidden:!!s.hidden,text:s.elements.map(textOf).filter(Boolean).join("\n").slice(0,2000),notes:String(s.notes || "").slice(0,1000)}));
      const data = parseJsonLoose(await streamTask("review",{slides,lang:I18N.lang,total:deck.slides.length},{signal:pm.signal}));
      summary = String(data.summary || summary);
      issues = (Array.isArray(data.issues)?data.issues:[]).slice(0,50).filter(i => Number.isInteger(i.slide) && i.slide>=1 && i.slide<=slides.length).map(i => ({slide:i.slide-1,element:"",kind:"ai",severity:i.severity==="warning"?"warning":"note",title:String(i.title || ""),detail:String(i.detail || i.suggestion || "")})).filter(i=>i.title);
      if (deck.slides.length>50) summary += " " + L("ИИ рассмотрел первые 50 слайдов.","AI reviewed the first 50 slides.");
    } finally {pm.close();}
  }
  await modal({title:ai?L("Советы ИИ по презентации","AI presentation review"):L("Проверка презентации","Presentation check"),wide:true,body:`<div class="sl-studio"><p class="sl-studio-intro">${esc(summary)}</p><div class="sl-review-metrics"><span><b>${report.slides}</b>${esc(L("слайдов","slides"))}</span><span><b>${report.words}</b>${esc(L("слов","words"))}</span><span><b>${report.pictures}</b>${esc(L("изображений","pictures"))}</span><span><b>${report.charts}</b>${esc(L("диаграмм","charts"))}</span></div><div class="sl-review-list">${issues.map((issue,i)=>`<button class="sl-review-issue ${issue.severity}" data-sl-issue="${i}"><span class="sl-review-number">${issue.slide+1}</span><span><strong>${esc(issue.title)}</strong><small>${esc(issue.detail)}</small></span>${icon("chevron")}</button>`).join("") || `<div class="sl-review-clear">${icon("check")} ${esc(L("Замечаний не найдено. Проверьте содержание и источники перед выступлением.","No issues found. Check content and sources before presenting."))}</div>`}</div></div>`,
    onOpen:(box,done)=>box.addEventListener("click",e=>{const row=e.target.closest("[data-sl-issue]");if(!row)return;const issue=issues[+row.dataset.slIssue];done("focus");view.goto(issue.slide);if(issue.element)view.select([issue.element]);view.focusStage();}),
    buttons:[{label:t("close"),value:null,primary:true}]});
}

export async function themeGallery(view) {
  let selected = "";
  await view.deck.preloadMedia();
  const choice = await modal({title:L("Тема вашей презентации","Choose a presentation theme"),wide:true,body:`<div class="sl-studio"><p class="sl-studio-intro">${esc(L("Посмотрите, как текущий слайд выглядит с другой палитрой и шрифтами. Содержание сохранится.","Preview the current slide with a different palette and fonts. Content stays in place."))}</p><div class="sl-studio-themes">${THEMES.map(th=>`<button type="button" data-studio-theme="${th.id}" aria-pressed="false"><span class="sl-studio-theme-preview"></span><strong>${esc(th[I18N.lang] || th.en)}</strong><span class="sl-studio-swatches">${[th.colors.accent1,th.colors.accent2,th.colors.accent3].map(c=>`<i style="background:${c}"></i>`).join("")}</span></button>`).join("")}</div></div>`,
    onOpen:box=>{
      for(const th of THEMES) {
        const preview=Object.create(view.deck);
        preview.masters=view.deck.masters.map(m=>{const c={...th.colors};if((m.clrMap.bg1==="lt1")===!!th.dark){[c.dk1,c.lt1]=[c.lt1,c.dk1];[c.dk2,c.lt2]=[c.lt2,c.dk2];}return {...m,theme:{...m.theme,colors:c,fonts:{major:th.fonts[0],minor:th.fonts[1]},name:th.en}};});
        const host=box.querySelector(`[data-studio-theme="${th.id}"] .sl-studio-theme-preview`);
        const width=Math.max(180,host.clientWidth || 220);
        const node=renderSlide(preview,view.slide,{scale:width/view.deck.w,slideNo:view.cur+1});host.append(node);fitTexts(node);
      }
      box.addEventListener("click",e=>{const button=e.target.closest("[data-studio-theme]");if(!button)return;selected=button.dataset.studioTheme;box.querySelectorAll("[data-studio-theme]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));});
    },buttons:[{label:t("cancel"),value:null},{label:L("Применить тему","Apply theme"),primary:true,value:()=>selected}]});
  if(choice)view.applyTheme(choice);
}

export async function customRewrite(view) {
  if(view.editing)view.endEdit();
  if(!(await ensureOnline()))return;
  const request=await modal({title:L("Изменить с помощью ИИ","Revise with AI"),wide:true,body:`<div class="sl-studio"><label class="field"><span>${esc(L("Что изменить","What should change"))}</span><textarea class="input" id="sl-ai-instruction" rows="3" maxlength="4000" placeholder="${esc(L("Например, сделай объяснение понятнее для новой аудитории, сохрани факты","For example, make the explanation clearer for a new audience while preserving the facts"))}" autofocus></textarea></label><label class="field"><span>${esc(L("Область изменений","Scope"))}</span><select class="input" id="sl-ai-scope"><option value="slide">${esc(L("Текущий слайд","Current slide"))}</option><option value="selection" ${view.selected().length?"":"disabled"}>${esc(L("Выбранные объекты","Selected objects"))}</option><option value="deck">${esc(L("Вся презентация","Whole presentation"))}</option></select></label><p class="muted small">${esc(L("Сначала появится черновик. Вы сможете выбрать изменения, применить их одним действием и отменить.","You will see a draft first. Select changes to apply together, then undo them if needed."))}</p></div>`,buttons:[{label:t("cancel"),value:null},{label:L("Подготовить черновик","Prepare draft"),primary:true,value:b=>({instruction:b.querySelector("#sl-ai-instruction").value.trim(),scope:b.querySelector("#sl-ai-scope").value})}]});
  if(!request?.instruction)return;
  const candidates=[], selected=new Set(view.sel);let chars=0;
  const slides=request.scope==="deck"?view.deck.slides:[view.slide];
  for(const slide of slides)walk(slide.elements,el=>{const text=textOf(el);if(!text.trim() || (request.scope==="selection" && !selected.has(el.id)))return;if(candidates.length>=60 || chars+text.length>45000)return;chars+=text.length;candidates.push({id:el.id,slide,el,before:text});});
  if(!candidates.length){toast(t("sl_ai_no_text"));return;}
  const pm=progressModal(L("ИИ готовит черновик…","AI prepares a draft…"));let data;
  try{data=parseJsonLoose(await streamTask("revise",{instruction:request.instruction,items:candidates.map(c=>({id:c.id,text:c.before})),lang:I18N.lang},{signal:pm.signal}));}finally{pm.close();}
  const seen=new Set();
  const changes=(Array.isArray(data.items)?data.items:[]).map(item=>{const c=candidates.find(c=>c.id===item.id);if(!c || seen.has(item.id) || typeof item.text!=="string" || !item.text.trim() || item.text===c.before)return null;seen.add(item.id);return {...c,after:item.text.slice(0,12000)};}).filter(Boolean);
  if(!changes.length){toast(t("sl_ai_unchanged"));return;}
  const choice=await modal({title:L("Проверьте изменения","Review changes"),wide:true,body:`<div class="sl-studio"><p class="sl-studio-intro">${esc(L("ИИ предлагает изменения текста. Проверьте факты, числа и формулировки перед применением. За один запрос обрабатывается до 60 текстовых объектов и 45 тысяч символов.","Review facts, numbers and wording before applying the draft. One request processes up to 60 text objects and 45,000 characters."))}</p><div class="sl-rewrite-list">${changes.map((change,i)=>`<label class="sl-rewrite-item"><span class="sl-rewrite-head"><input type="checkbox" data-sl-change="${i}" checked> ${esc(L("Слайд","Slide"))} ${view.deck.slides.indexOf(change.slide)+1}</span><span class="sl-rewrite-columns"><span><small>${esc(L("Было","Before"))}</small><span>${esc(change.before)}</span></span><span><small>${esc(L("Предложение","Draft"))}</small><span>${esc(change.after)}</span></span></span></label>`).join("")}</div></div>`,buttons:[{label:t("cancel"),value:null},{label:L("Применить выбранное","Apply selected"),primary:true,value:b=>[...b.querySelectorAll("[data-sl-change]:checked")].map(n=>+n.dataset.slChange)}]});
  if(!choice?.length)return;
  const applicable=choice.map(i=>changes[i]).filter(c=>view.deck.slides.includes(c.slide) && textOf(c.el)===c.before);
  if(!applicable.length){toast(L("Документ изменился. Подготовьте новый черновик.","The document changed. Prepare a fresh draft."));return;}
  view.change(()=>{for(const c of applicable){c.el.text.paras=linesToParas(c.after.split("\n"),c.el.text.paras);view.touch(c.slide);}}, {all:true,thumbsAll:true});
  toast(t("sl_ai_applied"),{kind:"ok",action:{label:t("undo"),run:()=>view.undo()}});
}

const objectSnapshot = el => JSON.stringify(el, (key,value) => key.startsWith('_') ? undefined : value);
function describeObjectChange(c,W,H) {
  if(c.notes!==undefined)return c.notes;
  const p=c.patch,base=c.box || c.el,lines=[];
  if(['x','y','w','h'].some(k=>k in p)){
    const percent=(key,size)=>Math.round((p[key] ?? base[key] ?? 0)/size*100)+'%';
    lines.push(L('Расположение','Position')+': '+percent('x',W)+', '+percent('y',H));
    lines.push(L('Размер','Size')+': '+percent('w',W)+' × '+percent('h',H));
  }
  if(p.rot!==undefined)lines.push(L('Поворот','Rotation')+': '+p.rot+'°');
  if(p.text!==undefined)lines.push(L('Новый текст','New text')+':\n'+p.text);
  if(p.chart){
    lines.push(L('Диаграмма','Chart')+': '+p.chart.type);
    lines.push([L('Категория','Category'),...p.chart.series.map(s=>s.name)].join(' · '));
    p.chart.cats.forEach((cat,i)=>lines.push([cat,...p.chart.series.map(s=>s.vals[i])].join(' · ')));
  }
  return lines.join('\n');
}

export async function editObjects(view) {
  if(view.editing)view.endEdit();
  if(!(await ensureOnline()))return;
  const request=await modal({title:L("Редактировать объекты с ИИ","Edit objects with AI"),wide:true,body:`<label class="field"><span>${esc(L("Что изменить","What should change"))}</span><textarea class="input" id="sl-object-instruction" rows="4" maxlength="4000" placeholder="${esc(L("Выровняй изображения, перераспредели текст, исправь диаграмму или подготовь заметки докладчика","Align images, rearrange text, revise a chart or prepare speaker notes"))}"></textarea></label><label class="field"><span>${esc(L("Область","Scope"))}</span><select class="input" id="sl-object-scope"><option value="slide">${esc(L("Текущий слайд","Current slide"))}</option><option value="deck">${esc(L("Вся презентация","Whole presentation"))}</option></select></label>`,buttons:[{label:t('cancel'),value:null},{label:L("Подготовить изменения","Prepare changes"),primary:true,value:b=>({instruction:b.querySelector('#sl-object-instruction').value.trim(),scope:b.querySelector('#sl-object-scope').value})}]});
  if(!request?.instruction)return;
  const slides=request.scope==='deck'?view.deck.slides:[view.slide], items=new Map(), W=view.deck.w,H=view.deck.h;
  const payload=slides.map(slide=>({id:slide.id,notes:slide.notes || '',objects:slide.elements.slice(0,100).map(el=>{
    const box=view.deck.boxOf(slide,el);
    items.set(el.id,{el,box,slide,before:objectSnapshot(el)});
    return {id:el.id,kind:el.kind,text:textOf(el),x:box.x/W,y:box.y/H,w:box.w/W,h:box.h/H,rotation:el.rot || 0,...(el.chart?{chart:el.chart}:{})};
  })}));
  const notesBefore=new Map(slides.map(s=>[s.id,s.notes || '']));
  const pm=progressModal(L("ИИ готовит изменения объектов…","AI prepares object edits…"));let data;
  try{data=parseJsonLoose(await streamTask('edit_objects',{instruction:request.instruction,slides:payload,lang:I18N.lang},{signal:pm.signal}));}finally{pm.close();}
  const changes=[],seen=new Set();
  for(const proposal of (Array.isArray(data.items)?data.items:[]).slice(0,200)){
    const item=items.get(proposal?.id);if(!item || seen.has(proposal.id))continue;seen.add(proposal.id);
    const patch={};
    for(const key of ['x','y','w','h'])if(typeof proposal[key]==='number' && Number.isFinite(proposal[key]) && proposal[key]>=0 && proposal[key]<=1 && (!['w','h'].includes(key)||proposal[key]>0))patch[key]=Math.round(proposal[key]*(['x','w'].includes(key)?W:H));
    const baseBox=item.box || item.el;
    if((patch.x ?? baseBox.x)+(patch.w ?? baseBox.w)>W || (patch.y ?? baseBox.y)+(patch.h ?? baseBox.h)>H)for(const key of ['x','y','w','h'])delete patch[key];
    if(typeof proposal.rotation==='number' && Number.isFinite(proposal.rotation))patch.rot=((proposal.rotation%360)+360)%360;
    if(item.el.text && typeof proposal.text==='string')patch.text=proposal.text.slice(0,12000);
    const c=proposal.chart;
    if(item.el.chart && c && ['col','bar','line','area','pie','doughnut'].includes(c.type) && Array.isArray(c.cats) && c.cats.length<=200 && Array.isArray(c.series) && c.series.length<=20 && c.series.every(s=>Array.isArray(s.vals)&&s.vals.length===c.cats.length&&s.vals.every(v=>typeof v==='number'&&Number.isFinite(v))))patch.chart={...item.el.chart,type:c.type,cats:c.cats.map(v=>String(v).slice(0,300)),series:c.series.map(s=>({name:String(s.name || '').slice(0,300),vals:s.vals}))};
    if(Object.keys(patch).length)changes.push({...item,patch,label:item.el.name || item.el.kind});
  }
  for(const note of (Array.isArray(data.notes)?data.notes:[]).slice(0,50)){
    const slide=slides.find(s=>s.id===note?.slide_id);if(slide && typeof note.text==='string' && !seen.has('note:'+slide.id)){seen.add('note:'+slide.id);changes.push({slide,notes:note.text.slice(0,12000),before:notesBefore.get(slide.id),label:L('Заметки докладчика','Speaker notes')});}
  }
  if(!changes.length){toast(t('sl_ai_unchanged'));return;}
  const choice=await modal({title:L('Проверьте изменения объектов','Review object changes'),wide:true,body:`<div class="sl-rewrite-list">${changes.map((c,i)=>`<label class="sl-rewrite-item"><span class="sl-rewrite-head"><input type="checkbox" data-object-change="${i}" checked>${esc(L('Слайд','Slide'))} ${view.deck.slides.indexOf(c.slide)+1}: ${esc(c.label)}</span><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(describeObjectChange(c,W,H))}</pre></label>`).join('')}</div>`,buttons:[{label:t('cancel'),value:null},{label:L('Применить выбранное','Apply selected'),primary:true,value:b=>[...b.querySelectorAll('[data-object-change]:checked')].map(n=>+n.dataset.objectChange)}]});
  if(!choice?.length)return;
  const chosen=choice.map(i=>changes[i]);
  if(chosen.some(c=>!view.deck.slides.includes(c.slide) || (c.el?objectSnapshot(c.el)!==c.before || !c.slide.elements.includes(c.el):(c.slide.notes || '')!==c.before))){toast(L('Документ изменился. Подготовьте новый черновик.','Document changed. Prepare a new draft.'));return;}
  view.change(()=>{for(const c of chosen){if(c.el){const {text,...properties}=c.patch;if(c.el.xfrmInherited && ['x','y','w','h'].some(k=>k in properties)){const b=c.box || view.deck.boxOf(c.slide,c.el);for(const k of ['x','y','w','h'])if(!(k in properties) && Number.isFinite(b[k]))properties[k]=b[k];properties.xfrmInherited=false;}Object.assign(c.el,properties);if(text!==undefined)c.el.text.paras=linesToParas(text.split('\n'),c.el.text.paras);}else c.slide.notes=c.notes;view.touch(c.slide);}},{all:true,thumbsAll:true});
  toast(t('sl_ai_applied'),{kind:'ok',action:{label:t('undo'),run:()=>view.undo()}});
}

export async function studioAction(view,action) {
  try {
    if(action==="themes")return await themeGallery(view);
    if(action==="audit")return await reviewDialog(view,false);
    if(action==="coach")return await reviewDialog(view,true);
    if(action==="revise")return await customRewrite(view);
    if(action==="objects")return await editObjects(view);
  } catch(error) {if(error.name!=="AbortError")toast(String(error.message || error),{kind:"error",ms:8000});}
}
