/* MemuAI — меню «+» в поле ввода и чипы активных инструментов.
 *
 * Раньше в поле ввода была скрепка, которая умела только прикреплять файлы.
 * Теперь «+» открывает единое меню: вложения (файлы, буфер обмена, снимок
 * экрана), инструменты ответа (изображение, веб-поиск, глубокое
 * исследование) и быстрые действия (Live, диктовка, скиллы, инструкции
 * чата, планирование, временный чат).
 *
 * Размышления и визуализация в меню не показываются: модель сама решает,
 * когда нарисовать схему, а размышления включаются настройкой по умолчанию.
 * Если их включили командой (/think, /visual), состояние видно в поле ввода
 * и снимается там же. «Агенты» есть только в Work и MemuCode: в обычном чате
 * агентов нет, а в проектных режимах они работают лишь по просьбе пользователя.
 *
 * Состояние инструментов принадлежит чату: оно лежит в S.chatCaps и
 * сохраняется вместе с ним. Включённый инструмент показывается чипом рядом
 * с «+», и его можно снять одним нажатием.
 *
 * Модуль не подменяет функции app.js: app.js сам вызывает
 * MemuTools.open(), MemuTools.renderChips() и MemuTools.isOn().
 */
"use strict";
const MemuTools = (() => {
  let menu = null;
  let anchor = null;
  let activeIndex = -1;
  let query = "";
  let sourcesOpen = false;

  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);

  /* ---------------------------------------------------------- состояние */
  function isOn(id) {
    const caps = S.chatCaps || {};
    switch (id) {
      case "research": return !!caps.deep_research;
      case "visualize": return !!caps.visualize;
      case "agents": return caps.agents === "always";
      case "image": return !!$("#input")?.dataset.imagine;
      case "search": return typeof effectiveCap === "function" && effectiveCap("web_search");
      case "think": return typeof effectiveCap === "function" && effectiveCap("thinking");
      default: return false;
    }
  }

  function persist() {
    if (S.messages.length > 0 && typeof persistChat === "function") persistChat();
  }

  function setOn(id, on) {
    S.chatCaps = S.chatCaps || {};
    const model = currentModel();
    if (id === "research") {
      S.chatCaps.deep_research = !!on;
      if (on) {
        // Исследование и генерация картинки — взаимоисключающие режимы.
        if ($("#input")?.dataset.imagine) setImagine(false);
        toast(t("Глубокое исследование включено: ИИ сам решит, сколько искать, и напишет доклад со ссылками",
          "Deep research is on: the AI decides how much to search and writes a cited report"), "info");
      }
    } else if (id === "visualize") {
      S.chatCaps.visualize = !!on;
    } else if (id === "agents") {
      S.chatCaps.agents = on ? "always" : undefined;
      if (!on) delete S.chatCaps.agents;
      else toast(t("Агенты разрешены в этом чате: ИИ сможет раздать части задачи и работать параллельно с ними",
        "Agents are allowed in this chat: the AI can hand out parts of the task and work alongside them"), "info");
    } else if (id === "image") {
      if (on && !(S.imageModels || []).length) {
        return toast(t("Нет модели генерации изображений — добавьте аккаунт с ней в MemuAI Proxy",
          "No image model — add an account that provides one in MemuAI Proxy"), "warn");
      }
      if (on) S.chatCaps.deep_research = false;
      setImagine(!!on);
    } else if (id === "search") {
      if (!searchCapAvailable(model)) {
        return toast(t("Сначала настройте встроенный или внешний веб-поиск", "Configure built-in or external web search first"), "warn");
      }
      const meme = typeof activeMeme === "function" ? activeMeme() : null;
      if (meme) return toast(t("Веб-поиск в этом чате задан мемесом", "Web search in this chat is fixed by the agent"), "warn");
      S.chatCaps.web_search = !!on;
    } else if (id === "think") {
      S.chatCaps.thinking = !!on;
    }
    persist();
    renderCaps();
  }

  function toggle(id) { setOn(id, !isOn(id)); }

  /* ---------------------------------------------------------- действия */
  async function pasteClipboard() {
    try {
      if (!navigator.clipboard?.read) throw new Error("clipboard");
      const items = await navigator.clipboard.read();
      const files = [];
      let text = "";
      for (const item of items) {
        const imageType = item.types.find((type) => type.startsWith("image/"));
        if (imageType) {
          const blob = await item.getType(imageType);
          files.push(new File([blob], `clipboard-${Date.now()}.${imageType.split("/")[1] || "png"}`, { type: imageType }));
        } else if (item.types.includes("text/plain") && !text) {
          text = await (await item.getType("text/plain")).text();
        }
      }
      if (files.length) {
        await addAttachmentFiles(files);
        toast(t("Изображение из буфера прикреплено", "Clipboard image attached"));
      } else if (text) {
        const input = $("#input");
        input.value = input.value ? input.value + "\n" + text : text;
        input.dispatchEvent(new Event("input"));
        input.focus();
      } else {
        toast(t("Буфер обмена пуст", "The clipboard is empty"), "warn");
      }
    } catch (error) {
      toast(t("Нет доступа к буферу обмена — нажмите Ctrl+V в поле ввода", "Clipboard access denied — press Ctrl+V in the message box"), "warn");
    }
  }

  /* Снимок любой области экрана: собственный выделитель MemuAI. Окно MemuAI
     на время выбора прячется; результат сразу попадает во вложения. */
  let capturing = false;
  async function captureScreen() {
    if (capturing) return;
    capturing = true;
    try {
      const shot = await api("/api/pg/screenshot", { method: "POST", body: { lang: PG_LANG, hide: true } });
      if (shot?.cancelled) return;
      if (shot?.data) {
        const blob = dataUrlBlob(shot.data);
        const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
        await addAttachmentFiles([new File([blob], `screenshot-${stamp}.png`, { type: "image/png" })]);
        toast(t("Снимок области прикреплён", "Screenshot attached"));
        $("#input")?.focus();
        return;
      }
    } catch (error) {
      const code = error?.payload?.code;
      if (code === "cancelled") return;
      if (code !== "unsupported") return toast(error.message || String(error), "warn");
    } finally {
      capturing = false;
    }
    return captureWindow();
  }

  async function captureWindow() {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      return toast(t("Снимок экрана недоступен в этом окне", "Screen capture is unavailable in this window"), "warn");
    }
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 1 }, audio: false });
      const track = stream.getVideoTracks()[0];
      const video = document.createElement("video");
      video.muted = true;
      video.srcObject = stream;
      await video.play();
      await new Promise((resolve) => setTimeout(resolve, 180));
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || track.getSettings().width || 1280;
      canvas.height = video.videoHeight || track.getSettings().height || 720;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (blob) {
        await addAttachmentFiles([new File([blob], `screenshot-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.png`, { type: "image/png" })]);
        toast(t("Снимок экрана прикреплён", "Screenshot attached"));
      }
    } catch (error) {
      if (error?.name !== "NotAllowedError" && error?.name !== "AbortError") toast(error.message || String(error), "warn");
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
    }
  }

  function editChatInstructions() {
    const current = String(S.chatSystem || "");
    panel(`<div class="tool-dialog">
      <div class="tool-dialog-mark">${icon("edit")}</div>
      <h2>${t("Инструкции для этого чата", "Instructions for this chat")}</h2>
      <p class="sub">${t("Работают только в текущем разговоре поверх общих настроек: роль, тон, формат ответа, ограничения.",
        "Apply to this conversation only, on top of your general settings: role, tone, answer format, constraints.")}</p>
      <textarea class="ptexta" id="tool-chat-system" rows="8" maxlength="12000" placeholder="${esc(t("Например: отвечай как опытный юрист, кратко и со ссылками на статьи закона.", "For example: answer as an experienced lawyer, briefly, citing the relevant articles."))}">${esc(current)}</textarea>
      <div class="row-end" style="margin-top:12px">
        ${current ? `<button class="pbtn ghost danger" id="tool-chat-system-clear">${t("Сбросить", "Clear")}</button>` : ""}
        <button class="pbtn ghost" id="tool-chat-system-cancel">${t("Отмена", "Cancel")}</button>
        <button class="pbtn" id="tool-chat-system-save">${t("Сохранить", "Save")}</button>
      </div></div>`, "tool-dialog-panel");
    const save = (value) => {
      S.chatSystem = value;
      persist();
      closePanel("choice");
      toast(value ? t("Инструкции чата сохранены", "Chat instructions saved") : t("Инструкции чата сброшены", "Chat instructions cleared"));
      renderChips();
    };
    $("#tool-chat-system-save").onclick = () => save($("#tool-chat-system").value.trim());
    $("#tool-chat-system-cancel").onclick = () => closePanel("cancel");
    if ($("#tool-chat-system-clear")) $("#tool-chat-system-clear").onclick = () => save("");
    setTimeout(() => $("#tool-chat-system")?.focus(), 30);
  }

  /* ---------------------------------------------------------- каталог */
  function catalog() {
    const project = typeof isProjectMode === "function" && isProjectMode();
    const model = currentModel();
    const searchReady = !!model && searchCapAvailable(model);
    const imageReady = (S.imageModels || []).length > 0;
    const liveVisible = pref("voice_live_visible", true) !== false;
    const liveReady = pref("voice_realtime_enabled", false) || (typeof voiceInputReady === "function" && voiceInputReady());
    const items = [
      { section: t("Добавить", "Add") },
      { id: "files", icon: "attach", label: t("Фото и файлы", "Photos and files"),
        hint: t("Изображения, PDF, DOCX, код и текст", "Images, PDF, DOCX, code and text"), run: () => $("#file-input").click() },
      // Round 29 (gdrive-picker.js): pick files on Google Drive; Docs/Sheets/Slides are exported.
      { id: "gdrive", icon: "folder", label: t("Добавить с Google Диска", "Add from Google Drive"),
        svg: window.MemuDrivePick ? MemuDrivePick.icon : "",
        hint: window.MemuDrivePick ? MemuDrivePick.menuHint() : "", hidden: !window.MemuDrivePick, run: () => MemuDrivePick.open() },
      { id: "paste", icon: "clipboard", label: t("Вставить из буфера", "Paste from clipboard"),
        hint: t("Картинка или текст из буфера обмена", "An image or text from the clipboard"), run: pasteClipboard },
      { id: "screenshot", icon: "camera", label: t("Снимок экрана", "Screenshot"),
        hint: t("Выделите любую область — она сразу во вложениях", "Select any area — attached right away"), run: captureScreen },
      { id: "sketch", icon: "edit", label: t("Нарисовать", "Sketch"),
        hint: t("Набросок от руки, фигуры, текст — прикрепится картинкой", "A freehand sketch, shapes, text — attached as an image"),
        run: () => MemuSketch.open() },
      // Round 29 (tables.js): build a table, paste it from Excel or import CSV/XLSX, attach it as Markdown.
      { id: "table", icon: "table", label: t("Таблица", "Table"),
        hint: t("Создать, вставить из Excel или импортировать CSV/XLSX", "Create, paste from Excel or import CSV/XLSX"),
        hidden: !window.MemuTables, run: () => MemuTables.openEditor() },
      { section: t("Инструменты ответа", "Answer tools") },
      // Round 29 (media-routing.js): the items only insert /image or /video — the AI also recognises such
      // requests on its own; without a model they explain where to set one up instead of being dead.
      ...(window.MemuMedia ? [
        { id: "image", icon: "image", label: t("Создать изображение", "Create an image"),
          hint: MemuMedia.pickModel("image", { explicit: true }) ? t("Вставит /image — опишите картинку", "Inserts /image — describe the picture")
            : t("Нет модели — нажмите, чтобы настроить", "No model — click to set one up"), run: () => MemuMedia.insertCommand("image") },
        { id: "video", icon: "video", label: t("Создать видео", "Create a video"),
          hint: MemuMedia.pickModel("video", { explicit: true }) ? t("Вставит /video — опишите ролик", "Inserts /video — describe the clip")
            : t("Нет модели — нажмите, чтобы настроить", "No model — click to set one up"), hidden: project, run: () => MemuMedia.insertCommand("video") },
      ] : [{ id: "image", icon: "image", toggle: true, label: t("Создать изображение", "Create an image"),
        hint: imageReady ? t("Следующее сообщение станет описанием картинки", "Your next message becomes the image prompt")
          : t("Нет модели генерации в Proxy", "No image model in Proxy"), disabled: !imageReady }]),
      { id: "search", icon: "globe", toggle: true, label: t("Веб-поиск", "Web search"),
        hint: searchReady ? t("Источник: ", "Source: ") + (typeof searchModeLabel === "function" ? searchModeLabel() || t("авто", "auto") : "")
          : t("Не настроен — откройте настройки веб-поиска", "Not configured — open web search settings"),
        disabled: !searchReady, more: searchReady ? "search-source" : "" },
      ...(searchReady && sourcesOpen ? searchSources() : []),
      { id: "research", icon: "research", toggle: true, label: t("Глубокое исследование", "Deep research"),
        hint: t("Десятки запросов, чтение источников и доклад со ссылками", "Dozens of searches, reading sources and a cited report"),
        hidden: project },
      // Агенты есть только в Work и MemuCode и только по просьбе: переключатель —
      // один из двух способов попросить (второй — написать об этом в сообщении).
      { id: "agents", icon: "users", toggle: true, label: t("Агенты", "Agents"),
        hint: t("Разрешить ИИ запускать параллельных агентов в этом чате", "Let the AI start parallel agents in this chat"),
        hidden: !project },
      { section: t("Действия", "Actions") },
      { id: "live", icon: "sparkle", label: pref("voice_realtime_enabled", false) ? t("Live-разговор (реальное время)", "Live conversation (real time)") : t("Голосовой разговор Live", "Live voice conversation"),
        hint: liveReady ? t("Разговор голосом, сохраняется в чате", "Talk by voice, saved to this chat")
          : t("Настройте голос в разделе «Голос и Live»", "Set up voice in Voice & Live settings"),
        hidden: project || !liveVisible, disabled: !liveReady, run: () => (typeof openLiveCall === "function") && openLiveCall() },
      { id: "dictate", icon: "mic", label: t("Диктовка", "Dictation"),
        hint: t("Надиктовать сообщение в поле ввода", "Dictate a message into the input"),
        disabled: !(typeof voiceInputReady === "function" && voiceInputReady()),
        run: () => startVoiceRecording("dictation") },
      { id: "skills", icon: "zap", label: t("Скиллы ИИ", "AI skills"),
        hint: t("Методики, которые ИИ подключает сам", "Playbooks the AI loads on its own"), run: () => $("#nav-skills")?.click() },
      { id: "instructions", icon: "edit", label: t("Инструкции для чата", "Chat instructions"),
        hint: S.chatSystem ? t("Заданы — нажмите, чтобы изменить", "Set — click to edit") : t("Роль, тон и формат только для этого чата", "Role, tone and format for this chat only"),
        on: !!S.chatSystem, run: editChatInstructions },
      { id: "schedule", icon: "clock", label: t("Запланировать задачу", "Schedule a task"),
        hint: t("Напоминание или регулярный запуск", "A reminder or a recurring run"), run: () => showSchedule(true) },
      { id: "temp", icon: "temp", label: S.tempChat ? t("Сделать чат постоянным", "Make this chat permanent") : t("Временный чат", "Temporary chat"),
        hint: S.tempChat ? t("Сохранить разговор в истории", "Keep the conversation in history") : t("Без памяти и истории, удалится сам", "No memory or history, deletes itself"),
        hidden: project, run: () => (S.tempChat ? $("#temp-make-permanent")?.click() : newChat(true)) },
    ];
    return items.filter((item) => !item.hidden);
  }

  /* Источники поиска раскрываются прямо в меню: отдельное контекстное меню
     закрывало бы «+». */
  function searchSources() {
    const mode = pref("web_search_mode", "auto");
    const native = !!modelCaps(currentModel()).search;
    const custom = typeof customSearchReady === "function" && customSearchReady();
    const pick = (value) => () => setSearchMode(value);
    return [
      { id: "src-auto", icon: "sparkle", sub: true, keep: true, on: mode === "auto", label: t("Автоматически", "Automatic"),
        hint: t("Встроенный поиск модели, иначе внешний", "The model's own search, otherwise external"), run: pick("auto") },
      native ? { id: "src-builtin", icon: "search", sub: true, keep: true, on: mode === "builtin", label: t("Встроенный поиск модели", "Model's built-in search"),
        hint: t("Поиск на стороне провайдера", "Search on the provider's side"), run: pick("builtin") } : null,
      custom ? { id: "src-custom", icon: "globe", sub: true, keep: true, on: mode === "custom", label: customSearchName(),
        hint: t("Внешний источник из настроек", "The external source from settings"), run: pick("custom") } : null,
      { id: "src-settings", icon: "settings", sub: true, label: t("Настройки веб-поиска", "Web search settings"),
        hint: t("Сервис, ключи, число результатов", "Service, keys, number of results"), run: () => { currentSettingsTab = "search"; showSettings(true); } },
    ].filter(Boolean);
  }

  /* ---------------------------------------------------------- меню */
  function close() {
    if (!menu) return;
    menu.remove();
    menu = null;
    anchor?.setAttribute("aria-expanded", "false");
    anchor?.classList.remove("open");
    document.removeEventListener("pointerdown", onOutside, true);
    window.removeEventListener("resize", close);
  }

  function onOutside(event) {
    if (menu && !menu.contains(event.target) && !anchor?.contains(event.target)) close();
  }

  function rowHtml(item, index) {
    const on = item.toggle ? isOn(item.id) : !!item.on;
    return `<div class="plus-row ${item.sub ? "is-sub" : ""} ${item.disabled ? "is-disabled" : ""} ${on ? "is-on" : ""}" role="menuitem${item.toggle ? "checkbox" : ""}"
        ${item.toggle ? `aria-checked="${on}"` : ""} aria-disabled="${!!item.disabled}" tabindex="-1" data-index="${index}" data-tool="${esc(item.id)}">
      <span class="plus-icon">${item.svg ? `<span class="ui-icon">${item.svg}</span>` : icon(item.icon)}</span>
      <span class="plus-text"><b>${esc(item.label)}${item.badge ? `<em>${esc(item.badge)}</em>` : ""}</b><small>${esc(item.hint || "")}</small></span>
      ${item.more ? `<button type="button" class="plus-more ${sourcesOpen ? "open" : ""}" data-more="${esc(item.more)}" aria-expanded="${sourcesOpen}" aria-label="${esc(t("Источник поиска", "Search source"))}" title="${esc(t("Источник поиска", "Search source"))}" tabindex="-1">${icon("more")}</button>` : ""}
      ${item.toggle ? `<span class="plus-switch" aria-hidden="true"><i></i></span>` : on ? `<span class="plus-dot" aria-hidden="true"></span>` : ""}
    </div>`;
  }

  function render() {
    if (!menu) return;
    const items = catalog();
    const needle = query.trim().toLowerCase();
    const visible = [];
    let section = null;
    for (const item of items) {
      if (item.section) { section = item; continue; }
      if (needle && !(`${item.label} ${item.hint || ""}`.toLowerCase().includes(needle))) continue;
      if (section) { visible.push(section); section = null; }
      visible.push(item);
    }
    const actionable = visible.filter((item) => !item.section);
    if (activeIndex >= actionable.length) activeIndex = actionable.length - 1;
    let counter = -1;
    menu.querySelector(".plus-list").innerHTML = visible.map((item) => item.section
      ? `<div class="plus-section" role="presentation">${esc(item.section)}</div>`
      : rowHtml(item, ++counter)).join("") || `<div class="plus-empty">${esc(t("Ничего не найдено", "Nothing found"))}</div>`;
    menu._items = actionable;
    highlight();
  }

  function highlight() {
    if (!menu) return;
    menu.querySelectorAll(".plus-row").forEach((row) => row.classList.toggle("is-active", +row.dataset.index === activeIndex));
    const row = menu.querySelector(`.plus-row[data-index="${activeIndex}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }

  function activate(index) {
    const item = menu?._items?.[index];
    if (!item || item.disabled) {
      if (item?.id === "search") { close(); currentSettingsTab = "search"; showSettings(true); }
      return;
    }
    if (item.toggle) {
      toggle(item.id);
      render();
      return;
    }
    if (item.keep) {
      try { item.run?.(); } catch (error) { toast(error.message || String(error), "err"); }
      render();
      return;
    }
    close();
    try { item.run?.(); } catch (error) { toast(error.message || String(error), "err"); }
  }

  function position() {
    if (!menu || !anchor) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(344, window.innerWidth - 16);
    menu.style.width = width + "px";
    const left = Math.max(8, Math.min(rect.left - 6, window.innerWidth - width - 8));
    menu.style.left = left + "px";
    const spaceAbove = rect.top - 12, spaceBelow = window.innerHeight - rect.bottom - 12;
    const openUp = spaceAbove > spaceBelow || spaceBelow < 320;
    menu.dataset.side = openUp ? "up" : "down";
    const max = Math.max(220, Math.min(560, openUp ? spaceAbove : spaceBelow));
    menu.style.maxHeight = max + "px";
    if (openUp) { menu.style.top = ""; menu.style.bottom = (window.innerHeight - rect.top + 8) + "px"; }
    else { menu.style.bottom = ""; menu.style.top = (rect.bottom + 8) + "px"; }
  }

  function open(button) {
    if (menu) { close(); return; }
    if (typeof closeFloatingMenus === "function") closeFloatingMenus();
    anchor = button;
    query = "";
    activeIndex = -1;
    sourcesOpen = false;
    menu = document.createElement("div");
    menu.className = "plus-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", t("Инструменты и вложения", "Tools and attachments"));
    menu.innerHTML = `<label class="plus-search">${icon("search")}<input type="text" placeholder="${esc(t("Найти инструмент…", "Find a tool…"))}" aria-label="${esc(t("Найти инструмент", "Find a tool"))}" spellcheck="false" autocomplete="off"><kbd>Esc</kbd></label>
      <div class="plus-list"></div>`;
    document.body.appendChild(menu);
    anchor.setAttribute("aria-expanded", "true");
    anchor.classList.add("open");
    render();
    position();
    const search = menu.querySelector("input");
    search.addEventListener("input", () => { query = search.value; activeIndex = query ? 0 : -1; render(); });
    menu.addEventListener("keydown", onKey);
    menu.addEventListener("click", (event) => {
      const more = event.target.closest("[data-more]");
      if (more) {
        event.stopPropagation();
        sourcesOpen = !sourcesOpen;
        render();
        return;
      }
      const row = event.target.closest(".plus-row");
      if (row) activate(+row.dataset.index);
    });
    menu.addEventListener("pointermove", (event) => {
      const row = event.target.closest(".plus-row");
      if (row && +row.dataset.index !== activeIndex) { activeIndex = +row.dataset.index; highlight(); }
    });
    document.addEventListener("pointerdown", onOutside, true);
    window.addEventListener("resize", close);
    requestAnimationFrame(() => search.focus());
  }

  function onKey(event) {
    const count = menu?._items?.length || 0;
    if (event.key === "Escape") { event.preventDefault(); close(); anchor?.focus(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!count) return;
      activeIndex = event.key === "ArrowDown" ? (activeIndex + 1) % count : (activeIndex - 1 + count) % count;
      highlight();
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      if (event.target.tagName === "INPUT" && event.target.value) return;
      event.preventDefault();
      activeIndex = event.key === "Home" ? 0 : count - 1;
      highlight();
      return;
    }
    if (event.key === "Enter" || (event.key === " " && event.target.tagName !== "INPUT")) {
      event.preventDefault();
      activate(activeIndex < 0 ? 0 : activeIndex);
    }
  }

  /* ---------------------------------------------------------- чипы */
  const CHIPS = [
    ["research", "research", "Исследование", "Research"],
    ["visualize", "chart", "Визуализация", "Visualization"],
    ["image", "image", "Изображение", "Image"],
    ["agents", "users", "Агенты", "Agents"],
  ];

  function renderChips() {
    const box = $("#tool-chips");
    if (!box) return;
    const project = typeof isProjectMode === "function" && isProjectMode();
    // В проектных режимах — только «Изображение» и «Агенты»; в обычном чате агентов нет.
    const chips = CHIPS.filter(([id]) => isOn(id) && (project ? (id === "image" || id === "agents") : id !== "agents"));
    let html = chips.map(([id, glyph, ru, eng]) => `<button type="button" class="tool-chip" data-chip="${id}" title="${esc(t("Нажмите, чтобы выключить", "Click to turn off"))}">
        ${icon(glyph)}<span>${esc(t(ru, eng))}</span><i class="tool-chip-x" aria-hidden="true">${icon("close")}</i></button>`).join("");
    if (S.chatSystem) {
      html += `<button type="button" class="tool-chip subtle" data-chip="instructions" title="${esc(t("Инструкции для этого чата", "Instructions for this chat"))}">${icon("edit")}<span>${esc(t("Инструкции", "Instructions"))}</span></button>`;
    }
    box.innerHTML = html;
    box.hidden = !html;
    box.querySelectorAll("[data-chip]").forEach((chip) => chip.onclick = () => {
      if (chip.dataset.chip === "instructions") return editChatInstructions();
      setOn(chip.dataset.chip, false);
    });
    const plus = $("#btn-plus");
    if (plus) {
      // Active tools are explicit removable chips; a second anonymous dot
      // suggested a hidden research mode even when only images were selected.
      plus.classList.remove("has-tools");
      plus.setAttribute("aria-label", t("Добавить файлы и инструменты", "Add files and tools"));
    }
    const input = $("#input");
    if (input && !input.dataset.imagine) {
      input.placeholder = isOn("research")
        ? t("О чём провести глубокое исследование?", "What should I research in depth?")
        : isOn("visualize") ? t("Что визуализировать?", "What should I visualize?") : pt("message");
    }
  }

  return { open, close, isOn, setOn, toggle, renderChips, catalog, editChatInstructions, captureScreen, pasteClipboard };
})();
