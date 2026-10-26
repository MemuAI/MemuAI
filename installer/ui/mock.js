

(function () {
  "use strict";
  var q = {};
  location.search.replace(/^\?/, "").split("&").forEach(function (kv) { if (!kv) return; var p = kv.split("="); q[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || ""); });
  var product = q.product === "proxy" ? { id: "proxy", name: "MemuAI Proxy" } : { id: "memuai", name: "MemuAI" };
  var kind = q.kind || "fresh";
  var MB = 1048576;
  var comps = product.id === "proxy" ? [{ id: "proxy", required: true, size: 96 * MB }]
    : [{ id: "proxy", required: true, size: 141 * MB }, { id: "memuai", required: false, size: 612 * MB }, { id: "memucode", required: false, size: 86 * MB }, { id: "memunote", required: false, size: 48 * MB }, { id: "memufiles", required: false, size: 34 * MB }];
  var dir = product.id === "proxy" ? "C:\\Users\\Yono\\AppData\\Local\\Programs\\MemuAI Proxy" : "C:\\Users\\Yono\\AppData\\Local\\Programs\\MemuAI";
  var state = { status: "idle", percent: 0, phase: "", file: "" };
  var timer = null, appsOnce = q.apps === "1";
  var FILES = ["MemuAIProxy/_internal/curl_cffi/libcurl-impersonate.dll", "MemuAI/_internal/onnxruntime/capi/onnxruntime.dll",
    "MemuAI/_internal/ctranslate2/ctranslate2.dll", "MemuAI/_internal/app/pgstatic/app.js", "MemuAI/MemuAI.pkg",
    "MemuCode/_internal/prompt_toolkit/application/application.py", "MemuAI/_internal/python313.dll",
    "MemuAI/_internal/piper/espeak-ng-data/ru_dict", "MemuAIProxy/MemuAIProxy.exe"];

  var removeData = false;
  function run(total, uninstall) {
    clearInterval(timer);
    var t0 = Date.now(), dur = (Number(q.speed) || 7) * 1000, freeze = q.pct != null && q.pct !== "" ? Number(q.pct) : null;
    if (appsOnce) { appsOnce = false; state = { status: "apps", apps: ["MemuAI.exe", "MemuAIProxy.exe"], percent: 3, phase: "closing" }; return; }
    if (q.error) { state = { status: "error", error: { code: q.error, message: q.error === "write_failed" ? "[WinError 32] The process cannot access the file: '…\\MemuAI\\_internal\\python313.dll'" : "" } }; return; }
    state = { status: "running", percent: 0, phase: uninstall ? "closing" : "preparing" };
    timer = setInterval(function () {
      var p = freeze != null ? freeze : Math.min(100, (Date.now() - t0) / dur * 100);
      var phase = uninstall ? (p < 8 ? "closing" : p < 20 ? "shortcuts" : p < 86 ? "files" : "registry")
        : (p < 2 ? "preparing" : p < 5 ? "closing" : p < 90 ? "extracting" : p < 93 ? "finishing" : "registering");
      state.percent = p; state.phase = phase;
      state.file = (phase === "extracting" || phase === "files") ? FILES[Math.floor(p * 7.3) % FILES.length] : "";
      if (!uninstall && phase === "extracting") { state.total = total; state.done = Math.round(total * (p - 5) / 85); } else { state.total = 0; }
      if (p >= 100 && freeze == null) {
        clearInterval(timer);
        var data = q.data === "none" ? [] : ["C:\\Users\\Yono\\AppData\\Roaming\\MemuAI"];
        state = uninstall ? { status: "done", percent: 100, phase: "done", result: { ok: true, failed: [], removed_data: removeData ? data : [], kept_data: removeData ? [] : data } }
          : { status: "done", percent: 100, phase: "done", result: { ok: true, install_dir: dir, components: ["proxy", "memuai"], warnings: [], webview2: "130.0.2849.80" } };
      }
    }, 60);
  }

  window.MemuMockApi = {
    init: function () {
      return {
        mode: q.mode === "uninstall" ? "uninstall" : "install",
        lang: q.lang === "ru" ? "ru" : "en",
        product: product, version: q.version || "3.6",
        kind: q.mode === "uninstall" ? "uninstall" : kind,
        existing: { found: kind !== "fresh" || q.mode === "uninstall", version: q.from != null ? q.from : kind === "upgrade" ? "2.0" : kind === "downgrade" ? "3.7" : (q.version || "3.6"), dir: dir, source: "memuai", components: [], legacy_machine: q.legacy === "1" },
        defaults: { dir: dir, components: comps.filter(function (c) { return c.id !== "memunote" && c.id !== "memufiles"; }).map(function (c) { return c.id; }), tasks: ["autostart"] },
        components: comps,
        tasks: product.id === "proxy" ? [{ id: "desktopicon", needs: [] }, { id: "autostart", needs: [] }]
          : [{ id: "desktopicon", needs: ["memuai"] }, { id: "proxyicon", needs: [] }, { id: "autostart", needs: [] }, { id: "memuaiautostart", needs: ["memuai"] }],
        default_dir: dir, app_dir: dir,
        data_dirs: product.id === "proxy" ? ["C:\\Users\\Yono\\AppData\\Roaming\\MemuAIProxy"] : ["C:\\Users\\Yono\\AppData\\Roaming\\MemuAI", "C:\\Users\\Yono\\AppData\\Local\\MemuAI"],
        log: "C:\\Users\\Yono\\AppData\\Local\\Temp\\MemuAI-Setup-20261007-112400.log"
      };
    },
    set_language: function (l) { return l; },
    check_dir: function (path, components) {
      var required = 0;
      comps.forEach(function (c) { if ((components || []).indexOf(c.id) >= 0) required += c.size; });
      if (!path) return { ok: false, error: "dir_empty", path: path, required: required, free: 0 };
      var adjusted = /^[A-Za-z]:\\?$/.test(path) || /\\Desktop\\?$/i.test(path);
      return { ok: true, path: adjusted ? path.replace(/\\?$/, "\\") + product.name : path, adjusted: adjusted, required: required, free: 182.4 * 1073741824 };
    },
    browse: function () { return "D:\\Apps"; },
    install: function (opts, force) {
      (window.__memuCalls = window.__memuCalls || []).push(["install", JSON.parse(JSON.stringify(opts)), !!force]);
      var total = 0;
      comps.forEach(function (c) { if ((opts.components || []).indexOf(c.id) >= 0) total += c.size; });
      run(total, false); return { started: true };
    },
    uninstall: function (remove) { removeData = !!remove; run(0, true); return { started: true }; },
    poll: function () { return JSON.parse(JSON.stringify(state)); },
    cancel: function () { clearInterval(timer); setTimeout(function () { state = { status: "cancelled", percent: 0 }; }, 400); return true; },
    launch: function () { (window.__memuCalls = window.__memuCalls || []).push(["launch"]); return true; },
    open_folder: function (p) { (window.__memuCalls = window.__memuCalls || []).push(["open_folder", p]); return true; }, open_log: function () { return true; },
    minimize: function () {}, close: function () { (window.__memuCalls = window.__memuCalls || []).push(["close"]); },

    drag_window: function () { (window.__memuDrag = window.__memuDrag || []).push(["drag"]); return false; },
    move_window: function (x, y) { (window.__memuDrag = window.__memuDrag || []).push(["move", x, y]); return true; }
  };

  window.__memuSetupReady = function (S, ui) {
    window.__memuUi = ui;
    var target = q.screen;
    if (!target) return;
    setTimeout(function () {
      if (target === "edition" || target === "location" || target === "options" || target === "review") {
        ui.startWizard();
        if (kind !== "fresh" && target !== "review") ui.editStep(target);
        for (var i = 0; i < 6 && S.screen !== target && S.screen !== "review"; i++) ui.nextStep();
      }
      else if (target === "express") { ui.expressSetup(); }
      else if (target === "progress") { if (q.mode === "uninstall") document.getElementById("btn-un-go").click(); else ui.startInstall(false); }
      else if (target === "done") { ui.startInstall(false); }
      else if (target === "error") { ui.showError({ code: q.code || "write_failed", message: "[WinError 32] The process cannot access the file because it is being used by another process: 'C:\\Users\\Yono\\AppData\\Local\\Programs\\MemuAI\\MemuAI\\_internal\\python313.dll'" }); }
      else if (target === "apps") { ui.show("review"); ui.showApps(["MemuAI.exe", "MemuAIProxy.exe"]); }
      else if (target === "un-done" || target === "keepoff") {
        if (target === "keepoff") { var k = document.getElementById("opt-keep"); k.checked = false; k.dispatchEvent(new Event("change")); }
        else document.getElementById("btn-un-go").click();
      }
      else if (target === "confirm") { ui.startInstall(false); setTimeout(function () { document.getElementById("btn-close").click(); }, 900); }
    }, 350);
  };
})();
