/* MemuAI — Round 31 (design-main): a quiet first-run tour, one-time hints for complex features and the «?» help.

   window.MemuTour:
     start()            — the interface tour (spotlight + bubble, ← → Enter Esc; skips steps whose target is hidden).
     welcome(force)     — the small first-run card (bottom right, not modal): «Show me» / «Later».
     hint(id, force)    — show one contextual hint now (ids: HINTS below); shown once unless force.
     menu(anchor)       — the «?» popover (tour, commands, shortcuts, docs, tips on/off, show tips again).
     reset()            — every hint becomes unseen again and hints turn on («Show tips again»).
     setEnabled(bool)   — contextual hints on/off (the tour and «?» always work).
     state()            — a copy of the stored state; settingsHtml({K, L2}) / bindSettings() — Settings → General.

   State: localStorage «memu_tips_v1» = { v: 1, welcome: "" | "done", seen: {id: 1}, off: bool, dot: bool }.
   Rules (the user dislikes pushy UI):
     • nothing ever opens on top of a dialog, a menu, the tour or another hint; at most one hint per 25 s;
     • an existing user (3+ chats when tour.js first runs) gets no welcome and hints start OFF — only a small dot on «?»;
     • automatic welcome/hints are off in automated browsers (navigator.webdriver: tests, screenshot harnesses) unless
       localStorage «memu_tips_force» = "1" — call MemuTour.welcome(true) / hint(id, true) there instead. */
(function () {
  "use strict";
  if (window.MemuTour) return;
  const KEY = "memu_tips_v1";
  // Round 33: never inside a module's own window (/?module_app=…, module-apps.js) — the app UI is hidden there.
  const AUTO = (!navigator.webdriver || localStorage.getItem("memu_tips_force") === "1") && !document.documentElement.dataset.moduleApp;
  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : localStorage.getItem("memu_lang")) === "en";
  const L = (ru, enText) => (en() ? enText : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (s, root = document) => root.querySelector(s);
  const SVG = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    help: SVG('<circle cx="12" cy="12" r="9"/><path d="M9.6 9.2a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.9"/><path d="M12 17.2h.01"/>'),
    route: SVG('<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/>'),
    slash: SVG('<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m14 8-4 8"/>'),
    keys: SVG('<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>'),
    book: SVG('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>'),
    bulb: SVG('<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>'),
    redo: SVG('<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>'),
    close: SVG('<path d="M6 6l12 12M18 6 6 18"/>'),
  };

  /* ------------------------------------------------------------------ state */
  function load() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || "null"); if (v && v.v === 1) return { seen: {}, ...v }; } catch (_) {}
    return null;
  }
  let st = load();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (_) {} };
  function init() {
    if (st) return;
    const chats = typeof S !== "undefined" && Array.isArray(S.chats) ? S.chats.length : 0;
    const veteran = chats >= 3;
    st = { v: 1, welcome: veteran ? "done" : "", seen: {}, off: veteran, dot: veteran };
    save();
  }

  /* ------------------------------------------------------------------ helpers */
  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && +cs.opacity > 0.05;
  };
  const panelOpen = () => !!$("#panel-back.visible");
  const somethingOpen = () => panelOpen() || !!(window.MemuLayers && MemuLayers.isOpen && MemuLayers.isOpen())
    || !!$(".memu-tour-spot, .memu-hint, .msearch-back.visible, #msx-root:not([hidden]), .live-call.visible, body.booting");
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches || /off|reduced/.test(document.documentElement.dataset.motion || "");

  /** Fixed bubble next to a rect (below, above, right, left — the first that fits), clamped to the window. */
  function placeBubble(bubble, rect, pref = "bottom") {
    const pad = 10, gap = 12, bw = bubble.offsetWidth, bh = bubble.offsetHeight, vw = innerWidth, vh = innerHeight;
    const fits = {
      bottom: rect.bottom + gap + bh <= vh - pad, top: rect.top - gap - bh >= pad,
      right: rect.right + gap + bw <= vw - pad, left: rect.left - gap - bw >= pad,
    };
    const order = [pref, "bottom", "top", "right", "left"];
    const side = order.find((s) => fits[s]) || "center";
    let x, y;
    if (side === "bottom" || side === "top") {
      x = rect.left + rect.width / 2 - bw / 2;
      y = side === "bottom" ? rect.bottom + gap : rect.top - gap - bh;
    } else if (side === "right" || side === "left") {
      x = side === "right" ? rect.right + gap : rect.left - gap - bw;
      y = rect.top + rect.height / 2 - bh / 2;
    } else { x = (vw - bw) / 2; y = (vh - bh) / 2; }
    x = Math.max(pad, Math.min(x, vw - bw - pad));
    y = Math.max(pad, Math.min(y, vh - bh - pad));
    bubble.style.left = Math.round(x) + "px";
    bubble.style.top = Math.round(y) + "px";
    bubble.dataset.side = side;
    // the little arrow points at the target's centre
    const ax = Math.max(16, Math.min(rect.left + rect.width / 2 - x, bw - 16));
    const ay = Math.max(16, Math.min(rect.top + rect.height / 2 - y, bh - 16));
    bubble.style.setProperty("--tour-ax", Math.round(ax) + "px");
    bubble.style.setProperty("--tour-ay", Math.round(ay) + "px");
  }

  /* ------------------------------------------------------------------ the tour */
  function steps() {
    return [
      { sel: "#btn-new", title: L("Новый чат", "New chat"),
        text: L("Каждый разговор — отдельный чат в списке слева. Кнопка рядом открывает временный чат: он не сохраняется и не попадает в память.",
          "Every conversation is its own chat in the list on the left. The button next to it opens a temporary chat: it isn't saved and doesn't reach memory.") },
      { sel: ".mode-switch", title: L("Три режима", "Three modes"),
        text: L("<b>Чат</b> — просто разговор. <b>Work</b> — ИИ читает и меняет файлы выбранной папки (с вашего разрешения). <b>MemuCode</b> — агент-программист для репозитория.",
          "<b>Chat</b> is a plain conversation. <b>Work</b> lets the AI read and change files in a folder you choose (with your approval). <b>MemuCode</b> is a coding agent for a repository.") },
      { sel: "#model-dd-btn", title: L("Модель", "Model"),
        text: L("Какая нейросеть отвечает. Список берётся из MemuAI Proxy — там добавляются аккаунты и ключи. Быстро открыть: Ctrl+M.",
          "Which AI model answers. The list comes from MemuAI Proxy, where accounts and keys are added. Shortcut: Ctrl+M.") },
      { sel: "#btn-plus", title: L("Файлы и инструменты", "Files and tools"),
        text: L("Фото и документы, снимок экрана, веб-поиск, глубокое исследование, картинки, Do For You. А если ввести <b>/</b> в поле — появятся быстрые команды.",
          "Photos and documents, screenshots, web search, deep research, images, Do For You. Type <b>/</b> in the message box for quick commands.") },
      { sel: "#nav-sched", title: L("Планирование", "Planning"),
        text: L("Задачи, которые MemuAI выполняет сам: по расписанию, при запуске программы или по условию. Можно описать словами.",
          "Tasks MemuAI runs on its own: on a schedule, when a program starts or when a condition is met. You can describe them in plain words.") },
      { sel: ".chat-search, #chat-search", title: L("Поиск везде", "Search everything"),
        text: L("Ctrl+K — чаты, память, файлы, настройки и команды в одном окне.", "Ctrl+K — chats, memory, files, settings and commands in one place.") },
      { sel: "#profile-card", title: L("Профиль и настройки", "Profile and settings"),
        text: L("Память, внешний вид, голос, интеграции, модули и документация. Здесь же включается <b>Быстрый доступ</b>: окно ИИ по Alt+Space поверх любых программ.",
          "Memory, appearance, voice, integrations, modules and documentation. This is also where you turn on <b>Quick Ask</b>: an AI window over any program on Alt+Space.") },
      { sel: "#btn-help", title: L("Справка всегда рядом", "Help is always here"),
        text: L("Здесь можно снова пройти тур, открыть документацию и горячие клавиши, включить или выключить подсказки.",
          "Take the tour again, open the documentation and shortcuts, or turn tips on and off.") },
    ];
  }

  let tour = null;
  function start() {
    init();
    endTour(); closeWelcome(); closeHint(); closeMenu();
    try { typeof closePanel === "function" && closePanel(); } catch (_) {}
    try { window.MemuLayers && MemuLayers.closeAll && MemuLayers.closeAll("api"); } catch (_) {}
    // the sidebar must be on screen for the first steps (narrow windows hide it)
    try { if (document.body.classList.contains("sidebar-hidden") && typeof toggleSidebar === "function") toggleSidebar(); } catch (_) {}
    const list = steps();
    const spot = document.createElement("div");
    spot.className = "memu-tour-spot";
    spot.setAttribute("aria-hidden", "true");
    const bubble = document.createElement("div");
    bubble.className = "memu-tour-bubble";
    bubble.setAttribute("role", "dialog");
    bubble.setAttribute("aria-modal", "false");
    bubble.setAttribute("aria-live", "polite");
    const block = document.createElement("div");
    block.className = "memu-tour-block";   // swallows clicks outside the bubble (the tour never triggers the app)
    block.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); });
    document.body.append(block, spot, bubble);
    tour = { list, i: -1, spot, bubble, block, prevFocus: document.activeElement };
    document.addEventListener("keydown", tourKeys, true);
    addEventListener("resize", tourPlace);
    st.welcome = "done"; st.dot = false; save(); refreshButton();
    go(1);
  }
  function go(dir) {
    if (!tour) return;
    let i = tour.i;
    do { i += dir; } while (i >= 0 && i < tour.list.length && !visible($(tour.list[i].sel)));
    if (i < 0) return;   // nothing before
    if (i >= tour.list.length) { endTour(); return; }
    tour.i = i;
    render();
  }
  function render() {
    const { list, i, bubble } = tour;
    const step = list[i];
    const shown = list.filter((s) => visible($(s.sel)));
    const n = shown.indexOf(step) + 1, total = shown.length;
    const last = n === total;
    bubble.innerHTML = `<div class="tour-head"><span class="tour-count">${esc(L(`${n} из ${total}`, `${n} of ${total}`))}</span>
        <button type="button" class="tour-x" data-tour="end" aria-label="${esc(L("Закрыть тур", "Close the tour"))}">${ICON.close}</button></div>
      <h3 class="tour-title">${esc(step.title)}</h3><p class="tour-text">${step.text}</p>
      <div class="tour-foot"><button type="button" class="tour-skip" data-tour="end">${esc(last ? "" : L("Пропустить", "Skip"))}</button>
        <span class="tour-grow"></span>
        ${n > 1 ? `<button type="button" class="pbtn ghost tour-btn" data-tour="prev">${esc(L("Назад", "Back"))}</button>` : ""}
        <button type="button" class="pbtn tour-btn" data-tour="next">${esc(last ? L("Готово", "Done") : L("Далее", "Next"))}</button></div>`;
    if (last) bubble.querySelector(".tour-skip").hidden = true;
    bubble.querySelectorAll("[data-tour]").forEach((b) => b.onclick = () => {
      const a = b.dataset.tour; if (a === "next") go(1); else if (a === "prev") go(-1); else endTour();
    });
    tourPlace();
    bubble.querySelector('[data-tour="next"]').focus({ preventScroll: true });
  }
  function tourPlace() {
    if (!tour || tour.i < 0) return;
    const el = $(tour.list[tour.i].sel);
    if (!visible(el)) { go(1); return; }
    const r = el.getBoundingClientRect(), p = 6;
    Object.assign(tour.spot.style, { left: r.left - p + "px", top: r.top - p + "px", width: r.width + p * 2 + "px", height: r.height + p * 2 + "px" });
    placeBubble(tour.bubble, { left: r.left - p, top: r.top - p, right: r.right + p, bottom: r.bottom + p, width: r.width + p * 2, height: r.height + p * 2 }, "bottom");
  }
  function tourKeys(e) {
    if (!tour) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); endTour(); }
    else if (e.key === "ArrowRight" || e.key === "Enter" && !/^(BUTTON)$/.test(document.activeElement?.tagName)) { e.preventDefault(); e.stopPropagation(); go(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); go(-1); }
    else if (e.key === "Tab") {   // focus stays inside the bubble
      const items = [...tour.bubble.querySelectorAll("button:not([hidden])")];
      if (!items.length) return;
      const k = items.indexOf(document.activeElement);
      e.preventDefault(); e.stopPropagation();
      items[(k + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus();
    }
  }
  function endTour() {
    if (!tour) return;
    const t = tour; tour = null;
    document.removeEventListener("keydown", tourKeys, true);
    removeEventListener("resize", tourPlace);
    const gone = () => { t.spot.remove(); t.bubble.remove(); t.block.remove(); };
    if (reduced()) gone(); else { t.spot.classList.add("leaving"); t.bubble.classList.add("leaving"); setTimeout(gone, 180); }
    t.block.remove();
    const back = $("#btn-help");
    try { (back && visible(back) ? back : t.prevFocus)?.focus?.({ preventScroll: true }); } catch (_) {}
  }

  /* ------------------------------------------------------------------ welcome card */
  let welcomeEl = null;
  function welcome(force) {
    init();
    if (!force && (st.welcome === "done" || !AUTO)) return false;
    if (!force && somethingOpen()) return false;
    closeWelcome();
    const card = document.createElement("div");
    card.className = "memu-welcome";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", L("Знакомство с MemuAI", "Getting started with MemuAI"));
    card.innerHTML = `<button type="button" class="mw-x" data-w="later" aria-label="${esc(L("Закрыть", "Close"))}">${ICON.close}</button>
      <div class="mw-mark" aria-hidden="true">${ICON.route}</div>
      <h3>${esc(L("Показать, что где?", "Want a quick look around?"))}</h3>
      <p>${esc(L("Минута: чаты, модели, инструменты и настройки. Тур всегда можно открыть снова в меню «?» сверху.",
        "One minute: chats, models, tools and settings. You can always reopen the tour from the «?» menu at the top."))}</p>
      <div class="mw-actions"><button type="button" class="pbtn ghost" data-w="later">${esc(L("Не сейчас", "Not now"))}</button>
        <button type="button" class="pbtn" data-w="go">${esc(L("Показать", "Show me"))}</button></div>`;
    card.querySelectorAll("[data-w]").forEach((b) => b.onclick = () => {
      st.welcome = "done"; save(); closeWelcome();
      if (b.dataset.w === "go") start();
      else if (window.toast) { try { toast(L("Тур — в меню «?» вверху справа", "The tour is in the «?» menu, top right"), "info"); } catch (_) {} }
    });
    document.body.appendChild(card);
    welcomeEl = card;
    return true;
  }
  function closeWelcome() { if (welcomeEl) { welcomeEl.remove(); welcomeEl = null; } }

  /* ------------------------------------------------------------------ contextual hints */
  // when(): the feature is on screen now; anchor: where the bubble points (inside what when() found).
  const HINTS = {
    work: { when: () => typeof S !== "undefined" && S.mode === "work" && !panelOpen() && visible($("#workbar")), anchor: "#work-access, #workbar",
      title: () => L("Как работает Work", "How Work works"),
      text: () => L("ИИ меняет файлы только внутри выбранного проекта. «С подтверждением» — каждое изменение ждёт вашего «да»; «План» — сначала план; «Цель» — работать до результата.",
        "The AI changes files only inside the chosen project. “Ask first” — every change waits for your OK; “Plan” — a plan first; “Goal” — keep going until it's done.") },
    plan: { when: () => visible($("#pln-root")), anchor: "#pln-root [data-act='new'], #pln-root",
      title: () => L("Задачи без вашего участия", "Tasks that run by themselves"),
      text: () => L("Проще всего — описать задачу словами («по будням в 9 присылай сводку»): MemuAI сам соберёт правило, вы проверите. Задачи работают и при закрытом окне, пока запущен Proxy.",
        "The easiest way is to describe it in words (“every weekday at 9 send me a digest”): MemuAI builds the rule and you check it. Tasks run even with the window closed while Proxy is running.") },
    memory: { when: () => visible($(".mem2-head")), anchor: ".mem2-head h2, .mem2-head",
      title: () => L("Что ИИ помнит о вас", "What the AI remembers"),
      text: () => L("Факты из разговоров. Закрепите важное, удалите лишнее. Во «Входящих» — то, что ИИ предлагает запомнить: оно не используется, пока вы не одобрите.",
        "Facts from your chats. Pin what matters, delete the rest. “Inbox” holds what the AI suggests remembering — it isn't used until you approve it.") },
    dfy: { when: () => visible($("#pane-dfy .sx-section")), anchor: "#pane-dfy .sx-page-head, #pane-dfy .sx-section",
      title: () => L("Do For You", "Do For You"),
      text: () => L("ИИ сам делает дело в своём браузере: бронирует, заполняет формы, ищет. Запуск — /dfy в чате или пункт в «+». Перед оплатой и отправкой он спросит вас.",
        "The AI does the job in its own browser: books, fills in forms, looks things up. Start it with /dfy in a chat or from the «+» menu. It asks you before paying or sending.") },
    modules: { when: () => visible($("#pane-modules .sx-section, #pane-modules .mod-list, #pane-modules > *:not(.sx-page-head)")), anchor: "#pane-modules .sx-page-head, #pane-modules",
      title: () => L("Модули", "Modules"),
      text: () => L("Дополнительные приложения внутри MemuAI: Персонажи, Студия, Дизайн и другие. Включённый модуль появляется в боковой панели; выключенный не тратит ресурсы.",
        "Extra apps inside MemuAI: Characters, Studio, Design and more. An enabled module appears in the sidebar; a disabled one costs nothing.") },
    quick: { when: () => visible($("#pane-quick .sx-section")), anchor: "#pane-quick .sx-page-head, #pane-quick .sx-section",
      title: () => L("Быстрый доступ", "Quick Ask"),
      text: () => L("Нажмите Alt+Space в любой программе — появится маленькое окно ИИ: спросили, получили ответ, вернулись к работе. Сочетание меняется ниже.",
        "Press Alt+Space in any program for a small AI window: ask, get the answer, get back to work. You can change the shortcut below.") },
    memunote: { when: () => visible($("#pane-memunote .sx-section, #pane-memunote > *:not(.sx-page-head)")), anchor: "#pane-memunote .sx-page-head, #pane-memunote",
      title: () => L("MemuNote", "MemuNote"),
      text: () => L("Отдельный ИИ-блокнот: заметки, которые ИИ помогает писать и править. Здесь — как его открыть и какой моделью он работает.",
        "A separate AI notebook: notes the AI helps you write and edit. Here you choose how to open it and which model it uses.") },
  };
  let hintEl = null, lastHint = 0;
  function hint(id, force) {
    init();
    const h = HINTS[id];
    if (!h) return false;
    if (!force && (!AUTO || st.off || st.seen[id] || tour || hintEl || welcomeEl || Date.now() - lastHint < 25000)) return false;
    if (!force && !h.when()) return false;
    // selectors in order of preference (querySelectorAll would return the ancestor first)
    const anchor = h.anchor.split(",").map((sel) => [...document.querySelectorAll(sel.trim())].find(visible)).find(Boolean);
    if (!anchor) return false;
    closeHint();
    st.seen[id] = 1; save();
    lastHint = Date.now();
    const el = document.createElement("div");
    el.className = "memu-hint";
    el.setAttribute("role", "note");
    el.dataset.hint = id;
    el.innerHTML = `<div class="mh-head"><span class="mh-ico" aria-hidden="true">${ICON.bulb}</span><b>${esc(h.title())}</b>
        <button type="button" class="mh-x" data-h="ok" aria-label="${esc(L("Закрыть подсказку", "Close the tip"))}">${ICON.close}</button></div>
      <p>${esc(h.text())}</p>
      <div class="mh-foot"><button type="button" class="mh-off" data-h="off">${esc(L("Не показывать подсказки", "Don't show tips"))}</button>
        <button type="button" class="pbtn mh-ok" data-h="ok">${esc(L("Понятно", "Got it"))}</button></div>`;
    el.querySelectorAll("[data-h]").forEach((b) => b.onclick = () => {
      if (b.dataset.h === "off") { setEnabled(false); try { window.toast && toast(L("Подсказки выключены. Включить — меню «?» или «Настройки → Общие»", "Tips are off. Turn them back on in the «?» menu or Settings → General"), "info"); } catch (_) {} }
      closeHint();
    });
    document.body.appendChild(el);
    el._anchor = anchor;
    hintEl = el;
    placeHint();
    return true;
  }
  function placeHint() {
    if (!hintEl && !document.querySelector(".memu-hint")) return;
    hintEl = hintEl || document.querySelector(".memu-hint");
    const a = hintEl._anchor;
    if (!a || !visible(a)) { closeHint(); return; }
    placeBubble(hintEl, a.getBoundingClientRect(), "bottom");
  }
  function closeHint() { const el = hintEl || document.querySelector(".memu-hint"); if (el) el.remove(); hintEl = null; }

  // one cheap check after DOM changes settle (throttled), never while something else is up
  let checkTimer = 0;
  function scheduleCheck() {
    if (checkTimer) return;
    checkTimer = setTimeout(() => {
      checkTimer = 0;
      if (hintEl) { placeHint(); return; }
      if (!st || st.off || !AUTO || tour || welcomeEl) return;
      for (const id of Object.keys(HINTS)) if (!st.seen[id] && HINTS[id].when()) { if (hint(id)) break; }
    }, 700);
  }

  /* ------------------------------------------------------------------ the «?» button and menu */
  function refreshButton() {
    const b = $("#btn-help");
    if (!b) return;
    const label = L("Справка и подсказки", "Help and tips");
    b.setAttribute("aria-label", label);
    b.title = label;
    b.classList.toggle("has-dot", !!(st && st.dot));
  }
  function mountButton() {
    if ($("#btn-help")) return;
    const ref = $("#btn-focus") || $("#btn-theme");
    if (!ref || !ref.parentNode) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "iconbtn memu-help-btn";
    b.id = "btn-help";
    b.setAttribute("aria-haspopup", "menu");
    b.setAttribute("aria-expanded", "false");
    b.innerHTML = `<span class="ui-icon">${ICON.help}</span>`;
    b.addEventListener("click", (e) => { e.stopPropagation(); if (menuEl) closeMenu(); else menu(b); });
    ref.parentNode.insertBefore(b, ref);
    refreshButton();
  }

  let menuEl = null, menuLayer = null;
  function menu(anchor) {
    init();
    closeMenu();
    anchor = anchor || $("#btn-help");
    const m = document.createElement("div");
    m.className = "memu-help-menu";
    m.setAttribute("role", "menu");
    const item = (act, ico, label, sub = "", extra = "") => `<button type="button" role="menuitem" data-help="${act}"${extra}><span class="ui-icon">${ico}</span><span class="hm-text"><span>${esc(label)}</span>${sub ? `<small>${esc(sub)}</small>` : ""}</span></button>`;
    m.innerHTML = `<div class="hm-title">${esc(L("Справка", "Help"))}</div>`
      + item("tour", ICON.route, L("Тур по интерфейсу", "Interface tour"), L("Что где — за минуту", "What's where, in a minute"))
      + item("commands", ICON.slash, L("Быстрые команды", "Quick commands"), L("Введите / в поле сообщения", "Type / in the message box"))
      + item("keys", ICON.keys, L("Горячие клавиши", "Keyboard shortcuts"), "Ctrl+/")
      + item("docs", ICON.book, L("Документация", "Documentation"), L("Подробно о каждой функции", "Every feature in detail"))
      + `<div class="hm-sep"></div>`
      + `<label class="hm-switch"><span class="ui-icon">${ICON.bulb}</span><span class="hm-text"><span>${esc(L("Подсказки к сложным разделам", "Tips for complex features"))}</span><small>${esc(L("Один раз, при первом открытии", "Once, on first visit"))}</small></span>
          <span class="switch"><input type="checkbox" role="switch" data-help="toggle" ${st.off ? "" : "checked"}><span class="sl"></span></span></label>`
      + item("reset", ICON.redo, L("Показать подсказки снова", "Show tips again"));
    document.body.appendChild(m);
    menuEl = m;
    anchor.setAttribute("aria-expanded", "true");
    st.dot = false; save(); refreshButton();
    const place = () => {
      const r = anchor.getBoundingClientRect();
      if (window.MemuLayers && MemuLayers.place) { MemuLayers.place(m, anchor, { gap: 6, maxHeight: innerHeight - 24, align: "end" }); return; }
      m.style.top = Math.round(r.bottom + 6) + "px";
      m.style.left = Math.round(Math.max(8, Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 8))) + "px";
    };
    place();
    // right-align under the button whatever place() decided
    const r = anchor.getBoundingClientRect();
    m.style.left = Math.round(Math.max(8, Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 8))) + "px";
    if (window.MemuLayers && MemuLayers.open) {
      menuLayer = MemuLayers.open({ el: m, anchor, kind: "menu", focus: true, items: "[role=menuitem], input", close: () => closeMenu(true) });
    } else {
      setTimeout(() => document.addEventListener("pointerdown", outside, true));
      document.addEventListener("keydown", menuKeys, true);
    }
    m.addEventListener("keydown", (e) => {
      const items = [...m.querySelectorAll("[role=menuitem], input")];
      const k = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(k + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus(); }
      else if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
      else if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); }
    });
    m.querySelectorAll("button[data-help]").forEach((b) => b.onclick = () => run(b.dataset.help));
    m.querySelector('input[data-help="toggle"]').onchange = (e) => setEnabled(e.target.checked);
    setTimeout(() => m.querySelector("[role=menuitem]")?.focus({ preventScroll: true }), 0);
  }
  const outside = (e) => { if (menuEl && !menuEl.contains(e.target) && e.target.closest("#btn-help") === null) closeMenu(); };
  const menuKeys = (e) => { if (e.key === "Escape" && menuEl) { e.preventDefault(); e.stopPropagation(); closeMenu(); $("#btn-help")?.focus(); } };
  function closeMenu(fromLayer) {
    if (!menuEl) return;
    const m = menuEl; menuEl = null;
    if (!fromLayer && menuLayer && window.MemuLayers) { try { MemuLayers.close(m, "api"); } catch (_) {} }
    menuLayer = null;
    m.remove();
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", menuKeys, true);
    $("#btn-help")?.setAttribute("aria-expanded", "false");
  }
  function run(act) {
    closeMenu();
    if (act === "tour") start();
    else if (act === "commands") {
      try { typeof closePanel === "function" && closePanel(); } catch (_) {}
      const input = $("#input");
      if (input) { input.value = "/"; input.focus(); try { typeof renderSlashMenu === "function" && renderSlashMenu(); } catch (_) {} }
    } else if (act === "keys") {
      try { if (typeof showShortcutsModal === "function") showShortcutsModal(); } catch (_) {}
    } else if (act === "docs") {
      try { Promise.resolve(showSettings(true)).then(() => openSettingsPage("docs")).catch(() => {}); } catch (_) {}
    } else if (act === "reset") {
      reset();
      try { window.toast && toast(L("Подсказки покажутся снова при открытии разделов", "Tips will show again when you open those features"), "ok"); } catch (_) {}
    }
  }

  /* ------------------------------------------------------------------ settings + api */
  function setEnabled(on) {
    init();
    st.off = !on; save();
    if (!on) closeHint();
    const box = $("#st-tips"); if (box) box.checked = !!on;
  }
  function reset() {
    init();
    st.seen = {}; st.off = false; lastHint = 0; save();
    const box = $("#st-tips"); if (box) box.checked = true;
  }
  function settingsHtml({ K, L2 }) {
    init();
    return K.section({ id: "settings-tips", title: L2("Подсказки и обучение", "Tips and learning"), keywords: "tips tour help onboarding подсказки тур обучение справка помощь",
      body: K.row({ label: L2("Подсказки к сложным разделам", "Tips for complex features"),
        desc: L2("Короткое пояснение при первом открытии Work, Планирования, Памяти, Do For You, Модулей, Быстрого доступа и MemuNote", "A short note the first time you open Work, Planning, Memory, Do For You, Modules, Quick Ask and MemuNote"),
        control: K.toggle ? K.toggle("st-tips", !st.off) : `<label class="switch"><input type="checkbox" role="switch" id="st-tips" ${st.off ? "" : "checked"}><span class="sl"></span></label>` })
        + K.row({ label: L2("Показать подсказки снова", "Show tips again"), desc: L2("Все подсказки станут непрочитанными", "Every tip becomes unseen again"),
          control: `<button type="button" class="pbtn ghost sx-btn-sm" id="st-tips-reset">${L2("Сбросить", "Reset")}</button>` })
        + K.row({ label: L2("Тур по интерфейсу", "Interface tour"), desc: L2("Минута: где чаты, модели, инструменты и настройки. Также — в меню «?» сверху", "One minute: chats, models, tools and settings. Also in the «?» menu at the top"),
          control: `<button type="button" class="pbtn ghost sx-btn-sm" id="st-tour-start">${L2("Начать", "Start")}</button>` }) });
  }
  function bindSettings() {
    const box = $("#st-tips");
    if (box) box.onchange = () => setEnabled(box.checked);
    const r = $("#st-tips-reset");
    if (r) r.onclick = () => { reset(); try { window.toast && toast(L("Подсказки покажутся снова", "Tips will show again"), "ok"); } catch (_) {} };
    const t = $("#st-tour-start");
    if (t) t.onclick = () => start();
  }

  /* ------------------------------------------------------------------ boot */
  function boot() {
    mountButton();
    const go = () => {
      init(); refreshButton();
      new MutationObserver(scheduleCheck).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class"] });
      new MutationObserver(refreshButton).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
      addEventListener("resize", () => { if (hintEl) placeHint(); });
      document.addEventListener("scroll", () => { if (hintEl) placeHint(); }, true);
      // the welcome waits for the profile step (name) and for the window to be idle
      if (AUTO && st.welcome !== "done") {
        let tries = 0;
        const tryWelcome = () => {
          tries += 1;
          const onboarded = typeof pref === "function" ? pref("profile_onboarded", false) : true;
          if (onboarded && !somethingOpen() && welcome()) return;
          if (st.welcome !== "done" && tries < 40) setTimeout(tryWelcome, 3000);
        };
        setTimeout(tryWelcome, 2500);
      }
    };
    if (document.body.classList.contains("boot-ready")) go();
    else new MutationObserver((_, obs) => { if (document.body.classList.contains("boot-ready")) { obs.disconnect(); go(); } })
      .observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();

  window.MemuTour = Object.freeze({ start, welcome, hint, menu, reset, setEnabled, settingsHtml, bindSettings,
    state: () => JSON.parse(JSON.stringify(st || load() || {})), HINTS: Object.freeze(Object.keys(HINTS)), end: endTour });
})();
