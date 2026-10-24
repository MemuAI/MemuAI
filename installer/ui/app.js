/* MemuAI Setup — interface logic: screens, i18n, the bridge to the Python
 * engine (window.pywebview.api, see installer/bridge.py) and the 3D scene. */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var html = document.documentElement;
  var I18N = window.MEMU_I18N;

  var S = {
    info: null, lang: "en", screen: "", history: [],
    edition: "full", components: [], tasks: [], dir: "", dirCheck: null,
    // wizard (Round 23): "Install" walks every step; an update starts at the review and
    // `expanded` turns it into the full walk once the user asks to change something;
    // `editing` = a step was opened from the review's "Change" (its primary returns there)
    expanded: false, editing: false,
    polling: null, status: "idle", result: null, stopping: false, lastPct: 0
  };

  // ------------------------------------------------------------------ bridge
  var api = null;
  function call(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    if (!api || typeof api[name] !== "function") return Promise.reject(new Error("bridge: " + name));
    try { return Promise.resolve(api[name].apply(api, args)); } catch (e) { return Promise.reject(e); }
  }

  // ------------------------------------------------------------------ i18n
  function esc(v) { return String(v == null ? "" : v).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function t(key, vars) {
    var dict = I18N[S.lang] || I18N.en;
    var s = dict[key] != null ? dict[key] : (I18N.en[key] != null ? I18N.en[key] : key);
    return s.replace(/\{(\w+)\}/g, function (_m, k) { return vars && vars[k] != null ? esc(vars[k]) : ""; });
  }
  function name() { return S.info ? S.info.product.name : "MemuAI"; }
  function fmtBytes(n) {
    if (!n) return "—";
    var gb = n / 1073741824;
    if (gb >= 1) return gb.toFixed(gb >= 10 ? 0 : 1).replace(".", S.lang === "ru" ? "," : ".") + " " + t("size.unit.gb");
    return Math.max(1, Math.round(n / 1048576)) + " " + t("size.unit.mb");
  }

  function applyI18n() {
    html.lang = S.lang;
    $$("[data-i18n]").forEach(function (el) { el.innerHTML = t(el.getAttribute("data-i18n"), { name: name() }); });
    $$("[data-i18n-title]").forEach(function (el) { el.title = t(el.getAttribute("data-i18n-title")); el.setAttribute("aria-label", el.title); });
    var seg = $(".seg");
    seg.setAttribute("data-lang", S.lang);
    $$(".seg button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-lang") === S.lang); });
    if (S.info) renderDynamic();
    if (S.screen) fit();
  }

  // ------------------------------------------------------------------ dynamic text
  var ICONS = {
    models: '<path d="M4 7.5L12 3l8 4.5v9L12 21l-8-4.5z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    research: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>',
    voice: '<path d="M4 12h2M8 8v8M12 5v14M16 8v8M20 11v2"/>',
    local: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    accounts: '<circle cx="9" cy="9" r="3.2"/><path d="M3.5 19c.8-3 3-4.6 5.5-4.6s4.7 1.6 5.5 4.6"/><circle cx="17" cy="8" r="2.4"/><path d="M15.5 13.6c2.4-.4 4.3 1 5 3.6"/>',
    api: '<path d="M8 6l-5 6 5 6M16 6l5 6-5 6M13.5 4l-3 16"/>'
  };

  function isProxyProduct() { return S.info && S.info.product.id === "proxy"; }

  // The welcome screen has four faces (installer/versions.py decides which):
  // fresh install, UPDATE (older version installed, or one whose number can't be
  // read), REPAIR (same version) and DOWNGRADE (newer version installed). The
  // work underneath is the same; only words and the version-transition visual differ.
  function welcomeKind() {
    var kind = S.info && S.info.kind;
    return kind === "upgrade" || kind === "reinstall" || kind === "downgrade" ? kind : "fresh";
  }

  var KEEP_ICON = '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg>';
  function renderVersionTransition(kind, from, v) {
    var vt = $("#vt");
    vt.hidden = kind === "fresh";
    if (kind === "fresh") return;
    vt.setAttribute("data-kind", kind);
    var fromEl = $("#vt-from"), toEl = $("#vt-to");
    fromEl.textContent = from || t("vt.unknown");
    fromEl.classList.toggle("word", !from);
    toEl.textContent = v;
    $("#vt-from-label").textContent = t("vt.installed");
    $("#vt-to-label").textContent = t(kind === "reinstall" ? "vt.same" : kind === "downgrade" ? "vt.older" : "vt.new");
    var path = $(".vt-path", vt);
    if (path && path.offsetWidth) vt.style.setProperty("--vt-len", path.offsetWidth + "px");
  }

  function renderDynamic() {
    var info = S.info, v = info.version, from = info.existing.version || "";
    var kind = info.mode === "uninstall" ? "uninstall" : welcomeKind();
    var vars = { version: v, from: from, name: name() };
    document.title = t(info.mode === "uninstall" ? "title.uninstall" : kind === "upgrade" ? "title.update" : "title.setup", vars);
    $("#brand-title").textContent = document.title;
    $("#brand-ver").textContent = v || from || "";
    $("#wordmark").textContent = name();
    // welcome
    var kickerKey = "welcome.kicker", titleKey = "welcome.title", leadKey = "welcome.lead";
    if (kind === "upgrade") { kickerKey = from ? "welcome.upgrade.kicker" : "welcome.upgrade.kicker.unknown"; titleKey = "welcome.upgrade.title"; leadKey = "welcome.upgrade.lead"; }
    else if (kind === "reinstall") { kickerKey = "welcome.reinstall.kicker"; titleKey = "welcome.reinstall.title"; leadKey = "welcome.reinstall.lead"; }
    else if (kind === "downgrade") { kickerKey = "welcome.downgrade.kicker"; titleKey = "welcome.downgrade.title"; leadKey = "welcome.downgrade.lead"; }
    else if (isProxyProduct()) { titleKey = "welcome.title.proxy"; leadKey = "welcome.lead.proxy"; }
    var welcome = $('.screen[data-screen="welcome"]');
    welcome.setAttribute("data-kind", kind);
    var kicker = $("#welcome-kicker").parentNode;
    kicker.classList.toggle("update", kind === "upgrade");
    kicker.classList.toggle("warn", kind === "downgrade");
    $("#welcome-kicker").innerHTML = t(kickerKey, vars);
    $("#welcome-title").innerHTML = t(titleKey, vars);
    $("#welcome-lead").innerHTML = t(leadKey, vars);
    renderVersionTransition(kind, from, v);
    var keep = $("#keep-chips");
    keep.hidden = !(kind === "upgrade" || kind === "reinstall");
    keep.innerHTML = (isProxyProduct() ? ["accounts", "settings"] : ["chats", "profiles", "accounts", "settings"]).map(function (k) {
      return "<li>" + KEEP_ICON + "<span>" + t("keep." + k) + "</span></li>";
    }).join("");
    $("#downgrade-note").hidden = kind !== "downgrade";
    $("#downgrade-note-text").innerHTML = t("welcome.downgrade.note", vars);
    var feats = isProxyProduct() ? ["accounts", "api", "local", "models"] : ["models", "research", "voice", "local"];
    $("#features").innerHTML = feats.map(function (f) {
      return '<div class="feat"><span class="feat-ic"><svg viewBox="0 0 24 24">' + ICONS[f] + '</svg></span><span><b>' +
        t("feat." + f + ".t") + "</b><small>" + t("feat." + f + ".b") + "</small></span></div>";
    }).join("");
    var size = sizeOf(S.components);
    $("#welcome-note").innerHTML = USER_ICON + "<span>" + t("welcome.note", { size: fmtBytes(size) }) + "</span>";
    var quickKey = kind === "upgrade" ? "btn.update" : kind === "reinstall" ? "btn.reinstall" : kind === "downgrade" ? "btn.downgrade" : "btn.install";
    $("#btn-start-label").textContent = t(quickKey, vars);
    $("#btn-install-label").textContent = t(quickKey, vars);
    // Express = straight to the review with the defaults (nothing is installed without the
    // review's confirmation). An update already starts at the review, so it needs no shortcut.
    $("#btn-express").hidden = kind !== "fresh";
    $("#btn-express").title = t("btn.express.hint");
    $("#legacy-welcome").hidden = !info.existing.legacy_machine;
    $("#legacy-options").hidden = !info.existing.legacy_machine;
    // edition
    $("#size-full").textContent = t("edition.size", { size: fmtBytes(sizeOf(["proxy", "memuai", S.components.indexOf("memucode") >= 0 ? "memucode" : "", S.components.indexOf("memunote") >= 0 ? "memunote" : "", S.components.indexOf("memufiles") >= 0 ? "memufiles" : ""])) });
    $("#size-proxy").textContent = t("edition.size", { size: fmtBytes(sizeOf(["proxy"])) });
    // location & options
    renderToggles();
    $("#data-note-text").innerHTML = t("options.data", { path: (info.data_dirs[0] || "%APPDATA%\\MemuAI") });
    renderSpace();
    renderReview();
    // progress / done
    var upd = kind === "upgrade", repair = kind === "reinstall";
    $("#progress-title").textContent = info.mode === "uninstall" ? t("un.progress.title", vars)
      : t(upd ? "install.title.update" : repair ? "install.title.repair" : "install.title", vars);
    $("#done-title").innerHTML = t(upd ? "done.title.update" : repair ? "done.title.repair" : "done.title", vars);
    $("#launch-label").textContent = t("btn.open", { app: launchApp() });
    // uninstall
    $("#un-kicker").innerHTML = t("un.kicker", { version: from || v });
    $("#un-title").innerHTML = t("un.title", vars);
    $("#un-dir").textContent = info.app_dir || info.existing.dir || "";
    $("#un-warn-text").innerHTML = t("un.remove.warn", { paths: (info.data_dirs || []).join(", ") });
    renderUnChips();
    $("#error-title").textContent = t(info.mode === "uninstall" ? "error.title.uninstall" : "error.title");
    if (S.screen === "progress") updateProgress(lastPoll || {});
    renderUnDone();
    renderStepper();
  }

  function sizeOf(ids) {
    var total = 0;
    (S.info.components || []).forEach(function (c) { if (ids.indexOf(c.id) >= 0) total += c.size || 0; });
    return total;
  }

  // ------------------------------------------------------------------ edition & options
  function setEdition(ed) {
    S.edition = ed;
    $$(".card").forEach(function (c) { c.setAttribute("aria-pressed", String(c.getAttribute("data-edition") === ed)); });
    var cli = $("#opt-memucode");
    $("#row-cli").classList.toggle("disabled", ed !== "full");
    var note = $("#opt-memunote");
    if (note) $("#row-note").classList.toggle("disabled", ed !== "full");
    var files = $("#opt-memufiles");
    if (files) $("#row-files").classList.toggle("disabled", ed !== "full");
    var comps = ["proxy"];
    if (ed === "full") { comps.push("memuai"); if (cli.checked) comps.push("memucode"); if (note && note.checked) comps.push("memunote"); if (files && files.checked) comps.push("memufiles"); }
    S.components = comps;
    S.tasks = S.tasks.filter(function (id) {
      var task = taskInfo(id);
      return task && task.needs.every(function (n) { return comps.indexOf(n) >= 0; });
    });
    renderDynamic();
    checkDir();
  }

  function taskInfo(id) {
    var list = S.info.tasks || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  function launchApp() { return S.components.indexOf("memuai") >= 0 && !isProxyProduct() ? "MemuAI" : "MemuAI Proxy"; }

  // Options step: the product's tasks in two groups (startup | desktop shortcuts), each
  // with a one-line explanation. Start-menu shortcuts and PATH are not optional — said once
  // in the footnote instead of as a switch that cannot be switched.
  var TASK_GROUPS = [["startup", ["autostart", "memuaiautostart"]], ["shortcuts", ["desktopicon", "proxyicon"]]];
  function taskLabel(id) {
    var proxy = isProxyProduct();
    return t(proxy && (id === "desktopicon" || id === "autostart") ? "task." + id + ".proxy" : "task." + id);
  }
  function taskAvailable(task) { return task.needs.every(function (n) { return S.components.indexOf(n) >= 0; }); }
  function renderToggles() {
    var host = $("#toggles"), proxy = isProxyProduct();
    host.innerHTML = TASK_GROUPS.map(function (group) {
      var rows = group[1].filter(function (id) { return taskInfo(id); }).map(function (id) {
        var task = taskInfo(id), available = taskAvailable(task);
        var desc = t(proxy && (id === "desktopicon" || id === "autostart") ? "task." + id + ".proxy.b" : "task." + id + ".b");
        return '<label class="row' + (available ? "" : " disabled") + '">' +
          '<span class="row-text"><b>' + taskLabel(id) + (id === "autostart" ? '<span class="rec">' + t("task.recommended") + "</span>" : "") +
          "</b><small>" + desc + "</small></span>" +
          '<input type="checkbox" class="switch" data-task="' + id + '"' + (S.tasks.indexOf(id) >= 0 && available ? " checked" : "") +
          (available ? "" : " disabled") + "></label>";
      }).join("");
      return rows ? '<div class="tgroup"><span class="group-label">' + t("options.group." + group[0]) + "</span>" + rows + "</div>" : "";
    }).join("");
    $$("input[data-task]", host).forEach(function (input) {
      input.addEventListener("change", function () {
        var id = input.getAttribute("data-task");
        S.tasks = S.tasks.filter(function (x) { return x !== id; });
        if (input.checked) S.tasks.push(id);
        renderReview();
      });
    });
    $("#options-foot").innerHTML = INFO_ICON + "<span>" + t(S.components.indexOf("memucode") >= 0 ? "options.foot.cli" : "options.foot") + "</span>";
  }

  // ------------------------------------------------------------------ review
  var SUM_ICONS = {
    version: '<path d="M4 12h11M11 7l5 5-5 5"/><path d="M20 5v14"/>',
    components: '<path d="M4 7.5L12 3l8 4.5v9L12 21l-8-4.5z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    location: '<path d="M3 7.5A2.5 2.5 0 015.5 5H10l2 2.2h6.5A2.5 2.5 0 0121 9.7v7.8a2.5 2.5 0 01-2.5 2.5h-13A2.5 2.5 0 013 17.5z"/>',
    options: '<rect x="3" y="4" width="18" height="13" rx="2.5"/><path d="M8 21h8M12 17v4"/>',
    data: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>'
  };
  var INFO_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8v.2"/></svg>';
  var DISK_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="6" width="17" height="12" rx="2.5"/><path d="M7 14.5h.1M10.5 14.5H17"/></svg>';
  var USER_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8.5" r="3.6"/><path d="M5 20c1-3.6 3.8-5.6 7-5.6s6 2 7 5.6"/></svg>';
  function chosenDir() { return S.dirCheck && S.dirCheck.ok ? S.dirCheck.path : $("#opt-dir").value; }
  function summaryRows() {
    var info = S.info, kind = welcomeKind(), v = info.version, from = info.existing.version || "";
    var rows = [];
    if (kind !== "fresh") {
      var value = kind === "reinstall" ? t("sum.version.same", { version: v })
        : '<span class="sum-from">' + esc(from || t("vt.unknown")) + '</span><span class="sum-arrow">→</span><span class="sum-to' + (kind === "downgrade" ? " warn" : "") + '">' + esc(v) + "</span>";
      rows.push({ id: "version", label: t("sum.version"), html: value });
    }
    if (!isProxyProduct()) {
      var comps = S.edition !== "full" ? t("sum.proxyonly") : t(S.components.indexOf("memucode") >= 0 ? "sum.full.cli" : "sum.full");
      if (S.edition === "full" && S.components.indexOf("memunote") >= 0) comps += " · " + t("sum.note");
      if (S.edition === "full" && S.components.indexOf("memufiles") >= 0) comps += " · " + t("sum.files");
      rows.push({ id: "components", label: t("sum.components"), html: comps, edit: "edition" });
    }
    rows.push({ id: "location", label: t("sum.location"), html: '<code class="path" title="' + esc(chosenDir()) + '">' + esc(chosenDir()) + "</code>", edit: "location", bad: !!(S.dirCheck && !S.dirCheck.ok) });
    var tasks = (info.tasks || []).map(function (x) { return x.id; }).filter(function (id) {
      var task = taskInfo(id); return S.tasks.indexOf(id) >= 0 && task && taskAvailable(task);
    });
    var order = ["autostart", "memuaiautostart", "desktopicon", "proxyicon"];
    tasks.sort(function (a, b) { return order.indexOf(a) - order.indexOf(b); });
    rows.push({ id: "options", label: t("sum.options"), html: tasks.length ? tasks.map(taskLabel).join(" · ") : t("sum.tasks.none"), edit: "options" });
    rows.push({ id: "data", label: t("sum.data"), html: kind === "fresh" ? t("sum.data.fresh", { path: info.data_dirs[0] || "" }) : t("sum.data.keep") });
    return rows;
  }
  function renderReview() {
    if (!S.info || S.info.mode === "uninstall") return;
    var kind = welcomeKind(), vars = { version: S.info.version, name: name() };
    $("#review-title").innerHTML = t(kind === "upgrade" ? "review.title.update" : kind === "reinstall" ? "review.title.repair" : kind === "downgrade" ? "review.title.downgrade" : "review.title", vars);
    $("#review-lead").innerHTML = t(kind === "fresh" ? "review.lead" : "review.lead.update", vars);
    $("#summary").innerHTML = summaryRows().map(function (r) {
      return '<li class="sum-row' + (r.bad ? " bad" : "") + '" data-row="' + r.id + '"><span class="sum-ic"><svg viewBox="0 0 24 24">' + SUM_ICONS[r.id] + "</svg></span>" +
        '<span class="sum-text"><small>' + r.label + '</small><span class="sum-value">' + r.html + "</span></span>" +
        (r.edit ? '<button class="btn link sum-edit" type="button" data-edit="' + r.edit + '">' + t("btn.edit") + "</button>" : "") + "</li>";
    }).join("");
    $$("[data-edit]", $("#summary")).forEach(function (b) { b.addEventListener("click", function () { editStep(b.getAttribute("data-edit")); }); });
    $("#review-downgrade").hidden = kind !== "downgrade";
    $("#review-downgrade-text").innerHTML = t("welcome.downgrade.note", { version: S.info.version, from: S.info.existing.version || "" });
    var res = S.dirCheck, req = res ? res.required : sizeOf(S.components);
    $("#review-space").innerHTML = DISK_ICON + "<span>" + (res && res.free ? t("review.space", { required: fmtBytes(req), free: fmtBytes(res.free) })
      : t("review.space.unknown", { required: fmtBytes(req) })) + "</span>";
    $("#btn-install").disabled = !!(res && !res.ok);
  }

  var dirTimer = null;
  function checkDir() {
    clearTimeout(dirTimer);
    dirTimer = setTimeout(function () {
      var value = $("#opt-dir").value;
      call("check_dir", value, S.components).then(function (res) {
        if ($("#opt-dir").value !== value) return;      // a newer check is on its way
        S.dirCheck = res;
        renderSpace();
        renderReview();
      }).catch(function () {});
    }, 180);
  }

  function renderSpace() {
    var res = S.dirCheck, msg = $("#dir-msg"), box = $("#path-box");
    var req = res ? res.required : sizeOf(S.components);
    var free = res ? res.free : 0;
    $("#space-text").textContent = free ? t("options.space", { required: fmtBytes(req), free: fmtBytes(free) }) : t("options.space.unknown", { required: fmtBytes(req) });
    $("#space-fill").style.width = free ? Math.min(100, Math.max(3, 100 * req / free)) + "%" : "0";
    msg.className = "field-msg";
    msg.textContent = "";
    box.classList.remove("bad");
    var def = S.info && (S.info.defaults.dir || S.info.default_dir) || "";
    $("#btn-dir-reset").hidden = !def || $("#opt-dir").value === def;
    if (!res) return;
    if (!res.ok && res.error) { msg.textContent = t("error." + res.error); msg.classList.add("bad"); box.classList.add("bad"); }
    else if (res.adjusted) msg.textContent = t("options.adjusted") + " → " + res.path;
    $("#btn-install").disabled = !res.ok;
    $("#btn-location-next").disabled = !res.ok;
  }

  // ------------------------------------------------------------------ wizard & stepper
  // "Install" never skips a step. A first install walks every step with good defaults
  // already chosen (components → folder → shortcuts & startup → review → install); an
  // update, repair or downgrade opens the review of what will happen, and any "Change"
  // there turns it into the full walk.
  function flowSteps() {
    var full = isProxyProduct() ? ["location", "options", "review"] : ["edition", "location", "options", "review"];
    if (welcomeKind() !== "fresh" && !S.expanded) return ["review"];
    return full;
  }
  function stepperSteps() { return flowSteps().concat(["progress"]); }
  var WIZARD = { edition: 1, location: 1, options: 1, review: 1 };

  function startWizard() { S.editing = false; show(flowSteps()[0]); }
  function expressSetup() { S.editing = false; show("review"); }
  function editStep(id) {
    if (flowSteps().indexOf(id) < 0) S.expanded = true;
    S.editing = true;
    show(id);
  }
  function nextStep() {
    var f = flowSteps(), i = f.indexOf(S.screen);
    if (S.editing || i < 0) { show("review"); return; }
    show(f[Math.min(i + 1, f.length - 1)]);
  }
  function prevStep() {
    if (S.editing) { show("review", { back: true }); return; }
    var f = flowSteps(), i = f.indexOf(S.screen);
    show(i > 0 ? f[i - 1] : "welcome", { back: true });
  }

  function renderStepper() {
    var steps = stepperSteps();
    $$(".step").forEach(function (el) {
      var id = el.getAttribute("data-step");
      var index = steps.indexOf(id);
      el.hidden = index < 0;
      if (index >= 0) el.querySelector("b").textContent = String(index + 1);
    });
    var progressLabel = $('.step[data-step="progress"] span');
    if (progressLabel) progressLabel.textContent = t(S.info && S.info.mode === "install" && welcomeKind() === "upgrade" ? "step.update" : "step.install");
    var current = steps.indexOf(S.screen === "done" ? "progress" : S.screen);
    var preview = S.screen === "welcome";
    $("#stepper").classList.toggle("preview", preview);
    $$(".step").forEach(function (el) {
      var index = steps.indexOf(el.getAttribute("data-step"));
      el.classList.toggle("active", index === current && S.screen !== "done");
      el.classList.toggle("done", index >= 0 && (index < current || S.screen === "done"));
      if (index === current) el.setAttribute("aria-current", "step"); else el.removeAttribute("aria-current");
    });
    // the wizard's primary button: "Continue", or "Back to summary" after a "Change"
    $$("[data-next] .next-label").forEach(function (span) { span.textContent = t(S.editing ? "btn.toReview" : "btn.continue"); });
  }

  var CENTERED = { progress: 1, done: 1, error: 1, "un-done": 1 };
  var TALL = { done: 1, error: 1 };

  // Where the crystal sits. The numbers were tuned for the 960×620 design: on the
  // side screens it is centred in the free area left of the panel; on the centred
  // screens it sits in the free band above the panel and scales with that band.
  // The panel's FINAL rectangle is read from #panel-ghost (same geometry, no
  // transition), so the crystal glides in step with the panel morph.
  var HERO = {
    welcome: { y: 0.47, s: 0.47, light: 1 }, edition: { y: 0.47, s: 0.44, light: 1 }, location: { y: 0.47, s: 0.43, light: 1 },
    options: { y: 0.47, s: 0.42, light: 1 }, review: { y: 0.47, s: 0.45, light: 1 },
    uninstall: { y: 0.47, s: 0.44, light: 0.85 },
    progress: { ring: 1, ref: 306, s: 0.4, light: 1 }, "un-done": { f: 0.427, ref: 306, s: 0.3, light: 0.8 },
    done: { f: 0.47, ref: 212, s: 0.33, light: 1 }, error: { f: 0.47, ref: 212, s: 0.3, light: 0.55 }
  };
  var RING_MARGIN = 14, CSS_RING_RADIUS = 126;   // px; the CSS fallback's ring (.css-star .pring, r = 48 of 50) at scale 1
  function heroTarget(screen) {
    var spec = HERO[screen] || HERO.welcome;
    var W = window.innerWidth || 960, H = window.innerHeight || 620;
    var ghost = $("#panel-ghost"), r = ghost ? ghost.getBoundingClientRect() : null;
    var bar = $(".titlebar"), tb = bar ? bar.getBoundingClientRect().bottom : 46;
    if (!r || !r.width) r = { left: W * 0.46, top: H * 0.57 };
    if (spec.ring) {
      // progress: the ring (MemuScene.RING_RADIUS crystal units) is centred in the free band
      // between the title bar and the panel with RING_MARGIN above and below — never under the
      // title bar or cut by the window's top edge — and the crystal is sized to sit inside it
      var bandR = Math.max(60, r.top - tb), ringPx = Math.max(28, bandR / 2 - RING_MARGIN), cy = tb + bandR / 2;
      var units = (window.MemuScene && window.MemuScene.RING_RADIUS) || 1.25;
      return { x: 0.5, y: cy / H, s: ringPx / (units * 0.5 * H), light: spec.light, px: W / 2, py: cy, k: ringPx / CSS_RING_RADIUS };
    }
    if (spec.f == null) {
      // side screens: crystal size ∝ window height, but never wider than the free area
      var free = Math.max(120, r.left);
      var sc = Math.min(spec.s, spec.s * (free / 400) * (620 / H) * 1.18);
      return { x: (r.left / 2) / W, y: spec.y, s: sc, light: spec.light, px: r.left / 2, py: spec.y * H, k: (sc / 0.47) * (H / 620) };
    }
    var band = Math.max(60, r.top - tb);
    var scale = Math.max(0.12, Math.min(spec.s * 1.25, spec.s * (band / spec.ref) * (620 / H)));
    var py = tb + band * spec.f;
    return { x: 0.5, y: py / H, s: scale, light: spec.light, px: W / 2, py: py, k: 0.85 * Math.min(1.25, band / spec.ref) };
  }

  function placeHero() {
    var h = heroTarget(S.screen);
    html.style.setProperty("--hero-x", h.px.toFixed(1) + "px");
    html.style.setProperty("--hero-y", h.py.toFixed(1) + "px");
    html.style.setProperty("--hero-s", h.k.toFixed(3));
    var M = window.MemuScene;
    if (M && M.ok) M.setTarget(h.x, h.y, h.s, h.light);
    return h;
  }

  function sceneFor(screen) {
    placeHero();
    var M = window.MemuScene;
    if (!M || !M.ok) return;
    // The progress ring lives only on the progress screen; on "done" it stays full and fades out.
    if (screen === "progress") M.setRing(0, 1, true);
    else if (screen === "done") M.setRing(1, 0, false);
    else M.setRing(null, 0, false);
    switch (screen) {
      case "welcome": M.setEnergy(0.18); M.setFlow(0.35); break;
      case "edition": M.setEnergy(0.24); M.setFlow(0.4); break;
      case "location": M.setEnergy(0.26); M.setFlow(0.42); break;
      case "options": M.setEnergy(0.28); M.setFlow(0.45); break;
      case "review": M.setEnergy(0.32); M.setFlow(0.5); break;
      case "uninstall": M.setEnergy(0.12); M.setFlow(0.25); break;
      case "progress": M.setEnergy(0.35); M.setFlow(1); break;
      case "done": M.setEnergy(0.6); M.setFlow(0.55); M.setProgress(0); M.burst(); break;
      case "error": M.setEnergy(0.04); M.setFlow(0.15); M.setProgress(0); break;
      case "un-done": M.setEnergy(0.12); M.setFlow(0.3); M.setProgress(0); break;
    }
  }

  // ------------------------------------------------------------------ fit
  // The CSS is fluid down to MIN_W×MIN_H. Below that — or if a screen still does
  // not fit (a long translation, a warning note) — the whole stage is scaled with
  // a transform; the stage keeps covering the window exactly (its virtual size is
  // window / scale). Inner scrolling is the very last resort (scale < MIN_SCALE).
  var MIN_W = 860, MIN_H = 540, MIN_SCALE = 0.62, FIT_SLACK = 1;   // layout px are whole numbers: ±1 is rounding
  var fitState = { scale: 1, timer: null };
  function applyScale(s) {
    var W = window.innerWidth || MIN_W, H = window.innerHeight || MIN_H;
    fitState.scale = s;
    var st = html.style;
    st.setProperty("--stage-s", String(s));
    st.setProperty("--stage-w", (W / s).toFixed(2) + "px");
    st.setProperty("--stage-h", (H / s).toFixed(2) + "px");
    html.setAttribute("data-fit", s < 0.999 ? "scaled" : "fluid");
  }
  // How far the screen's content runs past its bottom padding, in layout px. Only layout
  // boxes are read (offsetTop/offsetHeight; the screen is its children's offsetParent):
  // while a screen enters, its children are still translated and the panel is scaled by
  // the entrance animations, and getBoundingClientRect/scrollHeight count that as overflow.
  // Measuring them made fit() shrink or densify a screen that fits, and the delayed refit
  // undid it a second later — the "jerk" at the end of every entrance (Round 25).
  function overflowOf(el) {
    if (!el) return 0;
    var kids = el.children, lastBottom = 0;
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k.hidden || k.offsetParent !== el || !k.offsetHeight) continue;
      lastBottom = Math.max(lastBottom, k.offsetTop + k.offsetHeight);
    }
    var limit = el.clientHeight - (parseFloat(getComputedStyle(el).paddingBottom) || 0);
    return lastBottom ? Math.max(0, lastBottom - limit) : 0;
  }
  function fit() {
    var W = window.innerWidth, H = window.innerHeight;
    if (!W || !H) return;
    // The panel is still morphing to its final height (#panel-ghost has it already): a screen
    // measured now would be judged against the wrong height. Wait for the morph to end.
    var ghost = $("#panel-ghost"), panel = $("#panel");
    if (ghost && panel && Math.abs(ghost.offsetHeight - panel.offsetHeight) > 1) { refit(120); return; }
    html.classList.remove("fit-scroll", "fit-dense", "fit-tight");
    var s = Math.min(1, W / MIN_W, H / MIN_H);
    applyScale(s);
    var screen = $(".screen.active");
    if (screen && overflowOf(screen) > FIT_SLACK) html.classList.add("fit-dense");
    if (screen && overflowOf(screen) > FIT_SLACK) html.classList.add("fit-tight");
    for (var i = 0; i < 6 && screen; i++) {
      var over = overflowOf(screen);
      if (over <= FIT_SLACK) break;
      var avail = Math.max(120, screen.clientHeight);
      s = Math.max(MIN_SCALE, s * avail / (avail + over + 2));
      applyScale(s);
      if (s <= MIN_SCALE) break;
    }
    if (screen && overflowOf(screen) > FIT_SLACK) html.classList.add("fit-scroll");
    var vt = $(".vt-path");
    if (vt && vt.offsetWidth) $("#vt").style.setProperty("--vt-len", vt.offsetWidth + "px");
    placeHero();
  }
  function refit(delay) {
    clearTimeout(fitState.timer);
    fitState.timer = setTimeout(fit, delay == null ? 0 : delay);
  }
  window.__memuFit = function () { fit(); return { scale: fitState.scale, scroll: html.classList.contains("fit-scroll"), dense: html.classList.contains("fit-dense"), tight: html.classList.contains("fit-tight") }; };

  function show(screen, opts) {
    opts = opts || {};
    var prev = S.screen;
    if (prev === screen) return;
    var back = !!opts.back;
    if (prev && !opts.replace && !back) S.history.push(prev);
    S.screen = screen;
    if (!WIZARD[screen] || screen === "review") S.editing = false;
    if (screen === "welcome") S.expanded = false;
    if (screen === "review") renderReview();
    $$(".screen").forEach(function (el) {
      var id = el.getAttribute("data-screen");
      if (id === screen) {
        el.classList.remove("leave-back", "from-back");
        if (back) { el.classList.add("from-back"); void el.offsetWidth; }
        el.classList.add("active");
        el.classList.remove("from-back");
      } else if (el.classList.contains("active")) {
        el.classList.remove("active");
        el.classList.toggle("leave-back", back);
      }
    });
    html.setAttribute("data-screen", screen);
    var modeChange = document.body.classList.contains("centered") !== !!CENTERED[screen] ||
      document.body.classList.contains("tall") !== !!TALL[screen];
    document.body.classList.toggle("centered", !!CENTERED[screen]);
    document.body.classList.toggle("tall", !!TALL[screen]);
    sceneFor(screen);
    renderStepper();
    // Fit now, from layout boxes (the entrance transforms do not count), so the screen
    // enters already in its final shape. A panel morph (side ↔ centred) changes the
    // screen's height over .85 s: that fit waits for the morph's transitionend (wire());
    // the timer only covers a morph that never ran (reduced motion) or was interrupted —
    // measured mid-morph, the fit would shrink a screen that fits once the panel has grown.
    if (!modeChange) fit();
    else refit(html.classList.contains("reduce-motion") ? 30 : 1400);
  }

  function goBack() { prevStep(); }

  // ------------------------------------------------------------------ install / uninstall
  var lastPoll = null;
  function startInstall(force) {
    S.stopping = false;
    S.lastPct = 0;
    show("progress", { replace: true });
    updateProgress({ percent: 0, phase: "preparing" });
    call("install", { dir: chosenDir(), components: S.components, tasks: S.tasks }, !!force)
      .then(function () { startPolling(); })
      .catch(function (e) { showError({ code: "unexpected", message: String(e && e.message || e) }); });
  }

  function startUninstall(force) {
    S.stopping = false;
    S.lastPct = 0;     // a retry after "programs are running" starts over, not at the stale percent
    show("progress", { replace: true });
    updateProgress({ percent: 0, phase: "closing" });
    S.removeData = !$("#opt-keep").checked;
    call("uninstall", S.removeData, !!force).then(function () { startPolling(); })
      .catch(function (e) { showError({ code: "unexpected", message: String(e && e.message || e) }); });
  }

  function startPolling() {
    clearInterval(S.polling);
    S.polling = setInterval(function () {
      call("poll").then(function (st) {
        lastPoll = st;
        updateProgress(st);
        if (st.status === "done") { stopPolling(); finished(st.result || {}); }
        else if (st.status === "error") { stopPolling(); showError(st.error || {}); }
        else if (st.status === "apps") { stopPolling(); showApps(st.apps || []); }
        else if (st.status === "cancelled") { stopPolling(); S.stopping = false; show(S.info.mode === "uninstall" ? "uninstall" : "review", { replace: true, back: true }); }
      }).catch(function () {});
    }, 110);
  }
  function stopPolling() { clearInterval(S.polling); S.polling = null; }

  function phaseText(phase) {
    if (!phase) return "";
    if (S.stopping) return t("install.cancelling");
    return S.info.mode === "uninstall" ? t("un.phase." + phase) : t("install.phase." + phase);
  }

  function updateProgress(st) {
    var pct = Math.max(S.lastPct, Math.min(100, Number(st.percent) || 0));
    S.lastPct = pct;
    $("#pct-num").textContent = String(Math.floor(pct));
    $("#bar-fill").style.width = pct.toFixed(2) + "%";
    var phase = st.phase || "";
    $("#phase").textContent = phaseText(phase);
    $("#file").textContent = st.file ? String(st.file).replace(/\//g, "\\") : "";
    $("#bytes").textContent = st.total ? t("install.of", { done: fmtBytes(st.done || 0), total: fmtBytes(st.total) }) : "";
    $("#btn-stop").hidden = S.info.mode === "uninstall" || phase === "registering" || phase === "finishing" || phase === "done";
    $("#btn-stop").disabled = S.stopping;            // already stopping: no second confirmation
    // Until the work has a size (programs are being closed, the payload is being opened) or
    // while it is being cancelled, the ring circles instead of sitting at an empty 0 %.
    var busy = S.stopping || !phase || phase === "preparing" || phase === "closing";
    html.classList.toggle("ring-busy", busy);
    var arc = $("#pring-arc");                       // the CSS fallback's ring (no WebGL)
    arc.style.strokeDashoffset = busy ? "" : (100 - pct).toFixed(2);
    arc.style.opacity = busy || pct > 0.2 ? "" : "0";
    var M = window.MemuScene;
    if (M && M.ok) {
      var p = pct / 100;
      M.setRing(p, null, busy);
      if (S.info.mode === "uninstall") {
        // the crystal dims and settles as the program goes, inside its ring
        var h = heroTarget("progress");
        M.setEnergy(0.35 * (1 - p) + 0.05); M.setTarget(h.x, h.y, h.s * (1 - 0.2 * p), 1 - 0.3 * p); M.setProgress(0);
      }
      else { M.setEnergy(0.35 + 0.65 * p); M.setProgress(p); }
    }
  }

  function finished(result) {
    S.result = result;
    if (S.info.mode === "uninstall") { renderUnDone(); show("un-done", { replace: true }); return; }
    var warns = (result.warnings || []);
    var webviewMissing = S.components.indexOf("memuai") >= 0 && result.webview2 === "";
    $("#done-warn").hidden = !(warns.length || webviewMissing);
    $("#done-warn-text").textContent = webviewMissing ? t("done.webview") : t("done.warn");
    // Without WebView2 the chat app cannot open: no "Open MemuAI" button that would fail.
    $("#btn-finish").hidden = webviewMissing;
    $("#btn-done-close").classList.toggle("primary", webviewMissing);
    $("#btn-done-close").classList.toggle("ghost", !webviewMissing);
    $("#btn-open-folder").hidden = !result.install_dir;
    show("done", { replace: true });
  }

  // What the uninstaller removes and what stays, as chips that follow the "keep data" switch.
  var CHIP_X = '<svg viewBox="0 0 16 16"><path d="M5 5l6 6M11 5l-6 6"/></svg>';
  var CHIP_OK = '<svg viewBox="0 0 16 16"><path d="M8 1.8l5 2v3.7c0 3-2.1 5.5-5 6.4-2.9-.9-5-3.4-5-6.4V3.8z"/><path d="M5.8 8l1.6 1.6 3-3.2"/></svg>';
  function renderUnChips() {
    var host = $("#un-chips");
    if (!host || !S.info) return;
    var keep = $("#opt-keep").checked;
    var items = [["files", false], ["shell", false]];
    if (!isProxyProduct()) items.push(["path", false]);
    items.push([keep ? "data.keep" : "data.remove", keep]);
    host.innerHTML = items.map(function (it) {
      return '<li class="chip ' + (it[1] ? "kept" : "gone") + '">' + (it[1] ? CHIP_OK : CHIP_X) + "<span>" + t("un.chip." + it[0]) + "</span></li>";
    }).join("");
  }

  function renderUnDone() {
    if (!S.info || S.info.mode !== "uninstall") return;
    var r = S.result || {};
    $("#un-done-title").innerHTML = t("un.done.title", { name: name() });
    var kept = r.kept_data || [];
    // The text follows what was asked: deleting data that did not exist is not «kept in …»,
    // and a folder that could not be deleted is not «deleted as well».
    $("#un-done-text").innerHTML = !S.removeData
      ? t("un.done.kept", { name: name(), paths: (kept.length ? kept : S.info.data_dirs || []).join(", ") })
      : kept.length ? t("un.done.dataLeft", { paths: kept.join(", ") }) : t("un.done.removed");
    $("#un-done-partial").hidden = !(r.failed && r.failed.length);
  }

  function showError(err) {
    var code = err.code || "unexpected";
    var known = I18N.en["error." + code] != null;
    $("#error-text").textContent = known ? t("error." + code) : t("error.lead");
    $("#error-detail").textContent = err.message && err.message !== code ? err.message : "";
    $("#btn-log").hidden = !(S.info && S.info.log);
    $("#error-log").hidden = !(S.info && S.info.log);
    if (S.info && S.info.log) $("#error-log").innerHTML = t("error.log", { path: S.info.log });
    show("error", { replace: true });
  }

  function showApps(apps) {
    modal(t("apps.title"), t("apps.lead"), apps, [
      { label: t("btn.cancel"), cls: "text", run: function () { closeModal(); show(S.info.mode === "uninstall" ? "uninstall" : "review", { replace: true, back: true }); } },
      { label: t("btn.forceClose"), cls: "ghost", run: function () { closeModal(); retry(true); } },
      { label: t("btn.retry"), cls: "primary", run: function () { closeModal(); retry(false); } }
    ]);
  }

  function retry(force) {
    if (S.info.mode === "uninstall") startUninstall(force); else startInstall(force);
  }

  function modal(title, text, list, actions) {
    $("#modal-title").textContent = title;
    $("#modal-text").textContent = text;
    $("#modal-list").innerHTML = (list || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("");
    var host = $("#modal-actions");
    host.innerHTML = "";
    actions.forEach(function (a) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "btn " + a.cls; b.textContent = a.label;
      b.addEventListener("click", a.run);
      host.appendChild(b);
    });
    $("#modal").hidden = false;
    var primary = host.querySelector(".primary");
    if (primary) setTimeout(function () { primary.focus(); }, 50);
  }
  function closeModal() { $("#modal").hidden = true; }

  function requestClose() {
    if (S.polling && S.info.mode !== "uninstall" && S.screen === "progress") {
      if (S.stopping) return;                // the rollback is already under way
      modal(t("install.confirmStop.t"), t("install.confirmStop.b"), [], [
        { label: t("btn.keepGoing"), cls: "ghost", run: closeModal },
        { label: t("btn.stop"), cls: "danger", run: function () { closeModal(); S.stopping = true; call("cancel"); } }
      ]);
      return;
    }
    if (S.polling) return;   // uninstall in progress: no half-removed program
    call("close");
  }


  // ------------------------------------------------------------------ window drag
  // The window is frameless: it moves when the titlebar or any empty area (the
  // background with the crystal, the panel's own surface) is pressed. Interactive
  // things (buttons, inputs, cards, rows, the language switch, dialogs) never start
  // a drag, and neither does text inside the panel or a scrollbar. The bridge runs
  // the native caption move loop (bridge.drag_window → WM_NCLBUTTONDOWN/HTCAPTION:
  // smooth, multi-monitor, Aero Snap; no maximize on double click — the form has no
  // maximize box). Without it (older pywebview, non-Windows) we move the window
  // ourselves with bridge.move_window while the button is held.
  var DRAG_BLOCK = "button, a, input, select, textarea, label, summary, option, [contenteditable], [tabindex], " +
    "[role=button], [role=checkbox], [role=radio], [role=switch], [role=tab], [role=slider], [role=dialog], " +
    ".seg, .card, .row, .no-drag, .modal";
  var DRAG_SURFACE = ".panel, .screens, .screen, .stepper, .steps, .step, .spacer, .actions, .buttons, .toggles, .tgroup, .links";
  function closestEl(el, sel) {
    while (el && el.nodeType === 1) { if (el.matches && el.matches(sel)) return el; el = el.parentElement; }
    return null;
  }
  function isDragZone(target, ev) {
    if (!target || target.nodeType !== 1) return false;
    if (closestEl(target, DRAG_BLOCK)) return false;
    if (ev && target.clientWidth && (ev.offsetX >= target.clientWidth || ev.offsetY >= target.clientHeight) &&
        (target.scrollHeight > target.clientHeight || target.scrollWidth > target.clientWidth)) return false;  // scrollbar
    if (closestEl(target, ".panel")) return target.matches(DRAG_SURFACE);   // text inside the panel stays inert
    return true;                                                             // titlebar, background, crystal side
  }
  var drag = { active: false, fallback: false, dx: 0, dy: 0, frame: 0, x: 0, y: 0 };
  function dragEnd() {
    drag.active = false; drag.fallback = false;
    if (drag.frame) { cancelAnimationFrame(drag.frame); drag.frame = 0; }
    html.classList.remove("dragging");
  }
  function dragMove(ev) {
    if (!drag.active || !drag.fallback) return;
    if (!(ev.buttons & 1)) { dragEnd(); return; }
    drag.x = ev.screenX - drag.dx; drag.y = ev.screenY - drag.dy;
    if (!drag.frame) drag.frame = requestAnimationFrame(function () {
      drag.frame = 0;
      if (drag.active) call("move_window", drag.x, drag.y).catch(function () {});
    });
  }
  function dragStart(ev) {
    if (ev.button !== 0 || ev.detail > 1 || ev.ctrlKey || ev.altKey || ev.shiftKey || ev.metaKey) return;
    if (!isDragZone(ev.target, ev)) return;
    ev.preventDefault();                    // no focus change, no selection
    drag.active = true; drag.fallback = false;
    drag.dx = ev.clientX; drag.dy = ev.clientY;
    html.classList.add("dragging");
    call("drag_window").then(function (native) {
      if (native || !drag.active) { dragEnd(); return; }   // native loop done, or button already released
      drag.fallback = true;
    }, function () { if (drag.active) drag.fallback = true; else dragEnd(); });
  }
  function wireDrag() {
    document.addEventListener("mousedown", dragStart);
    window.addEventListener("mousemove", dragMove);
    window.addEventListener("mouseup", dragEnd);
    window.addEventListener("blur", dragEnd);
    document.addEventListener("dblclick", function (ev) { if (isDragZone(ev.target, null)) ev.preventDefault(); });
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    wireDrag();
    $$(".seg button").forEach(function (b) {
      b.addEventListener("click", function () {
        S.lang = b.getAttribute("data-lang");
        call("set_language", S.lang).catch(function () {});
        applyI18n();
      });
    });
    $("#btn-min").addEventListener("click", function () { call("minimize"); });
    $("#btn-close").addEventListener("click", requestClose);
    $("#btn-start").addEventListener("click", startWizard);
    $("#btn-express").addEventListener("click", expressSetup);
    $$("[data-back]").forEach(function (b) { b.addEventListener("click", goBack); });
    $$("[data-next]").forEach(function (b) { b.addEventListener("click", nextStep); });
    $$(".card").forEach(function (c) {
      c.addEventListener("click", function () { setEdition(c.getAttribute("data-edition")); });
      c.addEventListener("mousemove", function (e) {
        var r = c.getBoundingClientRect();
        var x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
        c.style.transform = "rotateY(" + (x * 10).toFixed(2) + "deg) rotateX(" + (-y * 10).toFixed(2) + "deg) translateZ(4px)";
      });
      c.addEventListener("mouseleave", function () { c.style.transform = ""; });
    });
    $("#opt-memucode").addEventListener("change", function () { setEdition(S.edition); });
    if ($("#opt-memunote")) $("#opt-memunote").addEventListener("change", function () { setEdition(S.edition); });
    if ($("#opt-memufiles")) $("#opt-memufiles").addEventListener("change", function () { setEdition(S.edition); });
    $("#opt-dir").addEventListener("input", function () { renderSpace(); checkDir(); });
    $("#btn-dir-reset").addEventListener("click", function () {
      $("#opt-dir").value = S.info.defaults.dir || S.info.default_dir || "";
      renderSpace(); checkDir();
    });
    $("#btn-browse").addEventListener("click", function () {
      call("browse", $("#opt-dir").value).then(function (path) {
        if (path) { $("#opt-dir").value = path; checkDir(); }
      }).catch(function () {});
    });
    $("#btn-install").addEventListener("click", function () { startInstall(false); });
    $("#btn-stop").addEventListener("click", requestClose);
    $("#btn-finish").addEventListener("click", function () {
      call("launch").catch(function () {}).then(function () { call("close"); });
    });
    $("#btn-done-close").addEventListener("click", function () { call("close"); });
    $("#btn-open-folder").addEventListener("click", function () {
      call("open_folder", (S.result && S.result.install_dir) || "").catch(function () {});
    });
    $("#btn-retry").addEventListener("click", function () { S.info.mode === "uninstall" ? show("uninstall", { replace: true }) : show("review", { replace: true }); });
    $("#btn-error-close").addEventListener("click", function () { call("close"); });
    $("#btn-log").addEventListener("click", function () { call("open_log"); });
    $("#opt-keep").addEventListener("change", function () {
      $("#un-warn").hidden = $("#opt-keep").checked;
      renderUnChips();
      fit();     // the warning adds height: without a refit the buttons slid off the panel on a small window
    });
    $("#btn-un-cancel").addEventListener("click", function () { call("close"); });
    $("#btn-un-go").addEventListener("click", function () { startUninstall(false); });
    $("#btn-un-close").addEventListener("click", function () { call("close"); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("#modal").hidden) { closeModal(); return; }
      if (e.key === "Escape" && WIZARD[S.screen] && !(e.target && e.target.tagName === "INPUT")) { prevStep(); return; }
      if (e.key === "Enter" && e.target && e.target.tagName !== "BUTTON" && $("#modal").hidden) {
        var primary = $(".screen.active .btn.primary:not([disabled]):not([hidden]), .screen.active .btn.danger");
        if (primary) primary.click();
      }
    });
    // light that follows the cursor across the glass
    var panel = $("#panel");
    document.addEventListener("mousemove", function (e) {
      var r = panel.getBoundingClientRect();
      panel.style.setProperty("--mx", ((e.clientX - r.left) / r.width * 100).toFixed(1) + "%");
      panel.style.setProperty("--my", ((e.clientY - r.top) / r.height * 100).toFixed(1) + "%");
    });
    document.addEventListener("contextmenu", function (e) { if (!(e.target && e.target.tagName === "INPUT")) e.preventDefault(); });
    window.addEventListener("resize", function () { fit(); refit(150); });
    panel.addEventListener("transitionend", function (e) { if (e.target === panel && e.propertyName === "height") fit(); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { if (S.screen) fit(); });
  }

  // ------------------------------------------------------------------ start
  // The fonts of the interface, both subsets (Latin + Cyrillic), before the first layout:
  // with font-display: block a late font re-flows every line after the entrance. Capped,
  // so a broken font file never holds the window back.
  function fontsReady() {
    var fonts = document.fonts;
    if (!fonts || !fonts.load) return Promise.resolve();
    var sample = "MemuAI Setup Установка 0123";
    var load = Promise.all(['400 16px "Setup Sans"', '500 12px "Setup Mono"'].map(function (f) { return fonts.load(f, sample); }));
    return Promise.race([load.catch(function () {}), new Promise(function (r) { setTimeout(r, 1500); })]);
  }

  // The interface stays hidden (html:not(.ready), style.css) until the bridge has answered,
  // the fonts are in and the first screen is laid out in its final shape; then it enters
  // once. Before Round 25 the panel animated in empty at page load and the texts, the
  // language switch and the fit arrived afterwards.
  var revealed = false;
  function reveal() {
    if (revealed) return;
    revealed = true;
    requestAnimationFrame(function () { html.classList.add("ready"); });
  }

  function start() {
    call("init").then(function (info) {
      return fontsReady().then(function () { return info; });
    }).then(function (info) {
      S.info = info;
      S.lang = info.lang === "ru" ? "ru" : "en";
      html.setAttribute("data-mode", info.mode);
      html.setAttribute("data-product", info.product.id);
      if (info.product.id === "proxy") {
        $$(".brand-mark, .css-star img").forEach(function (img) { img.src = "assets/logo-proxy.png"; });
        if (window.MemuScene && window.MemuScene.ok) window.MemuScene.setPalette(0.6);
      }
      S.components = (info.defaults.components || []).slice();
      S.tasks = (info.defaults.tasks || []).slice();
      S.edition = S.components.indexOf("memuai") >= 0 || info.product.id === "proxy" ? "full" : "proxy";
      $("#opt-memucode").checked = S.components.indexOf("memucode") >= 0;
      // Round 29: MemuNote is optional; hidden when this Setup does not carry it.
      var hasNote = (info.components || []).some(function (c) { return c.id === "memunote"; });
      $("#row-note").hidden = !hasNote;
      $("#opt-memunote").checked = S.components.indexOf("memunote") >= 0;
      // Round 35: MemuFiles, optional the same way.
      var hasFiles = (info.components || []).some(function (c) { return c.id === "memufiles"; });
      $("#row-files").hidden = !hasFiles;
      $("#opt-memufiles").checked = S.components.indexOf("memufiles") >= 0;
      $("#opt-dir").value = info.defaults.dir || info.default_dir || "";
      $$(".card").forEach(function (c) { c.setAttribute("aria-pressed", String(c.getAttribute("data-edition") === S.edition)); });
      $("#row-cli").classList.toggle("disabled", S.edition !== "full");
      $("#row-note").classList.toggle("disabled", S.edition !== "full");
      $("#row-files").classList.toggle("disabled", S.edition !== "full");
      applyI18n();
      checkDir();
      show(info.mode === "uninstall" ? "uninstall" : "welcome", { replace: true });
      reveal();
      if (window.__memuSetupReady) window.__memuSetupReady(S, { show: show, startInstall: startInstall, showApps: showApps, showError: showError, finished: finished, updateProgress: updateProgress, setEdition: setEdition, isDragZone: isDragZone,
        startWizard: startWizard, expressSetup: expressSetup, nextStep: nextStep, prevStep: prevStep, editStep: editStep, flowSteps: flowSteps });
    }).catch(function (e) {
      if (window.console) console.error(e);
      reveal();
    });
  }

  function boot() {
    fit();
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || /[?&]shot=1/.test(location.search)) html.classList.add("reduce-motion");
    var ok = false;
    try { ok = window.MemuScene && window.MemuScene.init($("#scene")); } catch (e) { ok = false; }
    if (!ok) html.classList.add("no-webgl");
    wire();
    setTimeout(reveal, 8000);     // a bridge that never answers must not leave an empty window
    var useMock = /[?&]mock=1/.test(location.search);
    if (useMock) {
      var s = document.createElement("script");
      s.src = "mock.js";
      s.onload = function () { api = window.MemuMockApi; start(); };
      document.body.appendChild(s);
      return;
    }
    var ready = function () { if (api) return; api = window.pywebview && window.pywebview.api; if (api) start(); };
    window.addEventListener("pywebviewready", ready);
    if (window.pywebview && window.pywebview.api) ready();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
