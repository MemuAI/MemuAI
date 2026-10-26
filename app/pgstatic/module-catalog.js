
"use strict";
const MemuModuleCatalog = (() => {
  const E = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const T = (ru,en) => typeof PG_LANG !== "undefined" && PG_LANG === "en" ? en : ru;
  const language = () => typeof PG_LANG !== "undefined" && PG_LANG === "en";
  const text = (row,key) => language() ? row[key + "_en"] || row[key] || "" : row[key] || row[key + "_en"] || "";
  const profile = () => typeof S !== "undefined" ? String(S.profileState?.active_id || "default") : "default";
  const ico = name => typeof icon === "function" ? (typeof ICONS !== "undefined" && !ICONS[name] ? icon("work") : icon(name)) : "";
  const categories = {productivity:["Продуктивность","Productivity"], writing:["Тексты","Writing"], development:["Разработка","Development"], education:["Обучение","Education"], creativity:["Творчество","Creativity"], utilities:["Инструменты","Utilities"]};
  const permissions = {chat:["Обращения к ИИ","AI requests"],models:["Список моделей","Model list"],storage:["Свои данные","Own data"],files:["Выбор файлов","File selection"],download:["Сохранение файлов","File saving"],clipboard:["Буфер обмена","Clipboard"],web:["Поиск в интернете","Web search"],external:["Открытие ссылок","Open links"],media:["Медиа и микрофон","Media and microphone"],background:["Работа в фоне","Background work"],notify:["Уведомления","Notifications"],"net-import":["Загрузка из интернета","Internet downloads"],music:["Музыка","Music"],learn:["Обучение","Learning"],database:["Базы данных","Databases"]};
  const label = (map,id) => map[id] ? T(...map[id]) : id;
  async function api(path,body,active) {
    const response = await fetch("/api/pg/module-catalog" + path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...body,profile_id:active})});
    let result; try { result = await response.json(); } catch (_) { throw new Error(T("Не удалось прочитать ответ каталога","Could not read catalogue response")); }
    if (!response.ok || result.error && !result.modules) throw new Error(result.error || `HTTP ${response.status}`);
    if (active !== profile()) throw new Error(T("Профиль сменился. Откройте каталог заново","Profile changed. Reopen the catalogue"));
    return result;
  }
  function download(name,value,type="application/json;charset=utf-8") {
    const blob = new Blob([value],{type}), url = URL.createObjectURL(blob);
    const link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function external(url) { const link=document.createElement("a");link.href=url;link.target="_blank";link.rel="noopener noreferrer";link.click(); }
  async function mount(host,callbacks={}) {
    const active=profile(), token=Symbol("catalogue");host._catalogueToken=token;
    const alive=()=>host.isConnected && host._catalogueToken===token && profile()===active && !!host.querySelector(".mc-workspace");
    let data=null, selected="", busy=false, query="", category="", installed=false, listingRequest=0;
    let detailTab="description", sourceFile="main.js", sourceRequest=0;
    const sourceCache=new Map();
    host.innerHTML=`<section class="modules-pane mc-workspace ms-catalog">
      <header class="modules-head"><div class="modules-head-main"><div class="settings-title">${T("Каталог модулей","Module catalogue")}</div><p class="sub">${T("Дополнения из репозитория MemuAI. Вы выбираете, что установить и включить.","Additions from the MemuAI repository. You choose what to install and enable.")}</p></div>
      <div class="modules-head-actions"><button class="pbtn ghost" id="mc-contribute">${ico("plus")} ${T("Предложить модуль","Contribute a module")}</button></div></header>
      <nav class="ms-nav" aria-label="${T('Разделы модулей','Module sections')}"><button type="button" id="mc-back">${T('Мои модули','My modules')}</button><span aria-current="page">${T('Каталог','Catalogue')}</span><span class="ms-nav-spacer"></span><button type="button" id="mc-source-toggle">${ico('settings')}${T('Источник','Source')}</button></nav>
      <div id="mc-banner" class="mc-banner" role="status"></div>
      <div class="mc-toolbar"><label class="modules-search">${ico("search")}<input id="mc-search" type="search" aria-label="${T("Найти модуль","Find a module")}" placeholder="${T("Название, автор, возможности…","Name, author, features…")}"></label>
      <label class="mc-category"><span class="sr-only">${T("Категория","Category")}</span><select id="mc-category"><option value="">${T("Все категории","All categories")}</option></select></label>
      <label class="module-check compact"><input id="mc-installed" type="checkbox">${T("Установленные","Installed")}</label><button class="pbtn ghost" id="mc-refresh">${ico("refresh")} ${T("Обновить","Refresh")}</button></div>
      <div id="mc-notice" class="mc-notice" role="status" hidden></div>
      <div class="mc-content"><div class="mc-directory"><p id="mc-result-count" class="mc-result-count"></p><div id="mc-results" class="mc-results" aria-live="polite"></div></div><article id="mc-detail" class="mc-detail" hidden></article></div>
      <details class="mc-source"><summary>${T("Источник каталога","Catalogue source")}</summary><p>${T("Официальный каталог проверяют сопровождающие репозитория. Если проект прекратит развиваться, здесь можно выбрать поддерживаемый сообществом источник. Новый источник отвечает за весь размещённый в нём код.","Repository maintainers review the official catalogue. If development stops, you can choose a community-maintained source here. The new source is responsible for all code it distributes.")}</p>
      <form id="mc-source-form"><label for="mc-source-url">${T("Адрес JSON-каталога (HTTPS)","JSON catalogue address (HTTPS)")}</label><div class="mc-source-row"><input id="mc-source-url" type="url" required spellcheck="false"><button class="pbtn" type="submit">${T("Сохранить источник","Save source")}</button><button class="pbtn ghost" id="mc-source-reset" type="button">${T("Официальный","Official")}</button></div></form></details>
      <section id="mc-authoring" class="mc-authoring" hidden></section></section>`;
    host.querySelector('#mc-source-toggle').onclick=()=>{const section=host.querySelector('.mc-source');section.open=true;section.scrollIntoView({block:'nearest',behavior:'auto'});host.querySelector('#mc-source-url').focus({preventScroll:true});};
    const $=selector=>host.querySelector(selector);
    function notice(message,error=false) { if(!alive())return;$("#mc-notice").hidden=!message;$("#mc-notice").textContent=message;$("#mc-notice").classList.toggle("error",error); }
    function loading(on) { busy=on; for(const id of ["#mc-refresh","#mc-source-reset"])$(id).disabled=on;$("#mc-source-form button[type=submit]").disabled=on; }
    function status(row) { return ({available:T("Не установлен","Not installed"),installed:T("Установлен","Installed"),update:T("Есть обновление","Update available"),conflict:T("ID уже занят","ID already in use")})[row.status] || row.status; }
    function render() {
      if(!alive()||!data)return;
      const needle=query.trim().toLocaleLowerCase(), rows=data.modules.filter(row=>(!category||row.category===category)&&(!installed||row.installed)&&(!needle||[row.id,row.title,row.title_en,row.summary,row.summary_en,row.description,row.description_en,row.author,label(categories,row.category),...(row.tags||[])].join(" ").toLocaleLowerCase().includes(needle)));
      if(selected && !rows.some(row=>row.id===selected)){selected=rows[0]?.id || "";detailTab="description";}
      $("#mc-result-count").textContent=`${T("Модули","Modules")} · ${rows.length}`;
      $("#mc-results").innerHTML=rows.length ? rows.map(row=>`<button type="button" class="mc-card${selected===row.id?" selected":""}" data-mc-id="${E(row.id)}" aria-pressed="${selected===row.id}"><span class="mc-card-icon" aria-hidden="true">${ico(row.icon||"work")}</span><span class="mc-card-main"><span class="mc-card-heading"><b>${E(text(row,"title"))}</b><span class="mc-status ${E(row.status)}">${E(status(row))}</span></span><span class="mc-card-summary">${E(text(row,"summary"))}</span><span class="mc-card-meta">${E(row.author)} <span>v${E(row.version)}</span><span>${E(label(categories,row.category))}</span></span></span>${ico("chevron-right")}</button>`).join("") : `<div class="mc-empty"><b>${T("Модулей не найдено","No modules found")}</b><p>${T("Измените поиск или категорию. Свой модуль можно предложить через pull request.","Try another search or category. You can contribute your own module through a pull request.")}</p></div>`;
      $("#mc-results").querySelectorAll("[data-mc-id]").forEach(button=>button.onclick=()=>{selected=button.dataset.mcId;detailTab="description";render();$("[data-mc-id='"+selected+"']")?.focus({preventScroll:true});});
      detail();
    }
    function detail() {
      if(!alive()||!data)return;
      const row=data.modules.find(row=>row.id===selected), box=$("#mc-detail");box.hidden=!row;
      host.querySelector('.mc-workspace').classList.toggle('mc-reading',!!row&&detailTab==='code');
      if(!row)return;
      const action=row.status==="available"?T("Установить","Install"):row.status==="update"?T("Обновить модуль","Update module"):"";
      box.innerHTML=`<header><div><span class="mc-category-name">${E(label(categories,row.category))}</span><h3>${E(text(row,"title"))}</h3><span class="mc-card-meta">${E(row.author)} · v${E(row.version)}</span></div><button class="pbtn ghost" id="mc-detail-close" aria-label="${T("Закрыть сведения","Close details")}">${ico("close")}</button></header>
      <div class="mc-detail-actions">${action?`<button class="pbtn" id="mc-install" ${!row.compatible||busy?"disabled":""}>${ico("download")} ${action}</button>`:""}${row.can_rollback?`<button class="pbtn ghost" id="mc-rollback" ${busy?"disabled":""}>${T("Вернуть предыдущую версию","Restore previous version")}</button>`:""}${row.installed?`<button class="pbtn ghost" id="mc-manage">${T("Мои модули","My modules")}</button>`:""}</div>
      <div class="mc-detail-tabs" role="tablist" aria-label="${T('Сведения о модуле','Module details')}"><button type="button" id="mc-description-tab" role="tab" aria-controls="mc-description-panel" aria-selected="${detailTab==='description'}" tabindex="${detailTab==='description'?0:-1}">${T('Описание','Overview')}</button><button type="button" id="mc-code-tab" role="tab" aria-controls="mc-code-panel" aria-selected="${detailTab==='code'}" tabindex="${detailTab==='code'?0:-1}">${ico('code')}${T('Исходный код','Source code')}</button></div>
      <section id="mc-description-panel" role="tabpanel" aria-labelledby="mc-description-tab" ${detailTab==='description'?'':'hidden'}>
      <p class="mc-description">${E(text(row,"description")||text(row,"summary"))}</p><dl class="mc-facts"><div><dt>${T("Лицензия","License")}</dt><dd>${E(row.license||T("Не указана","Unspecified"))}</dd></div><div><dt>${T("Размер пакета","Package size")}</dt><dd>${(row.package.bytes/1024).toFixed(1)} KB</dd></div>${row.min_app_version?`<div><dt>MemuAI</dt><dd>≥ ${E(row.min_app_version)}</dd></div>`:""}${row.installed?`<div><dt>${T("Установлена версия","Installed version")}</dt><dd>${E(row.installed_version)} · ${row.enabled?T("включён","enabled"):T("выключен","disabled")}</dd></div>`:""}</dl>
      <h4>${T("Доступы модуля","Module permissions")}</h4><ul class="mc-permissions">${row.permissions.map(p=>`<li>${E(label(permissions,p))}</li>`).join("")||`<li>${T("Без доступа к возможностям MemuAI","No access to MemuAI capabilities")}</li>`}</ul>
      <p class="mc-install-hint">${T("После установки или обновления модуль остаётся выключенным. Включите его в «Моих модулях», когда будете готовы. Данные и настройки окна сохраняются при обновлении.","Installed or updated modules stay disabled. Enable them in My modules when you are ready. Updates preserve data and window settings.")}</p>
      ${!row.compatible?`<p class="mc-warning">${T("Для установки нужна более новая версия MemuAI. Исходный код можно посмотреть сейчас.","Installing requires a newer MemuAI version. You can still inspect the source code.")}${row.unsupported_permissions?.length?` ${T('Неподдерживаемые доступы:','Unsupported permissions:')} ${E(row.unsupported_permissions.join(', '))}.`:''}</p>`:""}${row.status==="conflict"?`<p class="mc-warning">${T("Модуль с этим ID установлен из другого источника. Каталог его не заменит.","This ID is used by a module from another source. The catalogue will not replace it.")}</p>`:""}
      ${row.local_modified?`<label class="mc-replace"><input type="checkbox" id="mc-replace">${T("Заменить мои изменения кода (предыдущая версия будет сохранена)","Replace my code changes (the previous version will be retained)")}</label>`:""}

      <details class="mc-checksum"><summary>SHA-256</summary><code>${E(row.package.sha256)}</code></details></section>
      <section id="mc-code-panel" role="tabpanel" aria-labelledby="mc-code-tab" ${detailTab==='code'?'':'hidden'}></section>`;
      for(const tab of ['description','code']){
        $("#mc-"+tab+"-tab").onclick=()=>{detailTab=tab;detail();$("#mc-"+tab+"-tab")?.focus({preventScroll:true});};
        $("#mc-"+tab+"-tab").onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();detailTab=event.key==='Home'?'description':event.key==='End'?'code':tab==='code'?'description':'code';detail();$("#mc-"+detailTab+"-tab")?.focus({preventScroll:true});};
      }
      if(detailTab==='code')showCode(row);
      $("#mc-detail-close").onclick=()=>{selected="";box.hidden=true;render();};
      if($("#mc-manage"))$("#mc-manage").onclick=callbacks.back;
      const change=async path=>{
        if(busy)return;
        if(row.local_modified&&!$("#mc-replace")?.checked){notice(T("Подтвердите замену своих изменений кода.","Confirm replacement of your code changes."),true);$("#mc-replace")?.focus();return;}
        const replaceLocal=!!$("#mc-replace")?.checked;
        loading(true);detail();notice(T("Проверяем и устанавливаем пакет…","Verifying and installing the package…"));
        try {await api(path,{id:row.id,sha256:row.package.sha256,source:data.source,replace_local:replaceLocal},active);await callbacks.changed?.();await load(false);notice(T("Готово. Модуль выключен; включите его в «Моих модулях».","Done. The module is disabled; enable it in My modules."));}
        catch(error){notice(error.message,true);}finally{if(alive()){loading(false);detail();}}
      };

      if($("#mc-install"))$("#mc-install").onclick=()=>change("/install");
      if($("#mc-rollback"))$("#mc-rollback").onclick=()=>change("/rollback");
    }
    async function showCode(row) {
      const key=data.source+'|'+row.id+'|'+row.package.sha256, request=++sourceRequest;
      const pane=$('#mc-code-panel');
      const current=()=>alive()&&detailTab==='code'&&selected===row.id&&request===sourceRequest&&$('#mc-code-panel')===pane;
      pane.innerHTML=`<p class="mc-code-loading" role="status">${T('Проверяем и загружаем исходники…','Verifying and loading source files…')}</p>`;
      try {
        if(!sourceCache.has(key)){
          while(sourceCache.size>=3)sourceCache.delete(sourceCache.keys().next().value);
          sourceCache.set(key,api('/preview',{id:row.id,source:data.source,sha256:row.package.sha256},active));
        }
        const value=await sourceCache.get(key);if(!current())return;
        const names=Object.keys(value.files);
        if(!names.includes(sourceFile))sourceFile=names.includes('main.js')?'main.js':names[0];
        pane.innerHTML=`<p class="mc-code-note">${ico('shield')}${T('Размер и SHA-256 проверены. Код показан как текст и не запускается.','Size and SHA-256 verified. Source is displayed as text and never executed.')}</p>
          <div class="mc-code-tools"><span>${E(text(row,'title'))} · v${E(value.version)}</span><button type="button" class="pbtn ghost" id="mc-source-package">${ico('download')}${T('Скачать пакет','Download package')}</button></div>
          <div class="mc-source-browser"><nav class="mc-file-list" aria-label="${T('Файлы пакета','Package files')}">${names.map(name=>`<button type="button" data-mc-file="${E(name)}" aria-pressed="${sourceFile===name}">${ico('code')}<span>${E(name)}</span></button>`).join('')}</nav><div class="mc-file-view"><div class="mc-file-heading"><b id="mc-file-name"></b><button type="button" class="pbtn ghost" id="mc-copy-file">${ico('copy')}${T('Копировать','Copy')}</button><button type="button" class="pbtn ghost" id="mc-download-file" aria-label="${T('Скачать выбранный файл','Download selected file')}">${ico('download')}</button></div><pre class="mc-code-text" tabindex="0" aria-label="${T('Исходный код, только чтение','Read-only source code')}"><code></code></pre><p id="mc-code-status" class="mc-code-status" role="status"></p></div></div>`;
        function displayFile(name){
          sourceFile=name;
          pane.querySelector('#mc-file-name').textContent=name;
          pane.querySelector('.mc-code-text code').textContent=value.files[name];
          pane.querySelectorAll('[data-mc-file]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mcFile===name)));
          pane.querySelector('#mc-code-status').textContent='';
        }
        pane.querySelectorAll('[data-mc-file]').forEach(button=>button.onclick=()=>displayFile(button.dataset.mcFile));
        pane.querySelector('#mc-source-package').onclick=()=>download(value.package_name,value.package_text);
        pane.querySelector('#mc-download-file').onclick=()=>download(sourceFile.split('/').pop(),value.files[sourceFile],'text/plain;charset=utf-8');
        pane.querySelector('#mc-copy-file').onclick=async()=>{
          const status=pane.querySelector('#mc-code-status');
          try{
            if(typeof copyText==='function'){if(!await copyText(value.files[sourceFile],false))throw new Error('Copy failed');}
            else await navigator.clipboard.writeText(value.files[sourceFile]);
            status.textContent=T('Скопировано','Copied');
          }catch(_){status.textContent=T('Не удалось скопировать. Выделите текст или скачайте файл.','Copy failed. Select the text or download the file.');}
        };
        displayFile(sourceFile);
        pane.scrollIntoView({block:'nearest',behavior:'auto'});
      }catch(error){
        sourceCache.delete(key);if(!current())return;
        pane.innerHTML=`<div class="mc-code-error" role="status"><p>${E(error.message)}</p><button type="button" class="pbtn ghost" id="mc-source-retry">${T('Повторить загрузку','Try again')}</button></div>`;
        pane.querySelector('#mc-source-retry').onclick=()=>showCode(row);
      }
    }
    $('#mc-results').onkeydown=event=>{
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      const buttons=[...$('#mc-results').querySelectorAll('[data-mc-id]')], index=buttons.indexOf(document.activeElement);
      if(index<0)return;event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowDown'?1:-1)));
      buttons[next]?.click();
    };
    async function load(refresh) {
      const request=++listingRequest;
      loading(true);if(!data)$("#mc-results").innerHTML=`<div class="mc-empty" role="status">${T("Загружаем каталог…","Loading catalogue…")}</div>`;
      try {
        const result=await api(`?profile_id=${encodeURIComponent(active)}${refresh?"&refresh=true":""}`,undefined,active);if(!alive()||request!==listingRequest)return;const initial=!data;data=result;
        if(initial)selected=data.modules[0]?.id || '';
        $("#mc-source-url").value=data.source;
        const cats=[...new Set(data.modules.map(row=>row.category))].sort();if(category&&!cats.includes(category))category='';$("#mc-category").innerHTML=`<option value="">${T("Все категории","All categories")}</option>`+cats.map(id=>`<option value="${E(id)}">${E(label(categories,id))}</option>`).join("");$("#mc-category").value=category;
        $("#mc-banner").innerHTML=`${ico(data.official?"shield":"info")}<div><b>${data.official?T("Каталог MemuAI","MemuAI catalogue"):T("Сторонний каталог","Third-party catalogue")}</b><span>${data.mode==="bundled"?T("Показана копия из комплекта приложения: удалённый источник недоступен. Пакеты этой копии можно установить без сети.","Showing the bundled copy: the remote source is unavailable. Bundled packages can be installed offline."):data.error?T("Показана сохранённая копия: не удалось проверить обновления.","Showing the saved copy: updates could not be checked."):data.official?T("Код проверяется сопровождающими репозитория вручную. Установка — по вашему выбору.","Code is reviewed manually by repository maintainers. You decide what to install."):T("Модули предоставляет выбранный вами источник. Это не проверка со стороны команды MemuAI.","Modules come from your chosen source. This is not a review by the MemuAI team.")}</span></div>`;
        render();
      } catch(error){if(!alive()||request!==listingRequest)return;notice(error.message,true);if(!data)$("#mc-results").innerHTML=`<div class="mc-empty">${T("Каталог недоступен. Повторите загрузку или проверьте адрес источника ниже.","Catalogue unavailable. Retry or check the source address below.")}</div>`;}
      finally{if(alive()&&request===listingRequest){loading(false);detail();}}
    }
    $("#mc-back").onclick=callbacks.back;
    $("#mc-search").oninput=e=>{query=e.target.value;render();};$("#mc-category").onchange=e=>{category=e.target.value;render();};$("#mc-installed").onchange=e=>{installed=e.target.checked;render();};$("#mc-refresh").onclick=()=>load(true);
    async function source(url) {if(busy)return;loading(true);notice("");try{await api("/source",{source:url},active);if(!alive())return;data=null;selected="";detailTab='description';category='';sourceCache.clear();$("#mc-detail").hidden=true;await load(true);}catch(error){notice(error.message,true);}finally{if(alive())loading(false);}}
    $("#mc-source-form").onsubmit=e=>{e.preventDefault();source($("#mc-source-url").value);};$("#mc-source-reset").onclick=()=>source("");
    $("#mc-contribute").onclick=async()=>{
      const box=$("#mc-authoring");if(!box.hidden){box.hidden=true;return;}box.hidden=false;box.innerHTML=`<p role="status">${T("Загружаем ваши модули…","Loading your modules…")}</p>`;
      try {
        const result=await api(`/authoring?profile_id=${encodeURIComponent(active)}`,undefined,active);if(!alive())return;
        box.innerHTML=`<h3>${T("Модуль для pull request","A module for a pull request")}</h3><p>${T("Выберите свой модуль: MemuAI подготовит переносимый пакет и запись с контрольной суммой. Добавьте оба файла в репозиторий через pull request. Встроенные модули сначала скопируйте в мастерской.","Select a custom module: MemuAI prepares a portable package and its checksum entry. Add both to the repository through a pull request. Copy a built-in module in the workbench first.")}</p>
        ${result.modules.length?`<form id="mc-prepare-form"><div class="mc-author-grid"><label>${T("Модуль","Module")}<select name="id">${result.modules.map(row=>`<option value="${E(row.id)}">${E(text(row,"title"))} (${E(row.id)})</option>`).join("")}</select></label><label>${T("Автор","Author")}<input name="author" required maxlength="100"></label><label>${T("Лицензия (например MIT)","License (for example MIT)")}<input name="license" required maxlength="80" placeholder="MIT"></label><label>${T("Категория","Category")}<select name="category">${Object.keys(categories).map(id=>`<option value="${id}">${E(label(categories,id))}</option>`).join("")}</select></label><label class="mc-tags">${T("Теги через запятую","Comma-separated tags")}<input name="tags" maxlength="300"></label></div><button class="pbtn" type="submit">${T("Подготовить файлы","Prepare files")}</button></form>`:`<p>${T("Пока нет своих модулей. Создайте модуль или сделайте копию встроенного.","There are no custom modules yet. Create a module or copy a built-in one.")}</p>`}
        <div id="mc-prepared" class="mc-prepared" aria-live="polite"></div><button class="pbtn ghost" id="mc-guide">${T("Правила добавления в каталог","Catalogue contribution guide")}</button>`;
        $("#mc-guide").onclick=()=>external(result.contributing_url);
        if(!$("#mc-prepare-form"))return;
        $("#mc-prepare-form").onsubmit=async e=>{
          e.preventDefault();const form=e.currentTarget,button=form.querySelector("button"),values=Object.fromEntries(new FormData(form));button.disabled=true;
          try{const prepared=await api("/prepare",values,active);if(!alive())return;const entry=JSON.stringify(prepared.entry,null,2)+"\n";
            $("#mc-prepared").innerHTML=`<p>${T("Готово. Скачайте пакет в указанную папку и добавьте запись в массив modules файла module-catalog/index.json. Затем откройте pull request: команда проверит исходники перед включением в каталог.","Ready. Save the package in the indicated folder and add the entry to the modules array in module-catalog/index.json. Then open a pull request: maintainers review the source before including it.")}</p><code>${E(prepared.package_path)}</code><div class="mc-detail-actions"><button class="pbtn" id="mc-package">${T("Скачать пакет","Download package")}</button><button class="pbtn ghost" id="mc-entry">${T("Скачать запись каталога","Download catalogue entry")}</button><button class="pbtn ghost" id="mc-pr">${T("Открыть pull request","Open pull request")}</button></div><details><summary>${T("Запись каталога","Catalogue entry")}</summary><pre>${E(entry)}</pre></details>`;
            $("#mc-package").onclick=()=>download(prepared.package_path.split("/").pop(),prepared.package_text);$("#mc-entry").onclick=()=>download(values.id+".catalog-entry.json",entry);$("#mc-pr").onclick=()=>external(prepared.pull_request_url);
          }catch(error){notice(error.message,true);}finally{if(alive())button.disabled=false;}
        };
      }catch(error){if(alive())box.textContent=error.message;}
    };
    host._reloadCatalogue=()=>{if(alive())load(false);};
    await load(false);
  }
  document.addEventListener('memu:panel-restored',event=>{
    event.detail?.panel?.querySelector('#pane-modules')?._reloadCatalogue?.();
  });
  return {mount};
})();
window.MemuModuleCatalog=MemuModuleCatalog;
