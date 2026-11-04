// MemuAI help: the «?» menu in the top bar and one-time tips for complex
// features. The first-run introduction lives in onboarding.js.
(function () {
  "use strict";
  if (window.MemuHelp) return;
  const KEY = "memu_tips_v1";

  // B-ONB-M4: localStorage throws when storage is blocked (private mode,
  // disabled cookies) — a top-level read here would kill the whole module.
  const tipsForced = () => { try { return localStorage.getItem("memu_tips_force") === "1"; } catch (_) { return false; } };
  const langStored = () => { try { return localStorage.getItem("memu_lang"); } catch (_) { return null; } };
  const AUTO = (!navigator.webdriver || tipsForced()) && !document.documentElement.dataset.moduleApp;
  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : langStored()) === "en";
  const L = (ru, enText) => (en() ? enText : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (s, root = document) => root.querySelector(s);
  const SVG = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    help: SVG('<circle cx="12" cy="12" r="9"/><path d="M9.6 9.2a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.9"/><path d="M12 17.2h.01"/>'),
    start: SVG('<path d="M12 3.5 13.9 9l5.6.2-4.4 3.5 1.6 5.4L12 15l-4.7 3.1 1.6-5.4L4.5 9.2l5.6-.2z"/>'),
    route: SVG('<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/>'),
    slash: SVG('<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m14 8-4 8"/>'),
    keys: SVG('<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>'),
    book: SVG('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>'),
    bulb: SVG('<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>'),
    redo: SVG('<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>'),
    close: SVG('<path d="M6 6l12 12M18 6 6 18"/>'),
  };

  function load() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || "null"); if (v && v.v === 1) return { seen: {}, ...v, dot: false }; } catch (_) {}
    return null;
  }
  let st = load();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (_) {} };
  function init() {
    if (st) return;
    const chats = typeof S !== "undefined" && Array.isArray(S.chats) ? S.chats.length : 0;
    st = { v: 1, seen: {}, off: chats >= 3, dot: false };
    save();
  }

  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && +cs.opacity > 0.05;
  };
  const panelOpen = () => !!$("#panel-back.visible");
  // The first-run wizard and the setup conversation come first: tips wait.
  const introActive = () => !!$(".onb-back") || (!!(window.MemuOnboarding && MemuOnboarding.active && MemuOnboarding.active()) && !!$("#messages .msg-setup"));

  function placeBubble(bubble, rect, pref = "bottom") {
    const pad = 10, gap = 12, bw = bubble.offsetWidth, bh = bubble.offsetHeight, vw = innerWidth, vh = innerHeight;
    const fits = {
      bottom: rect.bottom + gap + bh <= vh - pad, top: rect.top - gap - bh >= pad,
      right: rect.right + gap + bw <= vw - pad, left: rect.left - gap - bw >= pad,
    };
    const side = [pref, "bottom", "top", "right", "left"].find((s) => fits[s]) || "center";
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
    const ax = Math.max(16, Math.min(rect.left + rect.width / 2 - x, bw - 16));
    const ay = Math.max(16, Math.min(rect.top + rect.height / 2 - y, bh - 16));
    bubble.style.setProperty("--hint-ax", Math.round(ax) + "px");
    bubble.style.setProperty("--hint-ay", Math.round(ay) + "px");
  }

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
    if (!force && (!AUTO || st.off || st.seen[id] || hintEl || introActive() || Date.now() - lastHint < 25000)) return false;
    if (!force && !h.when()) return false;

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

  let checkTimer = 0;
  function scheduleCheck() {
    if (checkTimer) return;
    checkTimer = setTimeout(() => {
      checkTimer = 0;
      if (hintEl) { placeHint(); return; }
      if (!st || st.off || !AUTO || introActive()) return;
      for (const id of Object.keys(HINTS)) if (!st.seen[id] && HINTS[id].when()) { if (hint(id)) break; }
    }, 700);
  }

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
    if (!anchor) return;
    const m = document.createElement("div");
    m.className = "memu-help-menu";
    m.setAttribute("role", "menu");
    const item = (act, ico, label, sub = "") => `<button type="button" role="menuitem" data-help="${act}"><span class="ui-icon">${ico}</span><span class="hm-text"><span>${esc(label)}</span>${sub ? `<small>${esc(sub)}</small>` : ""}</span></button>`;
    m.innerHTML = `<div class="hm-title">${esc(L("Справка", "Help"))}</div>`
      + (window.MemuOnboarding ? item("onboarding", ICON.start, L("Знакомство с MemuAI", "Getting started"), L("Модель, вопросы и главные настройки", "Model, questions and the main settings")) : "")
      + item("commands", ICON.slash, L("Быстрые команды", "Quick commands"), L("Введите / в поле сообщения", "Type / in the message box"))
      + item("keys", ICON.keys, L("Горячие клавиши", "Keyboard shortcuts"), "Ctrl+/")
      + item("team", ICON.route, L("Команда", "Team"), L("Роли моделей и общий результат", "Model roles and a shared result"))
      + item("docs", ICON.book, L("Документация", "Documentation"), L("Подробно о каждой функции", "Every feature in detail"))
      + `<div class="hm-sep"></div>`
      + `<label class="hm-switch"><span class="ui-icon">${ICON.bulb}</span><span class="hm-text"><span>${esc(L("Подсказки к сложным разделам", "Tips for complex features"))}</span><small>${esc(L("Один раз, при первом открытии", "Once, on first visit"))}</small></span>
          <span class="switch"><input type="checkbox" role="switch" data-help="toggle" ${st.off ? "" : "checked"}><span class="sl"></span></span></label>`
      + item("reset", ICON.redo, L("Показать подсказки снова", "Show tips again"));
    document.body.appendChild(m);
    menuEl = m;
    anchor.setAttribute("aria-expanded", "true");
    st.dot = false; save(); refreshButton();
    const r = anchor.getBoundingClientRect();
    if (window.MemuLayers && MemuLayers.place) MemuLayers.place(m, anchor, { gap: 6, maxHeight: innerHeight - 24, align: "end" });
    else m.style.top = Math.round(r.bottom + 6) + "px";
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
  function startIntro() {
    closeHint();
    if (window.MemuOnboarding && MemuOnboarding.restart) MemuOnboarding.restart();
  }
  function run(act) {
    closeMenu();
    if (act === "onboarding") startIntro();
    else if (act === "commands") {
      try { typeof closePanel === "function" && closePanel(); } catch (_) {}
      const input = $("#input");
      if (input) { input.value = "/"; input.focus(); try { typeof renderSlashMenu === "function" && renderSlashMenu(); } catch (_) {} }
    } else if (act === "keys") {
      try { if (typeof showShortcutsModal === "function") showShortcutsModal(); } catch (_) {}
    } else if (act === "team") {
      window.MemuTeams?.open();
    } else if (act === "docs") {
      try { Promise.resolve(showSettings(true)).then(() => openSettingsPage("docs")).catch(() => {}); } catch (_) {}
    } else if (act === "reset") {
      reset();
      try { window.toast && toast(L("Подсказки покажутся снова при открытии разделов", "Tips will show again when you open those features"), "ok"); } catch (_) {}
    }
  }

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
    const btn = (id, ru, enText) => `<button type="button" class="pbtn ghost sx-btn-sm" id="${id}">${L2(ru, enText)}</button>`;
    const intro = window.MemuOnboarding
      ? K.row({ label: L2("Знакомство с MemuAI", "Getting started"), desc: L2("Выбор модели, пара вопросов о ваших задачах и главные настройки — кратко или по разделам. Также — в меню «?» сверху", "Choosing a model, a few questions about your tasks and the main settings — briefly or section by section. Also in the «?» menu at the top"),
        control: btn("st-intro-start", "Пройти заново", "Start again") })
      : "";
    return K.section({ id: "settings-tips", title: L2("Подсказки и обучение", "Tips and learning"), keywords: "tips help onboarding getting started подсказки обучение справка помощь знакомство",
      body: K.row({ label: L2("Подсказки к сложным разделам", "Tips for complex features"),
        desc: L2("Короткое пояснение при первом открытии Work, Планирования, Памяти, Do For You, Модулей, Быстрого доступа и MemuNote", "A short note the first time you open Work, Planning, Memory, Do For You, Modules, Quick Ask and MemuNote"),
        control: K.toggle ? K.toggle("st-tips", !st.off) : `<label class="switch"><input type="checkbox" role="switch" id="st-tips" ${st.off ? "" : "checked"}><span class="sl"></span></label>` })
        + K.row({ label: L2("Показать подсказки снова", "Show tips again"), desc: L2("Все подсказки станут непрочитанными", "Every tip becomes unseen again"),
          control: btn("st-tips-reset", "Сбросить", "Reset") })
        + intro });
  }
  function bindSettings() {
    const box = $("#st-tips");
    if (box) box.onchange = () => setEnabled(box.checked);
    const r = $("#st-tips-reset");
    if (r) r.onclick = () => { reset(); try { window.toast && toast(L("Подсказки покажутся снова", "Tips will show again"), "ok"); } catch (_) {} };
    const t = $("#st-intro-start");
    if (t) t.onclick = () => startIntro();
  }

  function boot() {
    mountButton();
    const go = () => {
      init(); refreshButton();
      new MutationObserver(scheduleCheck).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class"] });
      new MutationObserver(refreshButton).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
      addEventListener("resize", () => { if (hintEl) placeHint(); });
      document.addEventListener("scroll", () => { if (hintEl) placeHint(); }, true);
    };
    if (document.body.classList.contains("boot-ready")) go();
    else new MutationObserver((_, obs) => { if (document.body.classList.contains("boot-ready")) { obs.disconnect(); go(); } })
      .observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();

  window.MemuHelp = Object.freeze({ hint, menu, reset, setEnabled, settingsHtml, bindSettings, start: startIntro,
    state: () => JSON.parse(JSON.stringify(st || load() || {})), HINTS: Object.freeze(Object.keys(HINTS)) });
})();
