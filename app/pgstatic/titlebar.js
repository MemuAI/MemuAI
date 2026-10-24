/* MemuAI custom title bar (Round 20).
 *
 * The native side (app/win_titlebar.py) removes the Windows caption of the
 * normal sizable window; this file turns the page's own top bar into the
 * title bar:
 *   - window buttons (minimize / maximize-restore / close) drawn in HTML;
 *   - pressing an empty part of the top bar starts the *native* move loop
 *     (Aero Snap, drag-down-to-restore); a double press toggles maximize;
 *   - right click there (or Alt+Space) opens the real system menu;
 *   - a thin strip along the top edge starts the native top/corner resize.
 *
 * Nothing is shown unless Python confirms the hook is installed
 * (window.pywebview.api.titlebar_state().installed), so a browser or dev
 * preview never gets dead buttons. html[data-titlebar="custom"] marks the
 * active state for CSS (titlebar.css).
 */
"use strict";
const MemuTitlebar = (() => {
  const root = document.documentElement;
  const NO_DRAG = [
    "button", "a[href]", "input", "textarea", "select", "option", "label", "summary", "iframe",
    "video", "audio", "canvas", "[role=button]", "[role=menu]", "[role=menuitem]", "[role=listbox]",
    "[role=option]", "[role=slider]", "[role=tab]", "[role=separator]", "[role=dialog]",
    "[contenteditable='']", "[contenteditable='true']", "[draggable='true']", "[data-no-drag]",
    ".dropdown", ".dd-pop", ".app-menu-pop", ".chat-search", ".context-meter", ".sidebar-resizer",
    "#module-slot-topbar > *", ".memu-wc", ".memu-tb-resize",
  ].join(",");
  /** Containers whose *empty* background drags the window when it lies
   *  within the title band (from the window top to the top bar's bottom). */
  const BAND_SURFACES = "body, #app, .main, .side, .side-top, .brand, .create-row";
  const OVERLAY_BAND = 32;

  let state = null;
  let enabled = false;
  let controls = null;
  let edges = null;
  let lastPress = null;
  let measureQueued = false;
  let resizeObserver = null;
  let mutationObserver = null;
  let overlayObserver = null;
  let pendingTries = 0;
  let stateTimer = 0;
  let booted = false;

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const L = (ru, english) => (en() ? english : ru);
  const bridge = () => (window.pywebview && window.pywebview.api) || null;
  const has = (name) => { const api = bridge(); return !!(api && typeof api[name] === "function"); };

  async function call(name, ...args) {
    if (!has(name)) return null;
    try { return await bridge()[name](...args); } catch (_) { return null; }
  }

  /* ------------------------------------------------------------ icons */
  // 10×10 glyphs on a 1 px grid, like Segoe Fluent's caption icons.
  const ICONS = {
    minimize: '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M0 5.5h10"/></svg>',
    maximize: '<svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y=".5" width="9" height="9" rx="1.5"/></svg>',
    restore: '<svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y="2.5" width="7" height="7" rx="1.25"/>' +
      '<path d="M2.5 2.5V2A1.5 1.5 0 0 1 4 .5h4A1.5 1.5 0 0 1 9.5 2v4A1.5 1.5 0 0 1 8 7.5h-.5"/></svg>',
    close: '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M.5.5l9 9M9.5.5l-9 9"/></svg>',
  };

  function labels() {
    const max = state && state.maximized;
    return {
      group: L("Управление окном", "Window controls"),
      minimize: L("Свернуть", "Minimize"),
      maximize: max ? L("Восстановить", "Restore") : L("Развернуть", "Maximize"),
      close: L("Закрыть", "Close"),
    };
  }

  function build() {
    if (controls) return;
    controls = document.createElement("div");
    controls.id = "memu-wincontrols";
    controls.className = "memu-wc";
    controls.setAttribute("role", "group");
    controls.innerHTML = ["minimize", "maximize", "close"].map((kind) =>
      `<button type="button" class="memu-wc-btn memu-wc-${kind}" data-wc="${kind}" tabindex="-1">` +
      (kind === "maximize" ? `<span class="memu-wc-ico max">${ICONS.maximize}</span><span class="memu-wc-ico restore">${ICONS.restore}</span>`
        : `<span class="memu-wc-ico">${ICONS[kind]}</span>`) + "</button>").join("");
    controls.addEventListener("mousedown", (event) => { event.stopPropagation(); if (event.button === 0) event.preventDefault(); });
    controls.addEventListener("dblclick", (event) => event.stopPropagation());
    controls.addEventListener("click", (event) => {
      const button = event.target.closest("[data-wc]");
      if (!button) return;
      const kind = button.dataset.wc;
      // Clear the hover look at once: after minimize/close the pointer may
      // never "leave" the button from the page's point of view.
      button.blur();
      call("titlebar_command", kind === "maximize" ? "toggle" : kind);
    });
    controls.addEventListener("contextmenu", (event) => {
      event.preventDefault(); event.stopPropagation();
      call("titlebar_system_menu", "mouse");
    });

    edges = document.createElement("div");
    edges.className = "memu-tb-resize";
    edges.setAttribute("aria-hidden", "true");
    edges.innerHTML = '<i data-edge="topleft"></i><i data-edge="top"></i><i data-edge="topright"></i>';
    edges.addEventListener("mousedown", (event) => {
      const edge = event.target.closest("[data-edge]");
      if (!edge || event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      call("titlebar_drag", edge.dataset.edge);
    });
    document.body.appendChild(edges);
    document.body.appendChild(controls);
  }

  function updateLabels() {
    if (!controls) return;
    const text = labels();
    controls.setAttribute("aria-label", text.group);
    controls.querySelectorAll("[data-wc]").forEach((button) => {
      const value = text[button.dataset.wc];
      button.setAttribute("aria-label", value);
      button.title = value;
    });
  }

  /* --------------------------------------------------------- geometry */
  function visible(node) { return !!(node && node.getClientRects().length); }

  /** Align the buttons with the top bar of whichever design is active. */
  function measure() {
    measureQueued = false;
    if (!enabled || !controls) return;
    const bar = document.querySelector(".topbar.appbar");
    let top = 0, right = 0, height = 32, mid = 16;
    if (visible(bar)) {
      const rect = bar.getBoundingClientRect();
      top = Math.max(0, rect.top);
      right = Math.max(0, window.innerWidth - rect.right);
      // Flush buttons stop above the bar's own bottom rule (Grid, Classic).
      height = rect.height - (parseFloat(getComputedStyle(bar).borderBottomWidth) || 0);
      // A wrapped (narrow) top bar is taller than its first row: centre the
      // buttons on the first row (the top-most item of the bar).
      let first = null;
      for (const child of bar.children) {
        if (!visible(child) || getComputedStyle(child).position === "absolute") continue;
        const r = child.getBoundingClientRect();
        if (r.height && (!first || r.top < first.top)) first = r;
      }
      if (first) {
        mid = first.top + first.height / 2 - rect.top;
        const row = Math.round(mid * 2);
        if (row > 20 && row < height) height = row;
      }
    }
    // A full-window overlay (settings, image viewer, palette…) covers the
    // top bar: the buttons then move to a standard 32 px caption band in the
    // window corner, which those overlays keep free (titlebar.css).
    let covered = false;
    if (visible(bar)) {
      const rect = bar.getBoundingClientRect();
      const x = Math.max(rect.left + 1, rect.right - controls.getBoundingClientRect().width - 24);
      const y = rect.top + Math.min(rect.height - 1, Math.max(1, mid));
      controls.style.visibility = "hidden";
      const hit = document.elementFromPoint(x, y);
      controls.style.visibility = "";
      covered = !!hit && !bar.contains(hit) && !(edges && edges.contains(hit));
    }
    if ((root.dataset.titlebarOverlay === "true") !== covered) root.dataset.titlebarOverlay = covered ? "true" : "false";
    const set = (name, value) => { if (root.style.getPropertyValue(name) !== value) root.style.setProperty(name, value); };
    set("--memu-tb-top", `${Math.round(top * 2) / 2}px`);
    set("--memu-tb-right", `${Math.round(right)}px`);
    set("--memu-tb-h", `${Math.max(28, Math.round(height))}px`);
    set("--memu-tb-mid", `${Math.max(14, Math.round(mid * 2) / 2)}px`);
    set("--memu-wc-w", `${Math.ceil(controls.getBoundingClientRect().width)}px`);
  }

  /** Only layout transitions of the bar or its ancestors can move it. */
  function onTransitionEnd(event) {
    const target = event.target;
    const bar = document.querySelector(".topbar.appbar");
    if (target instanceof Element && (target === bar || (bar && target.contains(bar)) || target.parentElement === document.body)) queueMeasure();
  }

  function queueMeasure() {
    if (measureQueued || !enabled) return;
    measureQueued = true;
    requestAnimationFrame(measure);
  }

  /* -------------------------------------------------------- drag zone */
  function hasPointerCursor(node, stop) {
    for (let el = node; el && el !== stop && el !== document.body; el = el.parentElement) {
      if (getComputedStyle(el).cursor === "pointer") return true;
    }
    return false;
  }

  /** Is this press on an empty part of the title area? */
  function isDragTarget(target, clientY) {
    if (!(target instanceof Element)) return false;
    if (target.closest(NO_DRAG)) return false;
    if (target.closest("[data-drag-region], #startup-cover")) return true;
    if (root.dataset.titlebarOverlay === "true") {
      // Only the free caption band above an overlay drags.
      return clientY <= OVERLAY_BAND && (target.parentElement === document.body || target === document.body)
        && !hasPointerCursor(target, null);
    }
    const bar = document.querySelector(".topbar.appbar");
    if (bar && bar.contains(target)) return !hasPointerCursor(target, bar);
    const limit = visible(bar) ? bar.getBoundingClientRect().bottom : 40;
    if (clientY > limit) return false;
    if (target === root || target.matches(BAND_SURFACES) || target.closest(".brand")) {
      return !hasPointerCursor(target, null);
    }
    return false;
  }

  function onPointerDown(event) {
    if (!enabled || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (!isDragTarget(event.target, event.clientY)) return;
    event.preventDefault();
    const now = performance.now();
    const limit = (state && state.doubleClickMs) || 500;
    if (lastPress && now - lastPress.t <= limit
        && Math.abs(event.screenX - lastPress.x) <= 4 && Math.abs(event.screenY - lastPress.y) <= 4) {
      // Windows: double click on the caption toggles maximize.
      lastPress = null;
      call("titlebar_command", "toggle");
      return;
    }
    lastPress = { t: now, x: event.screenX, y: event.screenY };
    call("titlebar_drag", "caption");
  }

  function onContextMenu(event) {
    if (!enabled || !isDragTarget(event.target, event.clientY)) return;
    event.preventDefault();
    call("titlebar_system_menu", "mouse");
  }

  function onKeyDown(event) {
    if (!enabled) return;
    if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
        && (event.code === "Space" || event.key === " ")) {
      event.preventDefault();
      call("titlebar_system_menu", "keyboard");
    }
  }

  /* ------------------------------------------------------------ state */
  function applyFlags() {
    const s = state || {};
    root.dataset.titlebarMax = s.maximized ? "true" : "false";
    root.dataset.titlebarActive = s.active === false ? "false" : "true";
    root.dataset.titlebarFullscreen = (s.fullscreen || document.fullscreenElement) ? "true" : "false";
    if (s.os) root.dataset.titlebarOs = s.os;
    if (controls) {
      const max = controls.querySelector("[data-wc=maximize]");
      if (max) max.classList.toggle("is-maximized", !!s.maximized);
    }
    updateLabels();
  }

  function enable() {
    if (enabled) return;
    build();
    enabled = true;
    root.dataset.titlebar = "custom";
    document.addEventListener("mousedown", onPointerDown, true);
    document.addEventListener("contextmenu", onContextMenu, true);
    document.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", onWindowResize);
    window.addEventListener("focus", onFocusChange);
    window.addEventListener("blur", onFocusChange);
    document.addEventListener("fullscreenchange", applyFlags);
    if ("ResizeObserver" in window) {
      resizeObserver = new ResizeObserver(queueMeasure);
      const bar = document.querySelector(".topbar.appbar");
      if (bar) resizeObserver.observe(bar);
      resizeObserver.observe(document.body);
    }
    mutationObserver = new MutationObserver(queueMeasure);
    mutationObserver.observe(root, { attributes: true, attributeFilter: ["data-design", "data-density", "data-theme", "class"] });
    mutationObserver.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    // Overlays are direct children of <body>: watch them opening/closing
    // (cheap filter, so busy chat updates deeper in the tree cost nothing).
    const watch = (node) => {
      if (node.nodeType === 1 && node !== controls && node !== edges) {
        overlayObserver.observe(node, { attributes: true, attributeFilter: ["class", "hidden", "style", "aria-hidden", "open"] });
      }
    };
    overlayObserver = new MutationObserver((records) => {
      for (const record of records) record.addedNodes.forEach(watch);
      queueMeasure();
    });
    overlayObserver.observe(document.body, { childList: true });
    Array.from(document.body.children).forEach(watch);
    // Design switches animate margins; settle after transitions as well.
    document.addEventListener("transitionend", onTransitionEnd, true);
    applyFlags();
    measure();
  }

  function disable() {
    if (!enabled && !controls) return;
    enabled = false;
    delete root.dataset.titlebar;
    delete root.dataset.titlebarMax;
    delete root.dataset.titlebarActive;
    delete root.dataset.titlebarFullscreen;
    delete root.dataset.titlebarOs;
    delete root.dataset.titlebarOverlay;
    ["--memu-tb-top", "--memu-tb-right", "--memu-tb-h", "--memu-tb-mid", "--memu-wc-w"].forEach((name) => root.style.removeProperty(name));
    document.removeEventListener("mousedown", onPointerDown, true);
    document.removeEventListener("contextmenu", onContextMenu, true);
    document.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("resize", onWindowResize);
    window.removeEventListener("focus", onFocusChange);
    window.removeEventListener("blur", onFocusChange);
    document.removeEventListener("fullscreenchange", applyFlags);
    document.removeEventListener("transitionend", onTransitionEnd, true);
    if (resizeObserver) resizeObserver.disconnect();
    if (mutationObserver) mutationObserver.disconnect();
    if (overlayObserver) overlayObserver.disconnect();
    resizeObserver = mutationObserver = overlayObserver = null;
    if (controls) controls.remove();
    if (edges) edges.remove();
    controls = edges = null;
  }

  function apply(next) {
    if (!next || typeof next !== "object") return;
    state = next;
    if (next.installed) enable(); else disable();
    if (enabled) { applyFlags(); queueMeasure(); }
    document.dispatchEvent(new CustomEvent("memu-titlebar", { detail: { ...next } }));
  }

  async function sync() {
    const next = await call("titlebar_state");
    if (next) apply(next);
    if (next && next.pending && pendingTries < 60) {
      // The window is not attached yet (startup): ask again shortly.
      pendingTries += 1;
      setTimeout(sync, 250);
    }
    return next;
  }

  function onWindowResize() {
    queueMeasure();
    // Maximize/restore/minimize all resize the viewport: refresh the icon.
    clearTimeout(stateTimer);
    stateTimer = setTimeout(sync, 60);
  }

  function onFocusChange() {
    // A focused iframe also blurs the window object; ask the document.
    setTimeout(() => {
      if (!state) return;
      const active = document.hasFocus();
      if (active !== (state.active !== false)) { state = { ...state, active }; applyFlags(); }
      if (active) sync();
    }, 0);
  }

  async function setMode(mode) {
    const next = await call("titlebar_set_mode", mode === "native" ? "native" : "custom");
    if (next) apply(next);
    return next;
  }

  function boot() {
    if (booted) return;
    if (has("titlebar_state")) { booted = true; sync(); }
  }

  // pywebview injects window.pywebview.api asynchronously.
  window.addEventListener("pywebviewready", boot);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();

  return {
    sync,
    setMode,
    /** True inside the desktop window that can host the custom title bar. */
    available: () => has("titlebar_state") && !!(state ? state.supported : true),
    state: () => (state ? { ...state } : null),
    active: () => enabled,
    // Exposed for tests only.
    _isDragTarget: isDragTarget,
    _measure: measure,
  };
})();
window.MemuTitlebar = MemuTitlebar;
