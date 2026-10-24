/* MemuAI Mini App — connection to the Telegram client.
 *
 * The official telegram-web-app.js is shipped in vendor/ and loaded from our
 * own origin first, then from telegram.org. Where both fail (the Mini App itself still opens: the client talks
 * to it directly), a compact built-in bridge takes over after a short wait:
 * it reads the launch parameters from the URL hash and speaks the same
 * postEvent / receiveEvent protocol (core.telegram.org/api/web-events), so
 * initData, the theme, the Back/Main buttons, haptics and popups keep working.
 */
(function () {
  "use strict";
  const OFFICIAL = "https://telegram.org/js/telegram-web-app.js?59";
  const VENDORED = "vendor/telegram-web-app.js";
  const WAIT_MS = 1800;

  function parseQuery(text) {
    const out = {};
    String(text || "").replace(/^[#?]/, "").split("&").forEach((pair) => {
      if (!pair) return;
      const at = pair.indexOf("=");
      const key = decodeURIComponent(at < 0 ? pair : pair.slice(0, at));
      let value = at < 0 ? "" : pair.slice(at + 1);
      try { value = decodeURIComponent(value.replace(/\+/g, "%20")); } catch (e) { /* keep raw */ }
      out[key] = value;
    });
    return out;
  }

  function launchParams() {
    let params = parseQuery(location.hash);
    if (params.tgWebAppData) {
      try { sessionStorage.setItem("memu_tg_launch", JSON.stringify(params)); } catch (e) { /* private mode */ }
      return params;
    }
    try { params = JSON.parse(sessionStorage.getItem("memu_tg_launch") || "{}") || {}; } catch (e) { params = {}; }
    return params;
  }

  function versionAtLeast(have, want) {
    const a = String(have || "6.0").split("."), b = String(want).split(".");
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const x = +a[i] || 0, y = +b[i] || 0;
      if (x !== y) return x > y;
    }
    return true;
  }

  /* ---------------------------------------------------------------- built-in bridge */
  function makeShim() {
    const params = launchParams();
    const listeners = {};
    const isIframe = (() => { try { return window.parent != null && window !== window.parent; } catch (e) { return true; } })();

    function postEvent(type, data) {
      const body = JSON.stringify(data || {});
      try {
        if (window.TelegramWebviewProxy !== undefined) { window.TelegramWebviewProxy.postEvent(type, body); return; }
        if (window.external && "notify" in window.external) { window.external.notify(JSON.stringify({ eventType: type, eventData: data || {} })); return; }
        if (isIframe) window.parent.postMessage(JSON.stringify({ eventType: type, eventData: data || {} }), "*");
      } catch (e) { /* not inside Telegram */ }
    }
    function emit(name, arg) { (listeners[name] || []).slice().forEach((fn) => { try { fn.call(app, arg); } catch (e) { console.error(e); } }); }

    let theme = {};
    try { theme = JSON.parse(params.tgWebAppThemeParams || "{}") || {}; } catch (e) { theme = {}; }
    const initData = params.tgWebAppData || "";
    const unsafe = parseQuery(initData);
    ["user", "receiver", "chat"].forEach((key) => { if (unsafe[key]) { try { unsafe[key] = JSON.parse(unsafe[key]); } catch (e) { /* ignore */ } } });

    function luminance(hex) {
      const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
      if (!m) return 0;
      const n = parseInt(m[1], 16);
      return (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
    }
    function bottomButton(kind) {
      const evt = kind === "main" ? "mainButtonClicked" : "secondaryButtonClicked";
      const state = { text: kind === "main" ? "Continue" : "Cancel", color: theme.button_color || "#2481cc",
        textColor: theme.button_text_color || "#ffffff", isVisible: false, isActive: true, isProgressVisible: false, hasShineEffect: false };
      const sync = () => postEvent(kind === "main" ? "web_app_setup_main_button" : "web_app_setup_secondary_button", {
        is_visible: state.isVisible, is_active: state.isActive, text: state.text, color: state.color,
        text_color: state.textColor, is_progress_visible: state.isProgressVisible, has_shine_effect: state.hasShineEffect });
      const button = {
        get text() { return state.text; }, get isVisible() { return state.isVisible; }, get isActive() { return state.isActive; },
        setText(text) { state.text = String(text || ""); sync(); return button; },
        setParams(p) { if (p.text != null) state.text = String(p.text); if (p.color) state.color = p.color; if (p.text_color) state.textColor = p.text_color;
          if (p.is_visible != null) state.isVisible = !!p.is_visible; if (p.is_active != null) state.isActive = !!p.is_active; sync(); return button; },
        show() { state.isVisible = true; sync(); return button; }, hide() { state.isVisible = false; sync(); return button; },
        enable() { state.isActive = true; sync(); return button; }, disable() { state.isActive = false; sync(); return button; },
        showProgress() { state.isProgressVisible = true; sync(); return button; }, hideProgress() { state.isProgressVisible = false; sync(); return button; },
        onClick(fn) { app.onEvent(evt, fn); return button; }, offClick(fn) { app.offEvent(evt, fn); return button; },
      };
      return button;
    }
    let backVisible = false;
    const popupCallbacks = [];
    const app = {
      initData, initDataUnsafe: unsafe, version: params.tgWebAppVersion || "6.0", platform: params.tgWebAppPlatform || "unknown",
      get themeParams() { return theme; },
      get colorScheme() { return luminance(theme.bg_color || "#ffffff") < 0.5 ? "dark" : "light"; },
      isExpanded: true, viewportHeight: window.innerHeight, viewportStableHeight: window.innerHeight,
      safeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 }, contentSafeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 },
      isFullscreen: false, shim: true,
      isVersionAtLeast(v) { return versionAtLeast(app.version, v); },
      onEvent(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
      offEvent(name, fn) { listeners[name] = (listeners[name] || []).filter((x) => x !== fn); },
      ready() { postEvent("web_app_ready"); }, expand() { postEvent("web_app_expand"); }, close() { postEvent("web_app_close"); },
      setHeaderColor(color) { postEvent("web_app_set_header_color", /^#/.test(color) ? { color } : { color_key: color }); },
      setBackgroundColor(color) { postEvent("web_app_set_background_color", { color }); },
      setBottomBarColor(color) { if (versionAtLeast(app.version, "7.10")) postEvent("web_app_set_bottom_bar_color", { color }); },
      enableClosingConfirmation() { postEvent("web_app_setup_closing_behavior", { need_confirmation: true }); },
      disableClosingConfirmation() { postEvent("web_app_setup_closing_behavior", { need_confirmation: false }); },
      disableVerticalSwipes() { if (versionAtLeast(app.version, "7.7")) postEvent("web_app_setup_swipe_behavior", { allow_vertical_swipe: false }); },
      enableVerticalSwipes() { if (versionAtLeast(app.version, "7.7")) postEvent("web_app_setup_swipe_behavior", { allow_vertical_swipe: true }); },
      requestFullscreen() { if (versionAtLeast(app.version, "8.0")) postEvent("web_app_request_fullscreen"); },
      exitFullscreen() { if (versionAtLeast(app.version, "8.0")) postEvent("web_app_exit_fullscreen"); },
      openLink(url) { if (window.TelegramWebviewProxy || isIframe) postEvent("web_app_open_link", { url }); else window.open(url, "_blank", "noopener"); },
      openTelegramLink(url) { postEvent("web_app_open_tg_link", { path_full: String(url).replace(/^https:\/\/t\.me/, "") }); },
      showPopup(p, cb) { popupCallbacks.push(cb || null); postEvent("web_app_open_popup", { title: p.title || "", message: p.message, buttons: p.buttons || [{ type: "close" }] }); },
      showAlert(message, cb) { app.showPopup({ message, buttons: [{ type: "close" }] }, cb ? () => cb() : null); },
      showConfirm(message, cb) { app.showPopup({ message, buttons: [{ type: "ok", id: "ok" }, { type: "cancel" }] }, (id) => cb && cb(id === "ok")); },
      BackButton: {
        get isVisible() { return backVisible; },
        show() { backVisible = true; postEvent("web_app_setup_back_button", { is_visible: true }); return app.BackButton; },
        hide() { backVisible = false; postEvent("web_app_setup_back_button", { is_visible: false }); return app.BackButton; },
        onClick(fn) { app.onEvent("backButtonClicked", fn); return app.BackButton; },
        offClick(fn) { app.offEvent("backButtonClicked", fn); return app.BackButton; },
      },
      HapticFeedback: {
        impactOccurred(style) { postEvent("web_app_trigger_haptic_feedback", { type: "impact", impact_style: style }); return app.HapticFeedback; },
        notificationOccurred(type) { postEvent("web_app_trigger_haptic_feedback", { type: "notification", notification_type: type }); return app.HapticFeedback; },
        selectionChanged() { postEvent("web_app_trigger_haptic_feedback", { type: "selection_change" }); return app.HapticFeedback; },
      },
      SettingsButton: { show() { postEvent("web_app_setup_settings_button", { is_visible: true }); }, hide() { postEvent("web_app_setup_settings_button", { is_visible: false }); },
        onClick(fn) { app.onEvent("settingsButtonClicked", fn); } },
    };
    app.MainButton = bottomButton("main");
    app.SecondaryButton = bottomButton("secondary");

    function receive(type, data) {
      data = data || {};
      if (type === "theme_changed" && data.theme_params) { theme = data.theme_params; emit("themeChanged"); }
      else if (type === "viewport_changed") {
        if (data.height) app.viewportHeight = data.height;
        if (data.is_state_stable) app.viewportStableHeight = app.viewportHeight;
        app.isExpanded = !!data.is_expanded; emit("viewportChanged", { isStateStable: !!data.is_state_stable });
      }
      else if (type === "safe_area_changed") { app.safeAreaInset = Object.assign({ top: 0, bottom: 0, left: 0, right: 0 }, data); emit("safeAreaChanged"); }
      else if (type === "content_safe_area_changed") { app.contentSafeAreaInset = Object.assign({ top: 0, bottom: 0, left: 0, right: 0 }, data); emit("contentSafeAreaChanged"); }
      else if (type === "fullscreen_changed") { app.isFullscreen = !!data.is_fullscreen; emit("fullscreenChanged"); }
      else if (type === "back_button_pressed") emit("backButtonClicked");
      else if (type === "main_button_pressed") emit("mainButtonClicked");
      else if (type === "secondary_button_pressed") emit("secondaryButtonClicked");
      else if (type === "settings_button_pressed") emit("settingsButtonClicked");
      else if (type === "popup_closed") { const cb = popupCallbacks.shift(); if (cb) cb(data.button_id || null); emit("popupClosed", { button_id: data.button_id || null }); }
      else if (type === "visibility_changed") emit(data.is_visible ? "activated" : "deactivated");
    }
    window.Telegram = window.Telegram || {};
    window.Telegram.WebView = { receiveEvent: receive, postEvent };
    window.addEventListener("message", (event) => {
      let msg = event.data;
      try { if (typeof msg === "string") msg = JSON.parse(msg); } catch (e) { return; }
      if (msg && msg.eventType) receive(msg.eventType, msg.eventData);
    });
    window.Telegram.WebApp = app;
    // The official script may still arrive after the wait: it must not take the events away from this bridge.
    const webView = window.Telegram.WebView;
    try {
      Object.defineProperty(window.Telegram, "WebView", { get: () => webView, set: () => {}, configurable: false });
      Object.defineProperty(window.Telegram, "WebApp", { get: () => app, set: () => {}, configurable: false });
    } catch (e) { /* frozen by someone else */ }
    if (initData) { postEvent("web_app_request_theme"); postEvent("web_app_request_viewport"); postEvent("web_app_request_safe_area"); postEvent("web_app_request_content_safe_area"); }
    return app;
  }

  /* ---------------------------------------------------------------- loader */
  function loadScript(src, timeout) {
    return new Promise((resolve) => {
      const script = document.createElement("script");
      let done = false;
      const finish = (ok) => { if (!done) { done = true; resolve(ok && !!(window.Telegram && window.Telegram.WebApp)); } };
      script.src = src;
      script.onload = () => finish(true);
      script.onerror = () => finish(false);
      setTimeout(() => finish(false), timeout);
      document.head.appendChild(script);
    });
  }

  async function loadOfficial() {
    if (window.Telegram && window.Telegram.WebApp) return true;
    // Only worth loading inside Telegram (launch data present).
    if (!launchParams().tgWebAppData) return false;
    // 1) the copy shipped with MemuAI (same origin: no extra connection, works where telegram.org is slow);
    if (await loadScript(VENDORED, WAIT_MS)) return true;
    // 2) telegram.org itself (HTTPS only — the page is always HTTPS inside Telegram).
    if (location.protocol === "https:" && await loadScript(OFFICIAL, WAIT_MS)) return true;
    return false;                                     // 3) the built-in bridge
  }

  window.MemuBridge = {
    ready: loadOfficial().then((official) => {
      const app = official ? window.Telegram.WebApp : makeShim();
      return { app, official, params: launchParams() };
    }),
    parseQuery,
    versionAtLeast,
  };
})();
