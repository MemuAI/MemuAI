import {decorate,icon,node,localBase,modelName,textContent,isChatModel,copy} from './ui.js';
import {render} from './markdown.js';

const $=id=>document.getElementById(id);
decorate();
const cfg=await chrome.storage.local.get({base:'http://127.0.0.1:8600',token:'',model:'',favorites:[],recentModels:[],lastChat:'',lastProfile:''});
let models=[],chats=[],profile='',currentChat='',currentUpdated=0,busy=false,connected=false,controller=null,approvalId='',loadSequence=0;
let staged=[],pageEnabled=false,favoritesOnly=false,currentAnswer=null,paintPending=false,refreshing=false,historyScope='browser';
let sharedSettings={read_pages:true,control:'ask',dfy:false,schedules:false,save_to_main:false},defaultModel='',preferenceBusy=false;
let currentBrowserOnly=null;
let resizeQueued=false;
const welcome=$('welcome');

function status(text='',error=false){$('status').textContent=text;$('status').hidden=!text;$('status').classList.toggle('error',error);}
function connection(ok,label){connected=ok;$('connection').className='connection '+(ok?'online':'offline');$('connection-label').textContent=label;controls();}
async function call(path,body,signal){
  const response=await fetch(localBase(cfg.base)+'/api/pg/browser-extension/bridge/'+path,{method:body?'POST':'GET',signal:signal||AbortSignal.timeout(45000),headers:{'Content-Type':'application/json',Authorization:'Bearer '+cfg.token,'X-MemuAI-Extension':chrome.runtime.id},body:body?JSON.stringify(body):undefined});
  if(!response.ok){const value=await response.json().catch(()=>({}));throw new Error(value.error||value.detail||`MemuAI: HTTP ${response.status}`);}
  return response;
}
function controls(){
  $('send').disabled=!connected||busy||!cfg.model||models.find(m=>m.id===cfg.model)?.availability?.available===false||(!staged.length&&!$('input').value.trim())||staged.some(a=>a.pending||a.error);
  $('send').hidden=busy;$('stop').hidden=!busy;
  for(const id of ['new','history-new','attach','page','model-trigger','quick-tools'])$(id).disabled=busy;
  $('input').disabled=busy;$('history').disabled=busy;
  for(const button of document.querySelectorAll('[data-page-action]'))button.disabled=busy||!connected||!cfg.model||sharedSettings.read_pages===false;
  $('composer-hint').textContent=currentChat&&currentBrowserOnly!==null?(currentBrowserOnly?'Чат только в браузере':'Общий чат MemuAI'):sharedSettings.save_to_main?'Новый чат также в MemuAI':'Чат только в браузере';
  sizeInput();
}
// Shrink before measuring: an old multiline height must not become the next
// intrinsic height after a sidebar resize, a hidden composer or clearing text.
function sizeInput(){
  const input=$('input'),composer=$('composer');if(composer.hidden)return;
  const oldScroll=input.scrollTop;input.style.height='0px';
  const viewport=window.visualViewport?.height||window.innerHeight||800;
  const chromeHeight=Array.from(document.body.children).filter(e=>e!==composer&&e!==$('messages')&&e.offsetParent!==null&&!['DIALOG','INPUT','SCRIPT'].includes(e.tagName)).reduce((n,e)=>n+e.getBoundingClientRect().height,0);
  const extras=Math.max(0,composer.getBoundingClientRect().height-input.getBoundingClientRect().height);
  const maximum=Math.max(40,Math.min(168,Math.floor(viewport*.24),viewport-chromeHeight-extras-100));
  const natural=input.value?input.scrollHeight:54;
  input.style.maxHeight=maximum+'px';input.style.height=Math.min(maximum,Math.max(Math.min(54,maximum),natural))+'px';
  input.scrollTop=Math.min(oldScroll,input.scrollHeight);
}
function queueResize(){if(resizeQueued)return;resizeQueued=true;requestAnimationFrame(()=>{resizeQueued=false;sizeInput();});}
function scrollBottom(force=false){const el=$('messages');if(force||el.scrollHeight-el.scrollTop-el.clientHeight<180)el.scrollTop=el.scrollHeight;}
function clearConversation(){loadSequence++;currentChat='';currentBrowserOnly=null;currentUpdated=0;approvalId='';$('approval').hidden=true;$('messages').replaceChildren(welcome);welcome.hidden=false;$('chat-title').textContent='Новый чат';$('readonly').hidden=true;$('composer').hidden=false;staged=[];paintAttachments();cfg.lastChat='';chrome.storage.local.set({lastChat:''});paintChats();}
function newChat(){if(busy)return;clearConversation();if(defaultModel)selectModel(defaultModel,false);$('history-dialog').close();status();$('input').focus();}
function message(role,text,files=[]){
  welcome.hidden=true;const article=node('article','message '+role),header=node('div','message-header');
  if(role==='assistant'){const img=node('img');img.src='icons/32.png';img.alt='';header.append(img);}
  header.append(node('span','',role==='user'?'Вы':'MemuAI'));
  const content=node('div','content');
  if(role==='user')content.textContent=textContent(text);else render(content,textContent(text));
  article.append(header);
  if(files.length){const box=node('div','message-files');for(const f of files){const data=f.preview||f.data||'';if(typeof data==='string'&&/^data:image\/(png|jpeg|webp|gif);base64,/.test(data)){const image=node('img','message-image');image.src=data;image.alt=f.name||'Изображение';image.loading='lazy';box.append(image);}else box.append(node('span','message-file',f.name||'Файл'));}article.append(box);}
  article.append(content);$('messages').append(article);content._text=textContent(text);content._article=article;
  if(role==='assistant'&&text)addActions(content);return content;
}
function addActions(content){if(content._article.querySelector('.message-actions'))return;const box=node('div','message-actions'),button=node('button','icon-button');button.type='button';button.title='Копировать ответ';button.setAttribute('aria-label','Копировать ответ');button.append(icon('copy'));button.onclick=()=>copy(content._text,button);box.append(button);content._article.append(box);}
function paintAnswer(){paintPending=false;if(!currentAnswer)return;const follow=$('messages').scrollHeight-$('messages').scrollTop-$('messages').clientHeight<180;render(currentAnswer,currentAnswer._text);if(follow)scrollBottom(true);}
function queuePaint(){if(!paintPending){paintPending=true;requestAnimationFrame(paintAnswer);}}

function selectModel(id,persist=true){const m=models.find(m=>m.id===id);if(!m||m.availability?.available===false)return;cfg.model=id;$('model-label').textContent=modelName(m);$('model-trigger').title=m.id;
  if(persist){cfg.recentModels=[id,...cfg.recentModels.filter(v=>v!==id)].slice(0,8);chrome.storage.local.set({model:id,recentModels:cfg.recentModels});}
  controls();paintModels();
}
async function saveModelPreferences(patch){
  if(preferenceBusy)return false;preferenceBusy=true;const requestToken=cfg.token,requestBase=cfg.base,requestProfile=profile;const previous={favorites:cfg.favorites.slice(),defaultModel,model:cfg.model};paintModels();
  try{const value=await(await call('model-preferences',patch)).json();
    if(requestToken!==cfg.token||requestBase!==cfg.base||requestProfile!==profile)return false;
    if(Array.isArray(value.pinned_models))cfg.favorites=value.pinned_models;
    if(typeof value.default_model==='string')defaultModel=value.default_model;
    if(patch.default_model)selectModel(patch.default_model);
    await chrome.storage.local.set({favorites:cfg.favorites,model:cfg.model,modelPreferencesUpdated:Date.now()});paintModels();return true;
  }catch(error){if(requestToken===cfg.token&&requestBase===cfg.base&&requestProfile===profile){cfg.favorites=previous.favorites;defaultModel=previous.defaultModel;selectModel(previous.model,false);status('Не удалось сохранить выбор модели: '+error.message,true);}return false;}
  finally{preferenceBusy=false;paintModels();}
}
function modelDetail(m){const c=m.caps||{},parts=[m.account||m.provider_label||m.prefix||m.provider||'Модель'];if(m.availability?.available===false)parts.push(m.availability.reason||'Сейчас недоступна');if(c.vision)parts.push('Изображения');const context=Number(m.context?.context||m.context_window);if(context>0)parts.push((context/1000).toLocaleString('ru-RU',{maximumFractionDigits:0})+'K контекста');return parts.join(' · ');}
function paintModels(){
  const target=$('model-list'),query=$('model-search').value.trim().toLocaleLowerCase();target.replaceChildren();
  const candidates=models.filter(m=>(!favoritesOnly||cfg.favorites.includes(m.id))&&`${modelName(m)} ${m.id} ${m.provider||''} ${m.prefix||''}`.toLocaleLowerCase().includes(query));
  $('model-count').textContent=models.length?`${models.length} моделей · выбор и закрепления общие с MemuAI`:'Добавьте аккаунт и модели в MemuAI Proxy';
  const sorted=candidates.slice().sort((a,b)=>{const rank=m=>cfg.favorites.includes(m.id)?0:cfg.recentModels.includes(m.id)?1:2;return rank(a)-rank(b)||(rank(a)===0?cfg.favorites.indexOf(a.id)-cfg.favorites.indexOf(b.id):0)||String(a.provider||a.prefix).localeCompare(String(b.provider||b.prefix))||modelName(a).localeCompare(modelName(b));});let group='';
  for(const m of sorted){const label=cfg.favorites.includes(m.id)?'Избранные':cfg.recentModels.includes(m.id)?'Недавние':m.provider||m.prefix||'Другие';if(label!==group){group=label;target.append(node('div','list-group',label));}
    const row=node('div','model-row'+(m.id===cfg.model?' selected':'')),select=node('button'),mark=node('span','row-icon'),labelBox=node('span','row-label');select.type='button';select.disabled=m.availability?.available===false||preferenceBusy;select.title=m.id;select.setAttribute('aria-pressed',String(m.id===cfg.model));mark.append(icon(m.id===cfg.model?'check':'spark'));labelBox.append(node('strong','',modelName(m)),node('small','',modelDetail(m)+(m.id===defaultModel?' · Основная':'')));select.append(mark,labelBox);select.onclick=async()=>{if(preferenceBusy)return;if(await saveModelPreferences({default_model:m.id,profile_id:profile})){$('model-dialog').close();$('input').focus();}};
    const star=node('button','icon-button favorite'+(cfg.favorites.includes(m.id)?' active':''));star.type='button';star.disabled=preferenceBusy;star.title=cfg.favorites.includes(m.id)?'Открепить модель':'Закрепить модель';star.setAttribute('aria-label',star.title+' · '+modelName(m));star.setAttribute('aria-pressed',String(cfg.favorites.includes(m.id)));star.append(icon('star'));star.onclick=async()=>{if(preferenceBusy)return;star.disabled=true;await saveModelPreferences({pin_model:m.id,pinned:!cfg.favorites.includes(m.id),profile_id:profile});};row.append(select,star);target.append(row);
  }
  if(!sorted.length)target.append(node('p','list-empty',favoritesOnly?'Здесь появятся модели, которые вы отметите звездой.':query?'Ничего не найдено. Попробуйте другое название.':'Моделей пока нет. Проверьте MemuAI Proxy и обновите список.'));
}
function paintChats(){
  const target=$('chat-list'),query=$('chat-search').value.trim().toLocaleLowerCase();target.replaceChildren();
  const list=chats.filter(c=>(historyScope==='all'||(historyScope==='browser'?(c.browser_only||c.visibility==='browser'||c.origin==='browser_extension'):!c.browser_only&&c.visibility!=='browser'))&&`${c.title||''} ${c.model||''}`.toLocaleLowerCase().includes(query)).sort((a,b)=>(Number(b.updated)||Number(b.created)||0)-(Number(a.updated)||Number(a.created)||0));let lastGroup='';
  for(const chat of list){let stamp=Number(chat.updated)||Number(chat.created)||0;if(stamp&&stamp<1e12)stamp*=1000;const date=stamp?new Date(stamp):null,day=date?.toLocaleDateString('ru-RU',{day:'numeric',month:'long'})||'Другие разговоры';if(lastGroup!==day){lastGroup=day;target.append(node('div','list-group',day));}
    const row=node('div','chat-row'+(chat.id===currentChat?' selected':'')),button=node('button'),mark=node('span','row-icon'),label=node('span','row-label');button.type='button';button.title=chat.title||'Без названия';mark.append(icon(chat.project_id?'folder':'chat'));label.append(node('strong','',chat.title||'Без названия'),node('small','',[chat.browser_only?'Только в браузере':chat.project_id?'Чат проекта':chat.mode==='code'||chat.mode==='work'?'Рабочий чат':'MemuAI',chat.count?chat.count+' сообщ.':''].filter(Boolean).join(' · ')));button.append(mark,label);button.onclick=()=>openChat(chat.id);row.append(button);target.append(row);
  }
  if(!list.length)target.append(node('p','list-empty',query?'Разговоров с таким названием нет.':historyScope==='browser'?'Начните разговор в панели. По умолчанию он останется только в истории браузера.':'Чаты из приложения MemuAI появятся здесь.'));
}
async function openChat(id,quiet=false){
  if(busy)return;const sequence=++loadSequence;try{const value=await(await call('chats?id='+encodeURIComponent(id)+'&profile_id='+encodeURIComponent(profile))).json();if(sequence!==loadSequence)return;if(!value.chat)throw new Error('Этот чат удалён. Обновите историю.');
    const chat=value.chat;currentChat=chat.id;currentBrowserOnly=Boolean(chat.browser_only||chat.visibility==='browser');currentUpdated=chat.updated||0;cfg.lastChat=chat.id;chrome.storage.local.set({lastChat:chat.id,lastProfile:profile});$('messages').replaceChildren(welcome);welcome.hidden=true;$('chat-title').textContent=chat.title||'Без названия';
    for(const m of chat.messages||[])if(['user','assistant'].includes(m.role)){const files=[...(m.attachments||[]),...(m.images||[]).filter(v=>typeof v==='string'&&v.startsWith('data:image/')).map(data=>({data,name:'Изображение'}))];const content=message(m.role,m.content,files);for(const tool of m.tool_activity||[])paintTool({...tool,phase:tool.phase||'done'},content);}
    if(models.some(m=>m.id===chat.model))selectModel(chat.model,false);
    const readonly=Boolean(chat.project_id||['work','code','memucode'].includes(chat.mode));$('readonly').hidden=!readonly;$('composer').hidden=readonly;$('open-chat').href='memuai://chat/open?id='+encodeURIComponent(chat.id);staged=[];paintAttachments();$('history-dialog').close();paintChats();scrollBottom(true);if(!quiet)status();
  }catch(error){status(error.message,true);}
}
async function refresh(preserve=false){
  if(refreshing)return;refreshing=true;
  const requestToken=cfg.token,requestBase=cfg.base;
  try{
    if(!cfg.token){connection(false,'Не подключено');status('Подключите расширение к MemuAI в настройках.');return;}
    const [modelData,chatData,settingsData]=await Promise.all([call('models').then(r=>r.json()),call('chats').then(r=>r.json()),call('settings').then(r=>r.json())]);
    if(requestToken!==cfg.token||requestBase!==cfg.base)return;
    if(modelData.profile_id&&chatData.profile_id&&modelData.profile_id!==chatData.profile_id)throw new Error('Профиль MemuAI сменился во время обновления. Обновите список ещё раз.');
    if(profile&&profile!==chatData.profile_id){controller?.abort();clearConversation();status('Профиль MemuAI сменился. Открыта его история.');}
    profile=chatData.profile_id||'default';chats=chatData.chats||[];models=(modelData.models||modelData.data||[]).filter(isChatModel);$('profile-name').textContent=chatData.profile_name||'Текущий профиль MemuAI';
    sharedSettings={...sharedSettings,...(settingsData.settings||settingsData)};
    if(Array.isArray(modelData.pinned_models)){cfg.favorites=modelData.pinned_models;chrome.storage.local.set({favorites:cfg.favorites});}
    defaultModel=modelData.default_model||'';
    const usable=id=>models.some(m=>m.id===id&&m.availability?.available!==false);
    const chosen=!currentChat&&usable(defaultModel)?defaultModel:usable(cfg.model)?cfg.model:usable(defaultModel)?defaultModel:models.find(m=>m.availability?.available!==false)?.id||'';
    $('history-policy').textContent=sharedSettings.save_to_main?'Новые чаты видны и в MemuAI':'Новые чаты остаются в истории браузера';
    if(sharedSettings.read_pages===false&&pageEnabled)await setPage(false);
    const wasConnected=connected;
    cfg.model=chosen;if(chosen)selectModel(chosen,false);else $('model-label').textContent='Выберите модель';paintModels();paintChats();connection(true,'Подключено');
    if(!models.length)status('В MemuAI Proxy пока нет доступных моделей для чата.');else if(!preserve||!wasConnected)status();
    const current=chats.find(c=>c.id===currentChat);
    if(!busy&&current&&current.updated!==currentUpdated)await openChat(currentChat,true);
    if(currentChat&&!current&&!busy){clearConversation();status('Чат удалён в MemuAI. Можно начать новый.');}
  }catch(error){connection(false,'Нет связи');status(/Failed to fetch|fetch failed|NetworkError/i.test(error.message)?'MemuAI недоступен. Запустите приложение или проверьте локальный адрес в настройках.':error.message,true);}
  finally{refreshing=false;controls();if(requestToken!==cfg.token||requestBase!==cfg.base)refresh();}
}

function paintAttachments(){const box=$('attachments');box.replaceChildren();box.hidden=!staged.length;for(const item of staged){const chip=node('div','attachment'+(item.error?' failed':''));if(item.preview){const image=node('img');image.src=item.preview;image.alt='';chip.append(image);}else chip.append(icon('page'));const label=node('span','attachment-label');label.append(node('strong','',item.name),node('small','',item.error|| (item.pending?'Читаю файл…':item.truncated?'Прочитан фрагмент':Math.max(1,Math.round(item.size/1024))+' КБ')));const remove=node('button','icon-button small');remove.type='button';remove.title='Убрать '+item.name;remove.setAttribute('aria-label',remove.title);remove.append(icon('close'));remove.onclick=()=>{staged=staged.filter(v=>v!==item);paintAttachments();};chip.append(label,remove);box.append(chip);}controls();}
async function addFiles(files){if(busy||!connected)return;const sequence=loadSequence;for(const file of Array.from(files)){if(sequence!==loadSequence)return;if(staged.length>=8){status('Можно прикрепить до 8 файлов за сообщение.',true);break;}if(file.size>5*1024*1024||!file.size){status(file.name+': нужен непустой файл до 5 МБ.',true);continue;}const item={name:file.name,size:file.size,pending:true};staged.push(item);paintAttachments();try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('Не удалось прочитать файл'));reader.readAsDataURL(file);});if(sequence!==loadSequence)return;const value=await(await call('attachment',{name:file.name,data,profile_id:profile})).json();if(sequence!==loadSequence)return;Object.assign(item,value,{pending:false});}catch(error){if(sequence!==loadSequence)return;item.pending=false;item.error=error.message;status(error.message,true);}paintAttachments();}$('input').focus();}
async function setPage(enabled){
  if(enabled&&sharedSettings.read_pages===false){status('Чтение страниц выключено. Включите его в настройках расширения.',true);return false;}
  if(enabled){try{const has=await chrome.permissions.contains({origins:['<all_urls>']});if(!has&&!await chrome.permissions.request({origins:['<all_urls>']})){status('Разрешите чтение сайтов, чтобы добавить страницу.',true);return;}const [tab]=await chrome.tabs.query({active:true,lastFocusedWindow:true});if(!tab||tab.incognito||!/^https?:\/\//.test(tab.url||''))throw new Error('Откройте обычную веб-страницу. Служебные вкладки браузера недоступны.');$('page-title').textContent=tab.title||new URL(tab.url).hostname;}catch(error){status(error.message,true);return;}}
  pageEnabled=enabled;$('page').setAttribute('aria-pressed',String(enabled));$('page-context').hidden=!enabled;queueResize();return true;
}
async function approve(approve){const id=approvalId;if(!id)return;approvalId='';$('approval').hidden=true;try{await call('answer',{id,approve});}catch(error){status(error.message,true);}}
let streamFailed=false,streamFinished=false;
function toolLabel(name){return({chrome_read:'Чтение страницы',chrome_screenshot:'Скриншот страницы',chrome_tabs:'Открытые вкладки',chrome_close_tab:'Закрытие вкладки',chrome_close_tabs:'Закрытие вкладок',chrome_open_tab:'Открытие вкладки',chrome_navigate:'Переход на страницу',chrome_action:'Действие на странице',web_search:'Поиск в интернете',web_fetch:'Чтение сайта'})[name]||String(name||'Инструмент').replace(/_/g,' ');}
function paintTool(value,content=currentAnswer){
  if(!content)return;
  if(!content._toolRows){const details=node('details','tool-trace'),summary=node('summary'),mark=node('span','tool-mark');mark.append(icon('spark'));summary.append(mark,node('span','tool-summary','Инструменты'),node('span','tool-chevron'));details.append(summary);content._article.insertBefore(details,content);content._toolBox=details;content._toolRows=new Map();}
  const key=value.id||value.tool||String(content._toolRows.size);let row=content._toolRows.get(key);
  if(!row){row=node('div','tool-step');row.append(node('span','tool-step-icon'),node('span','tool-step-label'),node('span','tool-step-state'));content._toolRows.set(key,row);content._toolBox.append(row);}
  row.className='tool-step '+(value.phase||'running');row.querySelector('.tool-step-icon').replaceChildren(icon(value.phase==='done'?'check':value.phase==='error'?'close':'spark'));
  row.querySelector('.tool-step-label').textContent=value.title||toolLabel(value.tool);row.querySelector('.tool-step-state').textContent=value.phase==='done'?'Готово':value.phase==='error'?'Ошибка':'Выполняется';
  const detail=value.detail||value.description;let note=row.querySelector('small');if(detail){if(!note){note=node('small');row.append(note);}note.textContent=String(detail).slice(0,600);}
  const running=Array.from(content._toolRows.values()).some(e=>e.classList.contains('running'));
  content._toolBox.querySelector('.tool-summary').textContent=running?'Использую инструменты…':`Инструменты · ${content._toolRows.size}`;
  if(value.phase==='running')content._toolBox.open=true;scrollBottom();
}
function approvalContent(value){
  const box=$('approval');box.replaceChildren();box.hidden=false;box.append(node('strong','',value.title||'Разрешить действие?'));
  if(value.description||value.detail)box.append(node('p','',value.description||value.detail));
  const tabs=value.tabs||value.args?.tabs;
  if(Array.isArray(tabs)&&tabs.length){const list=node('ul','approval-tabs');for(const tab of tabs){const item=node('li');item.append(node('span','',tab.title||tab.url||'Вкладка'));if(tab.url)item.append(node('small','',tab.url));list.append(item);}box.append(list);}
  else{const detail=value.description||value.detail||value.args?.url||value.args?.query||value.args?.text;if(detail)box.append(node('p','approval-detail',String(detail).slice(0,800)));else box.append(node('p','approval-detail',toolLabel(value.tool)));}
  const actions=node('div','approval-actions'),yes=node('button','primary-button',tabs?.length?'Разрешить для всех':'Разрешить'),no=node('button','secondary-button','Отклонить');yes.type=no.type='button';yes.onclick=()=>approve(true);no.onclick=()=>approve(false);actions.append(yes,no);box.append(actions);queueResize();
}
function event(value){
  if(value.event==='chat'){currentChat=value.id;currentBrowserOnly=typeof value.browser_only==='boolean'?value.browser_only:value.visibility?value.visibility==='browser':!sharedSettings.save_to_main;cfg.lastChat=value.id;chrome.storage.local.set({lastChat:value.id,lastProfile:profile});$('chat-title').textContent=$('chat-title').textContent==='Новый чат'?currentAnswer?._prompt?.slice(0,70)||'Новый чат':$('chat-title').textContent;controls();}
  if(value.event==='token'){currentAnswer._text+=value.text||'';queuePaint();}
  if(value.event==='replace'){currentAnswer._text=value.text||'';queuePaint();}
  if(value.event==='status')status(value.text);
  if(value.event==='tool')paintTool(value);
  if(value.event==='error'){streamFailed=true;status(value.text,true);}
  if(value.event==='done'){streamFinished=true;if(!streamFailed)status();addActions(currentAnswer);}
  if(value.event==='confirm'){approvalId=value.id;approvalContent(value);}
}
async function send(e){
  e.preventDefault();if(busy||$('send').disabled)return;const text=$('input').value.trim(),files=staged.slice(),model=models.find(m=>m.id===cfg.model);
  if(files.some(f=>f.isImg)&&model?.caps?.vision===false){status('Эта модель не поддерживает изображения. Выберите модель с поддержкой изображений.',true);$('model-dialog').showModal();return;}
  const sequence=++loadSequence;busy=true;streamFailed=false;streamFinished=false;controller=new AbortController();const activeController=controller,prompt=text||'Посмотри прикреплённые файлы.';
  const request={text,model:cfg.model,chat_id:currentChat,profile_id:profile,attachments:files.map(f=>f.id),page_context:pageEnabled};
  message('user',prompt,files);currentAnswer=message('assistant','');currentAnswer._prompt=prompt;const thinking=node('div','thinking');thinking.setAttribute('aria-label','MemuAI готовит ответ');thinking.append(node('i'),node('i'),node('i'));currentAnswer.append(thinking);staged=[];paintAttachments();$('input').value='';controls();scrollBottom(true);status('Думаю…');
  try{
    const [tab]=pageEnabled?await chrome.tabs.query({active:true,lastFocusedWindow:true}):[];
    if(sequence!==loadSequence||activeController.signal.aborted)throw new DOMException('Ответ остановлен','AbortError');
    const response=await call('chat',{...request,tab_id:tab?.id},activeController.signal);
    const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
    while(true){const{done,value}=await reader.read();if(sequence!==loadSequence||activeController.signal.aborted)throw new DOMException('Ответ остановлен','AbortError');pending+=decoder.decode(value||new Uint8Array(),{stream:!done});let end;while((end=pending.indexOf('\n\n'))!==-1){const frame=pending.slice(0,end);pending=pending.slice(end+2);for(const line of frame.split('\n'))if(line.startsWith('data:'))event(JSON.parse(line.slice(5).trim()));}if(done)break;}
    if(!streamFinished&&!streamFailed)throw new Error('Поток ответа прерван. Можно повторить запрос.');
  }catch(error){streamFailed=true;status(error.name==='AbortError'?'Ответ остановлен.':error.message,error.name!=='AbortError');}
  finally{
    if(approvalId)await approve(false);busy=false;controller=null;paintAnswer();if(!currentAnswer._text){currentAnswer.textContent=streamFailed?'Ответ не завершён.':'Модель не вернула текст.';}
    else addActions(currentAnswer);
    if(streamFailed){const restore=node('button','text-button','Вернуть запрос в поле ввода');restore.type='button';restore.onclick=()=>{$('input').value=text;staged=files;paintAttachments();$('input').focus();restore.remove();};currentAnswer._article.append(restore);}
    controls();await refresh(true);$('input').focus();
  }
}

$('composer').addEventListener('submit',send);$('stop').onclick=()=>controller?.abort();$('settings').onclick=()=>chrome.runtime.openOptionsPage();$('connection').onclick=()=>cfg.token?refresh():chrome.runtime.openOptionsPage();
$('new').onclick=$('history-new').onclick=newChat;$('history').onclick=()=>{paintChats();$('history-dialog').showModal();$('chat-search').focus();refresh(true);};
$('model-trigger').onclick=()=>{paintModels();$('model-dialog').showModal();$('model-search').focus();};
$('refresh').onclick=()=>refresh();$('model-search').oninput=paintModels;$('chat-search').oninput=paintChats;
for(const scope of ['browser','main','all'])$('history-'+scope).onclick=()=>{historyScope=scope;for(const name of ['browser','main','all']){$('history-'+name).classList.toggle('active',scope===name);$('history-'+name).setAttribute('aria-pressed',String(scope===name));}paintChats();};
for(const favorite of [false,true])$(favorite?'favorite-models':'all-models').onclick=()=>{favoritesOnly=favorite;for(const flag of [false,true]){$(flag?'favorite-models':'all-models').classList.toggle('active',flag===favorite);$(flag?'favorite-models':'all-models').setAttribute('aria-pressed',String(flag===favorite));}paintModels();};
for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>$(button.dataset.close).close();
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',e=>{if(e.target===dialog){const rect=dialog.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)dialog.close();}});
$('input').addEventListener('input',controls);$('input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}});
document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.altKey&&e.key.toLowerCase()==='n'){e.preventDefault();newChat();}});
$('attach').onclick=()=>$('file-input').click();$('file-input').onchange=()=>{addFiles($('file-input').files);$('file-input').value='';};$('page').onclick=()=>setPage(!pageEnabled);$('remove-page').onclick=()=>setPage(false);
$('input').addEventListener('paste',e=>{if(e.clipboardData?.files.length){e.preventDefault();addFiles(e.clipboardData.files);}});
let dragDepth=0;document.addEventListener('dragenter',e=>{if(Array.from(e.dataTransfer?.types||[]).includes('Files')){e.preventDefault();dragDepth++;$('drop-overlay').hidden=false;}});document.addEventListener('dragover',e=>{if(Array.from(e.dataTransfer?.types||[]).includes('Files'))e.preventDefault();});document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('drop-overlay').hidden=true;}});document.addEventListener('drop',e=>{if(e.dataTransfer?.files.length){e.preventDefault();dragDepth=0;$('drop-overlay').hidden=true;addFiles(e.dataTransfer.files);}});
for(const button of document.querySelectorAll('[data-prompt]'))button.onclick=async()=>{if(button.dataset.page){if(!await setPage(true))return;}if(button.dataset.pageAction){$('tools-dialog').close();$('input').value=button.dataset.prompt;controls();$('composer').requestSubmit();return;}$('input').value=button.dataset.prompt;controls();$('input').focus();if(button.dataset.attach)$('file-input').click();};
$('quick-tools').onclick=()=>{$('tools-dialog').showModal();};
$('layout-settings').onclick=()=>{chrome.tabs.create({url:'chrome://settings/appearance'}).catch(()=>chrome.runtime.openOptionsPage());status('Настройки браузера → Внешний вид → Боковая панель → Справа.');};$('layout-dismiss').onclick=()=>{$('layout-hint').hidden=true;chrome.storage.local.set({layoutHintDismissed:true});};
chrome.storage.onChanged.addListener((changes,area)=>{if(area!=='local')return;if(!changes.base&&!changes.token){if((changes.modelPreferencesUpdated||changes.settingsUpdated)&&!busy&&!preferenceBusy)refresh(true);return;}if(changes.base)cfg.base=changes.base.newValue||'http://127.0.0.1:8600';if(changes.token)cfg.token=changes.token.newValue||'';controller?.abort();clearConversation();models=[];chats=[];profile='';paintModels();paintChats();$('model-label').textContent='Выберите модель';refresh();});
window.addEventListener('focus',()=>{if(!busy)refresh(true);});document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)refresh(true);});
setInterval(()=>{if(!document.hidden&&!busy)refresh(true);},30000);
const lastChat=cfg.lastChat,lastProfile=cfg.lastProfile;await refresh();
if(lastChat&&lastProfile===profile&&chats.some(c=>c.id===lastChat))await openChat(lastChat,true);
chrome.runtime.sendMessage({op:'connect'}).catch(()=>{});
try{const layout=await chrome.sidePanel?.getLayout?.();const{layoutHintDismissed}=await chrome.storage.local.get({layoutHintDismissed:false});if(layout?.side==='left'&&!layoutHintDismissed)$('layout-hint').hidden=false;}catch{/* Older Chromium has no layout query. */}
controls();
window.addEventListener('resize',queueResize);window.visualViewport?.addEventListener('resize',queueResize);
if(typeof ResizeObserver!=='undefined')new ResizeObserver(queueResize).observe($('composer'));
