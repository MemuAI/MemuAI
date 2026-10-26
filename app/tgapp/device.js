

(function () {
  "use strict";
  const root = document.documentElement;
  const listeners = [];
  const MIN_LIST = 260, MAX_LIST = 560, DEFAULT_LIST = 360, SPLIT_AT = 720, RAIL_AT = 980, WIDE_AT = 1240;
  const DESKTOP_PLATFORMS = ["tdesktop", "macos", "weba", "webk", "web", "unigram"];

  function launchPlatform() {
    try {
      const fromSdk = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.platform;
      if (fromSdk && fromSdk !== "unknown") return fromSdk;
    } catch (e) {  }
    const match = /[#&?]tgWebAppPlatform=([^&]+)/.exec(location.hash + "&" + location.search);
    if (match) return decodeURIComponent(match[1]);
    try { const saved = JSON.parse(sessionStorage.getItem("memu_tg_launch") || "{}"); if (saved.tgWebAppPlatform) return saved.tgWebAppPlatform; } catch (e) {  }
    return "";
  }
  const mq = (query) => { try { return window.matchMedia(query).matches; } catch (e) { return false; } };

  function measure() {
    const width = Math.round(window.innerWidth || root.clientWidth || 0);
    const height = Math.round(window.innerHeight || root.clientHeight || 0);
    const platform = launchPlatform();
    const coarse = mq("(pointer: coarse)") && !mq("(pointer: fine)") ? true : mq("(any-pointer: coarse)") && !mq("(any-pointer: fine)");
    const hover = mq("(hover: hover)") && mq("(pointer: fine)");
    const touch = "ontouchstart" in window || (navigator.maxTouchPoints || 0) > 0;
    const short = Math.min(width, height), long = Math.max(width, height);
    let device;
    if (platform === "android" || platform === "ios") device = short >= 600 && long >= 900 ? "tablet" : "phone";
    else if (DESKTOP_PLATFORMS.includes(platform)) device = hover || !touch ? "desktop" : (short >= 600 ? "tablet" : "phone");
    else if (hover && !coarse) device = "desktop";
    else if (touch || coarse) device = short >= 600 && long >= 900 ? "tablet" : "phone";
    else device = width >= 900 ? "desktop" : "phone";

    if (device === "desktop" && touch && !hover && short >= 600) device = "tablet";
    const layout = width >= SPLIT_AT && height >= 420 ? "split" : "stack";
    return { width, height, platform: platform || "web", device, layout, wide: layout === "split" && width >= WIDE_AT,

      rail: layout === "split" && (device === "desktop" || width >= RAIL_AT),
      orientation: width >= height ? "landscape" : "portrait", pointer: coarse ? "coarse" : "fine", hover, touch };
  }

  const state = measure();
  function apply(next) {
    root.dataset.device = next.device;
    root.dataset.layout = next.layout;
    root.dataset.wide = next.wide ? "1" : "";
    root.dataset.rail = next.rail ? "1" : "";
    root.dataset.orientation = next.orientation;
    root.dataset.pointer = next.pointer;
    root.dataset.hover = next.hover ? "1" : "";
    root.dataset.platform = next.platform;
  }
  apply(state);
  try {
    const saved = +localStorage.getItem("memu_list_w");
    if (saved >= MIN_LIST && saved <= MAX_LIST) root.style.setProperty("--list-w", saved + "px");
  } catch (e) {  }

  let pending = 0;
  function update() {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(() => {
      const next = measure();
      const changed = ["device", "layout", "wide", "rail", "orientation", "pointer"].some((key) => next[key] !== state[key]);
      Object.assign(state, next);
      if (!changed) return;
      apply(next);
      listeners.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } });
    });
  }
  window.addEventListener("resize", update, { passive: true });
  window.addEventListener("orientationchange", () => setTimeout(update, 120));

  function syncViewport() {
    const vv = window.visualViewport;
    const full = Math.round(window.innerHeight || root.clientHeight || 0);
    const visible = Math.round(vv ? vv.height : full);
    root.style.setProperty("--app-h", visible + "px");
    root.style.setProperty("--app-top", Math.max(0, Math.round(vv ? vv.offsetTop : 0)) + "px");
    let keyboard = full - visible > 120;
    try {
      const app = window.Telegram && window.Telegram.WebApp;
      if (app && app.viewportStableHeight && app.viewportHeight && app.viewportStableHeight - app.viewportHeight > 120) keyboard = true;
    } catch (e) {  }
    const typing = document.activeElement && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
    root.dataset.kb = keyboard && typing && state.device !== "desktop" ? "1" : "";
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncViewport, { passive: true });
    window.visualViewport.addEventListener("scroll", syncViewport, { passive: true });
  }
  window.addEventListener("resize", syncViewport, { passive: true });
  document.addEventListener("focusin", () => setTimeout(syncViewport, 60));
  document.addEventListener("focusout", () => setTimeout(syncViewport, 60));
  syncViewport();

  document.addEventListener("wheel", (event) => {
    if (state.device !== "desktop" || event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
    const row = event.target.closest && event.target.closest(".chips, .sources, .variant-row, .tpl-examples, .att-tray");
    if (!row || row.scrollWidth <= row.clientWidth + 1) return;
    const before = row.scrollLeft;
    row.scrollLeft += event.deltaY;
    if (row.scrollLeft !== before) event.preventDefault();
  }, { passive: false });
  try { window.matchMedia("(pointer: fine)").addEventListener("change", update); } catch (e) {  }

  window.MemuDevice = {
    get device() { return state.device; }, get layout() { return state.layout; }, get wide() { return state.wide; },
    get platform() { return state.platform; }, get orientation() { return state.orientation; },
    isSplit: () => state.layout === "split", isDesktop: () => state.device === "desktop",
    state: () => Object.assign({}, state), on: (fn) => listeners.push(fn), update,
  };

  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { S, $, tr, esc, openSheet } = kit;
    listeners.push(() => kit.refreshTops());
    try { if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.onEvent("viewportChanged", () => { update(); syncViewport(); }); } catch (e) {  }

    const AUTO_FULL = ["tdesktop", "macos"];
    const tgApp = () => S.tg || (window.Telegram && window.Telegram.WebApp) || null;
    function canFullscreen() {
      const app = tgApp();
      try { return !!(app && app.requestFullscreen && app.isVersionAtLeast && app.isVersionAtLeast("8.0") && state.device === "desktop"); }
      catch (e) { return false; }
    }
    function setFullscreen(on, remember = true) {
      const app = tgApp();
      if (!canFullscreen()) return;
      if (remember) { S.ui.desk_full = !!on; kit.saveUiPrefs(); }
      try { if (on && !app.isFullscreen) app.requestFullscreen(); else if (!on && app.isFullscreen) app.exitFullscreen(); } catch (e) {  }
    }
    function drawFullButton() {
      const bar = $("#topbar .topbar-inner");
      let button = $("#top-full");
      if (!canFullscreen()) { if (button) button.remove(); return; }
      if (!button && bar) {
        button = document.createElement("button");
        button.id = "top-full"; button.className = "icon-btn muted";
        bar.appendChild(button);
        button.onclick = () => setFullscreen(!(tgApp() && tgApp().isFullscreen));
      }
      if (!button) return;
      const full = !!(tgApp() && tgApp().isFullscreen);
      const label = full ? tr("Свернуть в окно", "Exit full screen") : tr("Развернуть на весь экран", "Full screen");
      button.innerHTML = full
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>';
      button.title = label; button.setAttribute("aria-label", label);
    }
    kit.fullscreen = { can: canFullscreen, set: setFullscreen };
    kit.hooks.lookRows.push({
      html: (SET) => canFullscreen() ? `<div class="section-title">${esc(tr("На компьютере", "On a computer"))}</div><div class="group flat">
        <label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Открывать на весь экран", "Open in full screen"))}</div>
        <div class="cell-sub">${esc(tr("Telegram Desktop открывает приложение маленьким окном — на весь экран помещаются список и чат рядом.", "Telegram Desktop opens the app in a small window — in full screen the list and the chat fit side by side."))}</div></div>
        <span class="switch"><input type="checkbox" id="desk-full" ${S.ui.desk_full !== false ? "checked" : ""}><span></span></span></label></div>` : "",
      bind: (view) => { const box = $("#desk-full", view); if (box) box.onchange = () => setFullscreen(box.checked); },
    });

    function mountResizer() {
      const app = $("#app");
      if (!app || $("#pane-resizer")) return;
      const handle = document.createElement("div");
      handle.id = "pane-resizer"; handle.className = "pane-resizer";
      handle.setAttribute("role", "separator"); handle.setAttribute("aria-orientation", "vertical");
      handle.title = tr("Перетащите, чтобы изменить ширину списка · двойной клик — по умолчанию", "Drag to resize the list · double-click to reset");
      app.appendChild(handle);
      let startX = 0, startW = 0;
      const move = (event) => {
        const x = event.touches ? event.touches[0].clientX : event.clientX;
        const width = Math.max(MIN_LIST, Math.min(MAX_LIST, startW + x - startX));
        root.style.setProperty("--list-w", width + "px");
      };
      const stop = () => {
        document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", stop);
        root.classList.remove("resizing");
        const width = parseInt(getComputedStyle(root).getPropertyValue("--list-w"), 10);
        try { if (width) localStorage.setItem("memu_list_w", String(width)); } catch (e) {  }
      };
      handle.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        startX = event.clientX; startW = parseInt(getComputedStyle(root).getPropertyValue("--list-w"), 10) || DEFAULT_LIST;
        root.classList.add("resizing");
        document.addEventListener("pointermove", move); document.addEventListener("pointerup", stop);
      });
      handle.addEventListener("dblclick", () => { root.style.removeProperty("--list-w"); try { localStorage.removeItem("memu_list_w"); } catch (e) {  } });
      handle.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        const width = (parseInt(getComputedStyle(root).getPropertyValue("--list-w"), 10) || DEFAULT_LIST) + (event.key === "ArrowLeft" ? -20 : 20);
        root.style.setProperty("--list-w", Math.max(MIN_LIST, Math.min(MAX_LIST, width)) + "px");
      });
      handle.tabIndex = 0;
    }

    const SHORTCUTS = [
      ["Alt+N", "Новый чат", "New chat"],
      ["Ctrl+K", "Поиск (чаты, сессии)", "Search (chats, sessions)"],
      ["Alt+1…6", "Вкладки", "Tabs"],
      ["Esc", "Назад / закрыть", "Back / close"],
      ["Enter", "Отправить (на телефоне — новая строка)", "Send (a new line on a phone)"],
      ["Shift+Enter", "Новая строка", "New line"],
      ["/", "Команды MemuCode в поле ввода", "MemuCode commands in the input"],
      ["Tab · ↑ ↓", "Подсказки команд и их аргументов", "Command and argument suggestions"],
      ["?", "Эта подсказка", "This help"],
    ];
    function showShortcuts() {
      openSheet({ title: tr("Сочетания клавиш", "Keyboard shortcuts"),
        body: `<div class="group flat">${SHORTCUTS.map(([keys, ru, en]) => `<div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr(ru, en))}</div></div><div class="cell-value"><kbd>${esc(keys)}</kbd></div></div>`).join("")}</div>` });
    }
    kit.showShortcuts = showShortcuts;
    const typing = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    document.addEventListener("keydown", (event) => {
      if (!$("#app") || $("#app").hidden) return;
      const target = event.target;
      if (event.key === "Escape" && !event.defaultPrevented) {
        if (document.querySelector(".slash-menu:not([hidden])")) return;

        if (typing(target) && target.value && !document.querySelector(".sheet.open")) return;
        const sheets = document.querySelectorAll(".sheet.open");
        if (sheets.length || S.stack.length) { event.preventDefault(); kit.goBack(); }
        return;
      }
      const mod = event.ctrlKey || event.metaKey;
      if (mod && !event.shiftKey && !event.altKey && (event.key === "k" || event.key === "K" || event.code === "KeyK")) {
        event.preventDefault();

        const own = { code: "#code-search" }[S.tab];
        if (!own && S.tab !== "chats") kit.switchTab("chats");
        setTimeout(() => { const input = $(own || "#chat-search") || $("#chat-search"); if (input) { input.focus(); input.select(); } }, 60);
        return;
      }
      if (event.altKey && !mod && (event.code === "KeyN")) { event.preventDefault(); kit.openChat(null); return; }
      if (event.altKey && !mod && /^Digit[1-6]$/.test(event.code)) {
        const tabs = Array.from(document.querySelectorAll("#tabbar .tab"));
        const tab = tabs[+event.code.slice(5) - 1];
        if (tab) { event.preventDefault(); tab.click(); }
        return;
      }
      if ((window.MemuKeys ? MemuKeys.latin(event) : event.key) === "?" && !typing(target) && !mod) { event.preventDefault(); showShortcuts(); }
    });

    kit.hooks.afterBoot.push(() => {
      mountResizer();
      kit.refreshTops();
      drawFullButton();
      try { const app = tgApp(); if (app && app.onEvent) app.onEvent("fullscreenChanged", () => { drawFullButton(); update(); syncViewport(); }); } catch (e) {  }

      if (AUTO_FULL.includes(state.platform) && S.ui.desk_full !== false && state.layout === "stack" && canFullscreen()) setFullscreen(true, false);
    });
    kit.deviceLabel = () => {
      const names = { phone: tr("Телефон", "Phone"), tablet: tr("Планшет", "Tablet"), desktop: tr("Компьютер", "Computer") };
      return `${names[state.device] || state.device} · ${state.layout === "split" ? tr("две панели", "two panes") : tr("одна панель", "one pane")} · ${state.platform}`;
    };
  });
})();
