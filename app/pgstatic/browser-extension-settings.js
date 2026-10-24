/* Round 44: install and connect Chrome from the native MemuAI window. */
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
  const status=()=>state.connected?L("Chrome подключён","Chrome is connected"):state.paired?L("Ожидаю расширение","Waiting for the extension"):L("Ещё не подключено","Not connected yet");
  function paint(){
    const host=document.getElementById("bext-root");if(!host||!state)return;
    const steps=[
      [L("Скачайте и распакуйте","Download and extract"),L("Распакуйте ZIP в постоянную папку: Chrome будет запускать расширение из неё.","Extract the ZIP into a permanent folder: Chrome runs the extension from that folder."),'<div class="bext-actions">'+button("download",L("Скачать расширение","Download extension"),true)+button("save-as",L("Сохранить как…","Save as…"))+'</div><small>'+L("По умолчанию: Загрузки / MemuAI","Default location: Downloads / MemuAI")+'</small>'],
      [L("Добавьте папку в Chrome","Add the folder in Chrome"),L("Откройте адрес ниже. Включите «Режим разработчика», нажмите «Загрузить распакованное расширение» и выберите папку с manifest.json.","Open the address below. Enable Developer mode, select Load unpacked, and choose the folder containing manifest.json."),'<div class="bext-copyline"><code>chrome://extensions</code>'+button("copy-extensions",L("Копировать адрес","Copy address"))+'</div>'],
      [L("Подключите MemuAI","Connect MemuAI"),L("В настройках расширения нажмите «Подключить через MemuAI» и подтвердите запрос в приложении. Ссылка memuai:// передаст адрес автоматически. Если ссылка не открывается, используйте адрес и одноразовый код ниже.","In extension settings, click Connect through MemuAI and approve the request in the app. The memuai:// link supplies the address automatically. If the link does not open, use the address and one-time code below."),'<label class="bext-address"><span>'+L("Адрес этого MemuAI","This MemuAI address")+'</span><div class="bext-copyline"><code>'+esc(location.origin)+'</code>'+button("copy-address",L("Копировать","Copy"))+'</div></label><div class="bext-actions">'+button("pair",L("Получить код подключения","Get connection code"),true)+'</div><div id="bext-code" class="bext-code" role="status"></div>']
    ];
    host.innerHTML='<div class="bext-connection '+(state.connected?'is-connected':'')+'"><span class="bext-browser-mark">'+icon+'</span><div><strong id="bext-status">'+status()+'</strong><p>'+(state.connected?L("Модели и история чатов доступны в боковой панели.","Your models and chat history are available in the side panel."):state.paired?L("Откройте Chrome. Если связь не появилась, проверьте адрес в настройках расширения.","Open Chrome. If it stays disconnected, check the address in extension settings."):L("Установка занимает три шага. Код потребуется только при первом подключении.","Install in three steps. The code is only needed for the first connection."))+'</p></div>'+button("refresh",L("Проверить связь","Check connection"))+'</div><div id="bext-feedback" class="bext-feedback" role="status" aria-live="polite" hidden></div>'
      +K().section({title:L("Установка расширения","Install the extension"),card:false,body:'<ol class="bext-steps">'+steps.map((s,i)=>'<li><span class="bext-step-number">'+(i+1)+'</span><div><h4>'+s[0]+'</h4><p>'+s[1]+'</p>'+s[2]+'</div></li>').join("")+'</ol>'})
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
    if(!document.querySelector('link[href^="/browser-extension-settings.css"]')){const link=document.createElement("link");link.rel="stylesheet";link.href="/browser-extension-settings.css?v=3.8-r45";document.head.append(link);}
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
    clearInterval(timer);timer=setInterval(async()=>{const current=document.getElementById("bext-root");if(!current){clearInterval(timer);return;}if(document.hidden||current.closest(".sx-sub")?.hidden||actionBusy)return;paintCode();try{const fresh=await request("/status");if(["connected","paired","save_to_main","read_pages","control","dfy","schedules"].some(key=>fresh[key]!==state?.[key])){state=fresh;paint();}}catch(_){}},5000);load();
  }
  return {listHtml,subHtml,mount};
})();
