
window.MemuBrowserExtension = (() => {
  "use strict";
  const K = () => window.MemuSettingsKit;
  const L = (ru,en) => typeof PG_LANG !== "undefined" && PG_LANG === "en" ? en : ru;
  const esc = v => String(v ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const base="/api/pg/browser-extension";
  const icon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18M15 9v11M6 6.5h.01M9 6.5h.01"/></svg>';
  let state=null,code="",codeExpires=0,timer=0,actionBusy=false;
  const request=(path,body)=>api(base+path,body===undefined?undefined:{method:"POST",body});
  const button=(action,text,primary=false)=>'<button type="button" class="bext-button'+(primary?' bext-button--primary':'')+'" data-bext="'+action+'">'+text+'</button>';
  function listHtml(){
    return K().link({sub:"int-chrome",label:"Chrome · MemuAI",icon,desc:L("Ваши чаты и модели в браузере. Страницы и действия — по вашему выбору.","Your chats and models in your browser. Page context and actions are optional."),value:'<span id="bext-value">'+(state?.connected?L("Подключено","Connected"):"")+'</span>',keywords:"chrome browser extension расширение браузер dfy"});
  }
  function subHtml(){
    return K().sub({id:"int-chrome",title:"Chrome · MemuAI",desc:L("Откройте MemuAI в боковой панели Chrome и подключите её к этому приложению.","Open MemuAI in Chrome's side panel and connect it to this app."),body:'<div id="bext-root" class="bext-root"></div>'});
  }
  function checkbox(key,label,desc){
    return K().row({label,desc,control:'<label class="switch sx-switch"><input type="checkbox" role="switch" aria-label="'+esc(label)+'" data-bext-key="'+key+'" '+(state?.[key]?'checked':'')+'><span class="sl"></span></label>'});
  }
  /* The managed folder MemuAI installs into and keeps current. */
  function folderHtml(){
    const path=state?.update?.managed_path||state?.managed_path||"";
    if(!path)return '<small>'+L("Папка появится в данных MemuAI. ZIP по умолчанию сохраняется в Загрузки / MemuAI.","The folder is created in MemuAI's data. The ZIP is saved to Downloads / MemuAI by default.")+'</small>';
    return '<div class="bext-copyline bext-folder"><code title="'+esc(path)+'">'+esc(path)+'</code>'+button("copy-folder",L("Копировать путь","Copy path"))+button("reveal-folder",L("Показать папку","Show folder"))+'</div>';
  }
  /* Version line + what happens next; the extension reports its build on connect. */
  function updateHtml(){
    const u=state?.update;if(!state?.paired||!u)return "";
    const installed=u.installed||(u.legacy?L("старая сборка","an older build"):"");
    const auto=checkbox("auto_update",L("Обновлять расширение автоматически","Update the extension automatically"),L("Когда выходит новая версия MemuAI, расширение в папке MemuAI обновляется само и перезапускается между задачами.","When MemuAI ships a new version, the extension in the MemuAI folder updates itself and restarts between tasks."));
    let tone="",title="",text="",actions="";
    if(!state.connected||u.state==="unknown"){title=L("Версия расширения появится после подключения","The extension version appears once it connects");text=L("Доступная версия: ","Available version: ")+esc(u.available);}
    else if(u.state==="current"){tone="ok";title=L("Расширение актуально","The extension is up to date");text=L("Версия ","Version ")+esc(u.installed||u.available)+(u.managed?L(" · обновляется автоматически"," · updates automatically"):"");}
    else if(u.state==="managed"||u.state==="reloading"){tone="info";title=L("Доступна версия ","Version ")+esc(u.available)+L(""," is available");text=L("Расширение обновится само и перезапустится. Если этого не произошло, нажмите «Обновить сейчас».","The extension updates and restarts on its own. If it did not, select Update now.");actions=button("update-now",L("Обновить сейчас","Update now"),true);}
    else if(u.state==="restart"){tone="info";title=L("Новая версия уже в папке","The new version is in the folder");text=L("Перезапустите Chrome или нажмите «Обновить» у MemuAI на странице chrome://extensions — после этого обновления будут приходить сами.","Restart Chrome or select Reload for MemuAI on chrome://extensions. Later updates then arrive on their own.");actions='<div class="bext-copyline"><code>chrome://extensions</code>'+button("copy-extensions",L("Копировать адрес","Copy address"))+'</div>';}
    else if(u.state==="stuck"){tone="warn";title=L("Не удалось обновить автоматически","Automatic update did not complete");text=L("Откройте chrome://extensions и нажмите «Обновить» у MemuAI. Файлы новой версии уже в папке.","Open chrome://extensions and select Reload for MemuAI. The new files are already in the folder.");actions=button("update-now",L("Попробовать снова","Try again"));}
    else{tone="warn";title=L("Доступна версия ","Version ")+esc(u.available)+L(""," is available");text=(installed?L("Установлена: ","Installed: ")+esc(installed)+". ":"")+L("Эта копия распакована вручную, поэтому MemuAI не может обновить её сам. Создайте папку расширения в первом шаге, удалите старую копию в chrome://extensions и загрузите новую папку. Подключить понадобится один раз — дальше обновления будут приходить сами.","This copy was extracted manually, so MemuAI cannot update it. Create the extension folder in step one, remove the old copy on chrome://extensions and load the new folder. You connect once more; later updates arrive on their own.");actions=button("install",L("Создать папку расширения","Create the extension folder"),true);}
    return '<div class="bext-update'+(tone?' is-'+tone:'')+'"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v4.5h-4.5"/></svg><div><strong>'+title+'</strong><p>'+text+'</p>'+(actions?'<div class="bext-actions">'+actions+'</div>':"")+'</div></div>'+auto;
  }
  const status=()=>state.connected?L("Chrome подключён","Chrome is connected"):state.paired?L("Ожидаю расширение","Waiting for the extension"):L("Ещё не подключено","Not connected yet");
  function paint(){
    const stepDone=[!!(state?.update?.managed_path||state?.managed_path),state.connected,state.connected];
    const host=document.getElementById("bext-root");if(!host||!state)return;
    const steps=[
      [L("Подготовьте папку расширения","Prepare the extension folder"),L("MemuAI сам создаст папку с расширением и будет держать её в актуальном состоянии: новые версии приходят автоматически. Можно и по-старому — скачать ZIP и распаковать его в постоянную папку.","MemuAI creates the extension folder for you and keeps it current: new versions arrive automatically. You can still download the ZIP and extract it into a permanent folder."),'<div class="bext-actions">'+(state?.update?.managed_path?button("install",L("Обновить файлы в папке","Refresh the folder files")):button("install",L("Создать папку расширения","Create the extension folder"),true))+button("download",L("Скачать ZIP","Download ZIP"))+button("save-as",L("Сохранить ZIP как…","Save ZIP as…"))+'</div>'+folderHtml()],
      [L("Добавьте папку в Chrome","Add the folder in Chrome"),L("Откройте адрес ниже. Включите «Режим разработчика», нажмите «Загрузить распакованное расширение» и выберите папку из первого шага.","Open the address below. Enable Developer mode, select Load unpacked, and choose the folder from step one."),'<div class="bext-copyline"><code>chrome://extensions</code>'+button("copy-extensions",L("Копировать адрес","Copy address"))+'</div>'],
      [L("Подключите MemuAI","Connect MemuAI"),L("В настройках расширения нажмите «Подключить через MemuAI» и подтвердите запрос в приложении. Ссылка memuai:// передаст адрес автоматически. Если ссылка не открывается, используйте адрес и одноразовый код ниже.","In extension settings, click Connect through MemuAI and approve the request in the app. The memuai:// link supplies the address automatically. If the link does not open, use the address and one-time code below."),'<label class="bext-address"><span>'+L("Адрес этого MemuAI","This MemuAI address")+'</span><div class="bext-copyline"><code>'+esc(location.origin)+'</code>'+button("copy-address",L("Копировать","Copy"))+'</div></label><div class="bext-actions">'+button("pair",L("Получить код подключения","Get connection code"),true)+'</div><div id="bext-code" class="bext-code" role="status"></div>']
    ];
    host.innerHTML='<div class="bext-connection '+(state.connected?'is-connected':'')+'"><span class="bext-browser-mark">'+icon+'</span><div><strong id="bext-status">'+status()+'</strong><p>'+(state.connected?L("Модели и история чатов доступны в боковой панели.","Your models and chat history are available in the side panel."):state.paired?L("Откройте Chrome. Если связь не появилась, проверьте адрес в настройках расширения.","Open Chrome. If it stays disconnected, check the address in extension settings."):L("Установка занимает три шага. Код потребуется только при первом подключении.","Install in three steps. The code is only needed for the first connection."))+'</p></div>'+button("refresh",L("Проверить связь","Check connection"))+'</div><div id="bext-feedback" class="bext-feedback" role="status" aria-live="polite" hidden></div>'+updateHtml()
      +K().section({title:L("Установка расширения","Install the extension"),card:false,body:'<ol class="bext-steps">'+steps.map((s,i)=>'<li class="'+(stepDone[i]?'is-done':'')+'"><span class="bext-step-number">'+(i+1)+'</span><div><h4>'+s[0]+'</h4><p>'+s[1]+'</p>'+s[2]+'</div></li>').join("")+'</ol>'})
      +K().section({title:L("История браузера","Browser history"),desc:L("Эти настройки также доступны в самом расширении и синхронизируются автоматически.","These settings are also available inside the extension and sync automatically."),body:checkbox("save_to_main",L("Показывать новые чаты в MemuAI","Show new chats in MemuAI"),L("По умолчанию разговоры из расширения сохраняются в отдельном списке. Включите, чтобы новые чаты появились в общем списке приложения. Старые разговоры не перемещаются.","Extension conversations have a separate history by default. Enable this to show new chats in the app's main list. Existing conversations are not moved."))})
      +K().section({title:L("Доступ к браузеру","Browser access"),desc:L("Выберите, что MemuAI разрешено делать в вашем Chrome.","Choose what MemuAI can do in your Chrome."),body:
        checkbox("read_pages",L("Читать страницы","Read pages"),L("Добавлять страницу в чат по вашему выбору. Быстрый доступ использует расширение, когда оно доступно; без него работает обычный способ.","Add page context when you choose. Quick Access uses the extension when available, with the existing method as fallback."))
        +K().row({label:L("Управление страницами и вкладками","Control pages and tabs"),desc:L("Открытие и закрытие вкладок, переходы, ввод и нажатия из чата или CLI.","Opening and closing tabs, navigation, typing and clicks from chat or CLI."),control:'<select class="pinput bext-policy" aria-label="'+L("Управление страницами и вкладками","Control pages and tabs")+'" data-bext-key="control">'+[["ask",L("Спрашивать перед действием","Ask before acting")],["allow",L("Разрешить","Allow")],["deny",L("Запретить","Deny")]].map(([v,t])=>'<option value="'+v+'" '+(state.control===v?'selected':'')+'>'+t+'</option>').join("")+'</select>'})
        +checkbox("dfy",L("DFY в моём Chrome","DFY in my Chrome"),L("Отдельные фоновые вкладки с вашими входами на сайты. Chrome покажет штатное уведомление об отладке. При отключении расширения задача остановится.","Separate background tabs with your site sessions. Chrome shows its standard debugger notice. Disconnecting the extension stops the task."))
        +checkbox("schedules",L("Действия по расписанию","Scheduled actions"),L("Заранее разрешить сохранённым задачам управлять Chrome без диалогов подтверждения. Настройка «Запретить» выше продолжает действовать.","Preauthorize saved tasks to control Chrome without confirmation dialogs. Deny above still applies."))})
      +'<p class="bext-footnote">'+L("Chrome ограничивает доступ к своим служебным страницам и магазину расширений. Скриншот обычной вкладки доступен, когда она активна; DFY снимает свои фоновые вкладки без переключения вашей.","Chrome restricts its internal pages and extension store. Regular screenshots require the tab to be active; DFY captures its own background tabs without switching yours.")+'</p>'
      +(state.paired?'<div class="bext-disconnect"><p>'+L("Отключение удалит доступ этого расширения. Для повторного подключения понадобится новый код.","Disconnecting revokes this extension\'s access. Reconnecting will require a new code.")+'</p>'+button("revoke",L("Отключить расширение","Disconnect extension"))+'</div>':"");
    paintCode();const value=document.getElementById("bext-value");if(value)value.textContent=state.connected?L("Подключено","Connected"):L("Не подключено","Not connected");
  }
  function paintCode(){
    const el=document.getElementById("bext-code");if(!el)return;
    el.innerHTML=code&&Date.now()<codeExpires?'<div><code>'+esc(code)+'</code>'+button("copy-code",L("Копировать код","Copy code"))+'</div><small>'+L("Действует 5 минут и подходит для одного подключения.","Valid for 5 minutes and one connection.")+'</small>':code?'<small>'+L("Срок кода истёк. Получите новый код.","The code expired. Get a new one.")+'</small>':"";
  }
  function feedback(message,error=false,path=""){
    const el=document.getElementById("bext-feedback");if(!el)return;el.hidden=false;el.classList.toggle("is-error",error);el.replaceChildren(document.createTextNode(message));
    if(path){const reveal=document.createElement("button");reveal.type="button";reveal.className="bext-button";reveal.dataset.bext="reveal";reveal.dataset.path=path;reveal.textContent=L("Показать в папке","Show in folder");el.append(reveal);}
  }
  async function copy(text){
    if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);return;}
    const input=document.createElement("textarea");input.value=text;input.style.position="fixed";input.style.opacity="0";document.body.append(input);input.select();
    try{if(!document.execCommand("copy"))throw new Error(L("Не удалось скопировать. Выделите адрес вручную.","Copy failed. Select the address manually."));}finally{input.remove();}
  }
  async function load(){try{state=await request("/status");paint();}catch(error){const host=document.getElementById("bext-root");if(host)host.textContent=error.message;}}
  function mount(){
    if(!document.querySelector('link[href^="/browser-extension-settings.css"]')){const link=document.createElement("link");link.rel="stylesheet";link.href="/browser-extension-settings.css?v=5.0-r77";document.head.append(link);}
    const row=document.querySelector('[data-sx-open="int-chrome"], [data-sx-sub-open="int-chrome"]');if(row&&!row._bext){row._bext=true;row.addEventListener("click",load);}
    const host=document.getElementById("bext-root");if(!host||host._bext)return;host._bext=true;
    host.addEventListener("click",async event=>{
      const target=event.target.closest("[data-bext]"),action=target?.dataset.bext;if(!action||actionBusy)return;event.preventDefault();actionBusy=true;target.disabled=true;
      try{
        if(action==="download"||action==="save-as"){
          feedback(L("Сохраняю расширение…","Saving the extension…"));const saved=await request("/download",{mode:action==="save-as"?"ask":"downloads"});
          if(saved.cancelled)feedback(L("Сохранение отменено.","Save cancelled."));
          else if(saved.ok&&saved.path)feedback(L("Сохранено: ","Saved: ")+saved.path,false,saved.path);
          else throw new Error(L("Расширение не сохранилось. Повторите попытку.","The extension was not saved. Try again."));
        }else if(action==="install"){
          feedback(L("Создаю папку расширения…","Creating the extension folder…"));const result=await request("/install",{});state=result.status||state;paint();
          feedback(L("Готово: ","Done: ")+result.path+L(". Теперь выберите эту папку в Chrome (шаг 2).",". Now choose this folder in Chrome (step 2)."));
        }else if(action==="reveal-folder"){await request("/reveal",{});}
        else if(action==="copy-folder"){await copy(state?.update?.managed_path||"");feedback(L("Путь скопирован.","Path copied."));}
        else if(action==="update-now"){
          feedback(L("Обновляю расширение…","Updating the extension…"));const result=await request("/update",{});state=result.status||state;paint();
          feedback(result.action==="reload"?L("Новая версия записана, расширение перезапускается.","The new version is written; the extension is restarting."):result.action==="restart"?L("Новая версия записана. Перезапустите Chrome, чтобы она включилась.","The new version is written. Restart Chrome to switch to it."):result.state==="current"?L("Расширение уже актуально.","The extension is already up to date."):L("Эту копию нельзя обновить автоматически — создайте папку расширения.","This copy cannot be updated automatically: create the extension folder."));
        }else if(action==="pair"){const result=await request("/pairing",{});code=result.code;codeExpires=Date.now()+(result.expires_in||300)*1000;paintCode();}
        else if(action==="reveal"){await api("/api/pg/reveal-file",{method:"POST",body:{path:target.dataset.path}});}
        else if(action.startsWith("copy-")){await copy(action==="copy-code"?code:action==="copy-extensions"?"chrome://extensions":location.origin);feedback(L("Скопировано.","Copied."));}
        else if(action==="revoke"){await request("/revoke",{});code="";await load();feedback(L("Расширение отключено.","Extension disconnected."));if(window.refreshIntegrations)refreshIntegrations();}
        else{await load();feedback(state.connected?L("Связь с Chrome работает.","Chrome connection is working."):L("Chrome пока не подключён. Проверьте три шага выше.","Chrome is not connected yet. Check the three steps above."));}
      }catch(error){feedback(error.message,true);}finally{actionBusy=false;target.disabled=false;}
    });
    host.addEventListener("change",async event=>{
      const input=event.target,key=input.dataset.bextKey;if(!key)return;const previous=state?.[key];input.disabled=true;
      try{state=await request("/settings",{[key]:input.type==="checkbox"?input.checked:input.value});feedback(L("Настройка сохранена.","Setting saved."));if(window.refreshIntegrations)await refreshIntegrations();}
      catch(error){if(input.type==="checkbox")input.checked=!!previous;else input.value=previous;feedback(error.message,true);}finally{input.disabled=false;}
    });
    clearInterval(timer);timer=setInterval(async()=>{const current=document.getElementById("bext-root");if(!current){clearInterval(timer);return;}if(document.hidden||current.closest(".sx-sub")?.hidden||actionBusy)return;paintCode();try{const fresh=await request("/status");if(["connected","paired","save_to_main","read_pages","control","dfy","schedules","auto_update"].some(key=>fresh[key]!==state?.[key])||JSON.stringify(fresh.update||{})!==JSON.stringify(state?.update||{})){state=fresh;paint();}}catch(_){}},5000);load();
  }
  return {listHtml,subHtml,mount};
})();
