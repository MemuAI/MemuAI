/* Round 38 (п.6): the title bar of a module installed as an app (/?module_app=<id>).
 *
 * The native side is app/module_appbar.py: the window keeps its sizable frame (Windows owns the side/bottom
 * resize borders, Aero Snap, shadow, rounded corners), only the caption is removed (app/win_titlebar.py), and
 * the page gets window.pywebview.api.appbar_*. Here the module's own header strip becomes the caption:
 *   - minimize / maximize-restore / close at its right end, in the module's colours;
 *   - pressing an empty part of the bar (or any [data-memu-drag] inside the module) starts the native move
 *     loop (Snap, drag-down-to-restore), a double press toggles maximize;
 *   - right click on the bar / Alt+Space — the real system menu; a short hover on «maximize» opens
 *     Windows 11 Snap Layouts;
 *   - a thin strip along the top edge starts the native top / corner resize.
 * Styles (manifest window.appbar, built-in defaults in module_appbar.BUILTIN): auto | transparent (floats over
 * the module, e.g. MemuMusic's dark player) | custom (module colours) | none (only the window buttons).
 * The module can change title/actions/colours at run time: Memu.appbar.set({title, actions, color, text, style}).
 * Nothing is drawn unless Python reports the hook installed — a browser preview never gets dead buttons
 * (tests force it with MemuModuleAppbar.preview()). No timers, no observers.
 */
(function () {
  "use strict";
  const root = document.documentElement;
  const APP = root.dataset.moduleApp || "";
  if (!APP) {
    window.MemuModuleAppbar = { fromModule: () => ({ ok: true, active: false }), key: () => false, refresh() {}, preview() {} };
    return;
  }
  const BUILTIN = {
    music: { style: "transparent", text: "#f4f1f6", overlay: true, height: 36 },
  };
  const ICONS = {
    minimize: '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M0 5.5h10"/></svg>',
    maximize: '<svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y=".5" width="9" height="9" rx="1.5"/></svg>',
    restore: '<svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y="2.5" width="7" height="7" rx="1.25"/>' +
      '<path d="M2.5 2.5V2A1.5 1.5 0 0 1 4 .5h4A1.5 1.5 0 0 1 9.5 2v4A1.5 1.5 0 0 1 8 7.5h-.5"/></svg>',
    close: '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M.5.5l9 9M9.5.5l-9 9"/></svg>',
  };
  const NO_DRAG = "button, a[href], input, textarea, select, [role=button], [role=toolbar] *, .module-window-actions, .module-window-tools, .mab-wc, .mab-resize, [data-no-drag]";

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en") || root.lang === "en";
  const L = (ru, english) => (en() ? english : ru);
  const bridge = () => (window.pywebview && window.pywebview.api) || null;
  const has = (name) => { const api = bridge(); return !!(api && typeof api[name] === "function"); };
  let fake = null;   // preview(): a browser page that pretends to be a native window
  async function call(name, ...args) {
    if (fake) return fake(name, ...args);
    if (!has(name)) return null;
    try { return await bridge()[name](...args); } catch (error) { return null; }
  }

  let state = null, runtime = {}, controls = null, edges = null, bound = false, snapTimer = 0, tries = 0;
  let nativeTheme = "";
  function syncNativeTheme() {
    const dark = typeof runtime.dark === "boolean" ? runtime.dark : root.dataset.theme !== "light";
    const background = runtime.background || getComputedStyle(root).getPropertyValue("--bg").trim();
    // The HTML host is another backing surface, separate from WinForms and
    // WebView2. At fractional DPI a partially covered last pixel must not
    // reveal the host's light theme behind a dark module iframe.
    if (/^#[0-9a-f]{6}$/i.test(background)) root.style.setProperty("--module-app-bg", background);
    root.style.colorScheme = dark ? "dark" : "light";
    if (!state) return;
    const key = `${dark}:${background}`;
    if (nativeTheme === key) return;
    nativeTheme = key;
    call("appbar_theme", dark, background).then(ok => { if (ok === false || ok === null) nativeTheme = ""; });
  }

  function catalogSpec() {
    const row = ((window.MemuModules && window.MemuModules.catalog) || []).find((item) => item.id === APP);
    const authored = row && row.window && typeof row.window.appbar === "object" ? row.window.appbar : null;
    return Object.assign({ style: "auto", height: 36, color: "", text: "", overlay: false }, BUILTIN[APP] || {}, authored || {},
      (state && state.appbar) || {});
  }
  function effective() {
    const out = catalogSpec();
    for (const key of ["color", "text", "style"]) if (runtime[key]) out[key] = runtime[key];
    if (!["auto", "transparent", "none", "custom"].includes(out.style)) out.style = "auto";
    out.overlay = out.style === "transparent" || out.style === "none" || !!out.overlay;
    out.height = Math.max(28, Math.min(64, Number(out.height) || 36));
    return out;
  }
  const win = () => document.querySelector(`.module-window[data-module="${CSS.escape(APP)}"]`) || document.getElementById("module-window");
  const bar = () => { const w = win(); return w && w.querySelector('[data-win="bar"]'); };

  /* ------------------------------------------------------------------ build */
  function build() {
    if (controls) return;
    controls = document.createElement("div");
    controls.className = "mab-wc";
    controls.setAttribute("role", "group");
    controls.innerHTML = ["minimize", "maximize", "close"].map((kind) =>
      `<button type="button" class="mab-btn mab-${kind}" data-mab="${kind}" tabindex="-1">` +
      (kind === "maximize" ? `<span class="mab-ico max">${ICONS.maximize}</span><span class="mab-ico restore">${ICONS.restore}</span>`
        : `<span class="mab-ico">${ICONS[kind]}</span>`) + "</button>").join("");
    controls.addEventListener("mousedown", (event) => { event.stopPropagation(); if (event.button === 0) event.preventDefault(); });
    controls.addEventListener("dblclick", (event) => event.stopPropagation());
    controls.addEventListener("click", (event) => {
      const button = event.target.closest("[data-mab]");
      if (!button) return;
      button.blur();
      clearTimeout(snapTimer);
      // Close goes through Windows (WM_SYSCOMMAND) → pywebview «closing» → the module's own unsaved question.
      call("appbar_command", button.dataset.mab === "maximize" ? "toggle" : button.dataset.mab).then(() => setTimeout(syncState, 120));
    });
    controls.addEventListener("contextmenu", (event) => { event.preventDefault(); event.stopPropagation(); call("appbar_system_menu", "mouse"); });
    const max = controls.querySelector(".mab-maximize");
    max.addEventListener("mouseenter", () => { clearTimeout(snapTimer); snapTimer = setTimeout(() => call("appbar_snap_layouts"), 650); });
    max.addEventListener("mouseleave", () => clearTimeout(snapTimer));

    edges = document.createElement("div");
    edges.className = "mab-resize";
    edges.setAttribute("aria-hidden", "true");
    edges.innerHTML = '<i data-edge="topleft"></i><i data-edge="top"></i><i data-edge="topright"></i>';
    edges.addEventListener("mousedown", (event) => {
      const edge = event.target.closest("[data-edge]");
      if (!edge || event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      call("appbar_drag", edge.dataset.edge);
    });
    document.body.appendChild(edges);
  }

  function labels() {
    if (!controls) return;
    const maxed = !!(state && state.maximized);
    const text = { minimize: L("Свернуть", "Minimize"), maximize: maxed ? L("Восстановить", "Restore") : L("Развернуть", "Maximize"), close: L("Закрыть", "Close") };
    controls.setAttribute("aria-label", L("Управление окном", "Window controls"));
    controls.querySelectorAll("[data-mab]").forEach((button) => {
      button.title = text[button.dataset.mab];
      button.setAttribute("aria-label", text[button.dataset.mab]);
    });
    controls.querySelector(".mab-maximize").classList.toggle("is-maximized", maxed);
  }

  function bindBar() {
    const node = bar();
    if (!node || bound === node) return;
    bound = node;
    // pointerdown in the capture phase: modules.js drags / expands the in-MemuAI window on the same bar.
    let last = { at: 0, x: 0, y: 0 };
    node.addEventListener("pointerdown", (event) => {
      if (!root.dataset.mab || event.button !== 0 || event.target.closest(NO_DRAG)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const now = Date.now();
      const limit = (state && state.doubleClickMs) || 500;
      const repeat = now - last.at < limit && Math.abs(event.clientX - last.x) < 5 && Math.abs(event.clientY - last.y) < 5;
      last = { at: repeat ? 0 : now, x: event.clientX, y: event.clientY };
      if (repeat) call("appbar_command", "toggle").then(() => setTimeout(syncState, 120));
      else call("appbar_drag", "caption");
    }, true);
    node.addEventListener("contextmenu", (event) => {
      if (!root.dataset.mab || event.target.closest(NO_DRAG)) return;
      event.preventDefault();
      call("appbar_system_menu", "mouse");
    });
  }

  /* ------------------------------------------------------------------ apply */
  function apply() {
    syncNativeTheme();
    const node = bar();
    const on = !!(state && state.installed) && !!node;
    if (!on) {
      for (const key of ["mab", "mabStyle", "mabMax", "mabActive", "mabCaption", "mabInk"]) delete root.dataset[key];
      if (controls) controls.remove();
      sendToModule({ active: false });
      return;
    }
    build();
    bindBar();
    const look = effective();
    root.dataset.mab = "1";
    root.dataset.mabStyle = look.style;
    root.dataset.mabMax = state.maximized ? "1" : "";
    root.dataset.mabActive = state.active === false ? "false" : "true";
    root.style.setProperty("--mab-h", look.height + "px");
    if (look.color) root.style.setProperty("--mab-bg", look.color); else root.style.removeProperty("--mab-bg");
    if (look.text) root.style.setProperty("--mab-fg", look.text); else root.style.removeProperty("--mab-fg");
    // caption:"off" — the module draws its own name (MemuMusic's sidebar logo), the bar keeps only its buttons;
    // a dark text colour on a transparent bar means a light module: the soft scrim turns light too.
    if (runtime.caption === "off") root.dataset.mabCaption = "off"; else delete root.dataset.mabCaption;
    const rgb = /^#[0-9a-f]{6}$/i.test(look.text || "") ? [1, 3, 5].map((i) => parseInt(look.text.slice(i, i + 2), 16)) : null;
    if (rgb && rgb[0] * .299 + rgb[1] * .587 + rgb[2] * .114 < 128) root.dataset.mabInk = "dark"; else delete root.dataset.mabInk;
    if (controls.parentNode !== node) node.appendChild(controls);
    labels();
    sendToModule({ active: true, height: look.height, overlay: look.overlay, style: look.style });
  }

  function sendToModule(data) {
    const frame = win() && win().querySelector("iframe");
    if (!frame || !frame.contentWindow) return;
    try {
      frame.contentWindow.postMessage({ type: "memu-module-host", action: "host-event", payload: { name: "appbar", data } }, "*");
    } catch (error) { /* the module is loading; its load event sends it again */ }
    if (!frame.dataset.mabLoad) {
      frame.dataset.mabLoad = "1";
      frame.addEventListener("load", () => setTimeout(apply, 60));
    }
  }

  async function syncState() {
    const next = await call("appbar_state");
    if (!next) {
      // pywebview injects its api a moment after the page loads.
      if (!fake && !state && tries++ < 40) setTimeout(syncState, 150);
      return;
    }
    state = next;
    apply();
  }

  /* ------------------------------------------------------------------ module side */
  function fromModule(moduleId, payload) {
    if (moduleId !== APP) return { ok: true, active: false };
    payload = payload || {};
    if (payload.drag) {
      if (!root.dataset.mab) return { ok: true, active: false };
      if (payload.drag === "toggle") call("appbar_command", "toggle").then(() => setTimeout(syncState, 120));
      else call("appbar_drag", "caption");
      return { ok: true, active: true };
    }
    const clean = (value) => (/^#[0-9a-f]{6}$/i.test(String(value || "")) ? String(value) : "");
    runtime = { color: clean(payload.color), text: clean(payload.text), background: clean(payload.background),
      dark: typeof payload.dark === "boolean" ? payload.dark : null,
      style: ["auto", "transparent", "none", "custom"].includes(payload.style) ? payload.style : "",
      caption: payload.caption === "off" ? "off" : "" };
    apply();
    return { ok: true, active: !!root.dataset.mab, style: root.dataset.mabStyle || "" };
  }

  /** Alt+Space pressed inside the module (forwarded by the bridge): the real system menu. */
  function key(combo) {
    if (!root.dataset.mab || !combo || !combo.altKey || combo.ctrlKey || combo.metaKey || combo.key !== " ") return false;
    call("appbar_system_menu", "keyboard");
    return true;
  }

  function boot() {
    window.addEventListener("pywebviewready", syncState);
    window.addEventListener("resize", () => { if (root.dataset.mab) syncState(); }, { passive: true });
    window.addEventListener("focus", () => { if (root.dataset.mab) syncState(); });
    window.addEventListener("blur", () => { if (root.dataset.mab) { root.dataset.mabActive = "false"; } });
    document.addEventListener("keydown", (event) => {
      if (key({ key: event.key, altKey: event.altKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey })) event.preventDefault();
    }, true);
    syncState();
  }

  window.MemuModuleAppbar = {
    fromModule, key,
    refresh: syncState,
    get active() { return !!root.dataset.mab; },
    /** Tests / design preview in a browser: behave as an installed native caption. */
    preview(options) {
      const opts = options || {};
      const log = (window.__mabCalls = window.__mabCalls || []);
      let maximized = !!opts.maximized;
      fake = async (name, arg) => {
        log.push([name, arg]);
        if (name === "appbar_command" && arg === "toggle") maximized = !maximized;
        if (name === "appbar_state") return { installed: true, maximized, active: true, os: "win11", appbar: opts.appbar || null };
        return true;
      };
      state = null;
      return syncState();
    },
  };
  new MutationObserver(() => syncNativeTheme()).observe(root, {attributes: true, attributeFilter: ["data-theme"]});
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
