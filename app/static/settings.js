/* MemuAI Proxy — the Settings page (Round 23 redesign).

   Layout: a section rail on the left, one section on screen at a time; every section shows the
   everyday options first and hides the rest under «Дополнительно». Controls stay the ones app.js
   already saves ([data-setting], [data-psetting], #search-*, keys, password): this file only adds
   the shell around them — navigation, search, segmented controls over hidden <select>s, the theme,
   "restore default" buttons, the save status and per-row feedback. No API calls of its own. */
"use strict";

window.MemuSettings = (() => {
  const SECTIONS = ["general", "routing", "providers", "search", "access", "network", "cli",
    "reliability", "logs", "system"];
  /* Defaults of app/config.py Settings (tests/proxy_settings_ui_test.py keeps them in sync). */
  const DEFAULTS = {
    open_ui_on_start: true, close_to_tray: true, balancing: "round-robin", cookie_check_interval: 30,
    use_prefixes: true, strict_models: false, host: "127.0.0.1", port: 8317, cors_enabled: true,
    default_proxy: "", verify_ssl: true, first_byte_timeout: 90, max_retry_passes: 2, retry_delay_ms: 1500,
    request_timeout: 180, stream_idle_timeout: 300, sse_keepalive: true, sse_keepalive_interval: 15,
    search_results: 5, log_requests: true, log_persist: true, log_payloads: true, metrics_enabled: true,
  };
  /* Places outside this page that search can lead to. */
  const ELSEWHERE = [
    { id: "public", tab: "public", k: "публичный доступ интернет туннель tunnel ngrok cloudflare tailscale https сертификат certificate внешний ip домен domain порт port лимиты limits ключи доступа брандмауэр firewall public internet" },
    { id: "local", tab: "local", k: "локальные модели llama gguf hugging face hf токен token потоки threads движок engine cuda vulkan cpu память memory local models" },
    { id: "accounts", tab: "accounts", k: "аккаунты accounts личный прокси аккаунта per-account proxy приоритет резерв reserve куки cookie проверить check порядок order" },
    { id: "detector", tab: "detector", k: "детектор detector судья judge проба probe история history подмена модели" },
    { id: "companion", tab: "companion", k: "помощник companion ии-помощник assistant модель помощника" },
    { id: "home", tab: "home", k: "главная home выключатель api switch остановить stop статистика период график chart proxy_enabled" },
    { id: "models", tab: "models", k: "каталог моделей models catalog модели включить выключить enable disable значки badges возможности capabilities зрение vision инструменты tools обновить refresh метаданные metadata openrouter models.dev экспорт импорт export import" },
    { id: "cli", sec: "cli", k: "claude code codex cli интеграции integrations терминал terminal anthropic слоты slots" },
  ];
  const S = { sec: "", query: "", opened: new Set(), changedRow: null, statusTimer: 0, searchTimer: 0, wired: false };
  const norm = (value) => String(value || "").toLowerCase().replace(/ё/g, "е");
  const shell = () => document.getElementById("sg-shell");

  /* ---------------------------------------------------------------- navigation */
  function current() {
    let saved = "";
    try { saved = localStorage.getItem("oh_settings_sec") || ""; } catch (e) { /* ignore */ }
    return SECTIONS.includes(S.sec) ? S.sec : SECTIONS.includes(saved) ? saved : "general";
  }
  function select(sec, opts = {}) {
    if (!SECTIONS.includes(sec)) sec = "general";
    S.sec = sec;
    try { localStorage.setItem("oh_settings_sec", sec); } catch (e) { /* ignore */ }
    if (S.query && !opts.keepSearch) clearSearch(false);
    document.querySelectorAll("#sg-nav [data-sg-nav]").forEach((button) => {
      const on = button.dataset.sgNav === sec;
      button.classList.toggle("active", on);
      if (on) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    if (!S.query) {
      document.querySelectorAll("#sg-body .sg-sec").forEach((node) => { node.hidden = node.dataset.sec !== sec; });
    }
    syncWide();
    if (opts.scroll !== false) {
      const head = document.querySelector(".sg-head");
      const top = head ? head.getBoundingClientRect().top + window.scrollY - 70 : 0;
      if (window.scrollY > top) window.scrollTo({ top: Math.max(0, top), behavior: "auto" });
    }
  }

  /* The model catalog needs the width of the page: its rail folds to icons (with tooltips). */
  function syncWide() {
    const box = shell();
    if (!box) return;
    box.classList.toggle("is-wide", S.sec === "models" && !S.query);
    document.getElementById("tab-settings")?.classList.toggle("sg-is-wide", box.classList.contains("is-wide"));
    document.querySelectorAll("#sg-nav [data-sg-nav]").forEach((button) => {
      button.title = box.classList.contains("is-wide") ? button.textContent.trim() : "";
    });
  }

  /* ---------------------------------------------------------------- segmented controls */
  function syncSegments(root = document) {
    root.querySelectorAll(".sg-seg[data-for]").forEach((seg) => {
      const source = document.getElementById(seg.dataset.for);
      if (!source) return;
      const label = seg.closest(".sg-row")?.querySelector(".sg-label");
      if (label) seg.setAttribute("aria-label", label.textContent.trim());
      seg.querySelectorAll("[data-v]").forEach((button) => {
        const on = button.dataset.v === source.value;
        button.classList.toggle("active", on);
        button.setAttribute("aria-checked", String(on));
        button.tabIndex = on ? 0 : -1;
      });
    });
  }
  function onSegClick(event) {
    const button = event.target.closest(".sg-seg [data-v]");
    if (!button) return;
    const seg = button.closest(".sg-seg");
    const source = document.getElementById(seg.dataset.for);
    if (!source || source.value === button.dataset.v) return;
    source.value = button.dataset.v;
    source.dispatchEvent(new Event("change", { bubbles: true }));
    syncSegments(seg.parentNode);
  }
  function onSegKey(event) {
    const button = event.target.closest(".sg-seg [data-v]");
    if (!button || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    const all = Array.from(button.parentNode.querySelectorAll("[data-v]"));
    let index = all.indexOf(button);
    if (event.key === "Home") index = 0;
    else if (event.key === "End") index = all.length - 1;
    else index = (index + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) + all.length) % all.length;
    event.preventDefault();
    all[index].focus();
    all[index].click();
  }

  /* ---------------------------------------------------------------- value-dependent bits */
  function syncDependent() {
    const bal = document.getElementById("set-balancing");
    const balDesc = document.getElementById("sg-bal-desc");
    if (bal && balDesc) {
      const code = { "round-robin": "rr", order: "order", "first-available": "fa", "least-used": "lu" }[bal.value] || "rr";
      balDesc.textContent = t(`sg.bal.${code}.d`);
    }
    const keep = document.getElementById("set-keepalive");
    const keepInt = document.getElementById("set-keepaliveint");
    if (keep && keepInt) {
      /* A setting that only matters when another is on stays out of sight until then (as in the app's kit). */
      const row = keepInt.closest(".sg-row");
      if (row) row.hidden = !keep.checked;
    }
    const host = document.getElementById("set-host");
    const mode = document.getElementById("set-host-mode");
    if (host && mode) {
      const value = host.value.trim();
      const known = value === "127.0.0.1" || value === "0.0.0.0" || value === "localhost";
      if (!mode.dataset.custom) mode.value = !known ? "custom" : value === "0.0.0.0" ? "0.0.0.0" : "127.0.0.1";
      document.getElementById("sg-host-custom").hidden = mode.value !== "custom";
      const desc = document.getElementById("sg-host-desc");
      if (desc) desc.textContent = t(mode.value === "custom" ? "sg.host.custom.d" : mode.value === "0.0.0.0" ? "sg.host.lan.d" : "sg.host.local.d");
    }
    const theme = document.getElementById("sg-theme");
    if (theme && window.MemuTheme) theme.value = MemuTheme.get();
    const lang = document.getElementById("lang-select-2");
    if (lang && typeof LANG !== "undefined") lang.value = LANG;
    const topLang = document.getElementById("lang-select");
    if (topLang && typeof LANG !== "undefined") topLang.value = LANG;
    const pwd = document.getElementById("sg-pwd-default");
    if (pwd && typeof settingsCache !== "undefined") pwd.hidden = !settingsCache.admin_password_is_default;
    syncSegments();
    syncDefaults();
  }
  function onHostMode(event) {
    const mode = event.target;
    const host = document.getElementById("set-host");
    if (mode.value === "custom") {
      mode.dataset.custom = "1";
      document.getElementById("sg-host-custom").hidden = false;
      syncDependent();
      host.focus();
      host.select();
      return;
    }
    delete mode.dataset.custom;
    if (mode.value === "0.0.0.0" && host.value.trim() !== "0.0.0.0") {
      /* Opening the API to the network is a security decision: ask first, change nothing on "Cancel". */
      syncDependent();
      confirmDialog(t("sg.lan.title"), t("sg.lan.text"), () => {
        host.value = "0.0.0.0";
        host.dispatchEvent(new Event("change", { bubbles: true }));
        syncDependent();
      }, t("sg.lan.yes"));
      return;
    }
    if (host.value !== mode.value) {
      host.value = mode.value;
      host.dispatchEvent(new Event("change", { bubbles: true }));
    }
    syncDependent();
  }

  /* ---------------------------------------------------------------- "restore default" */
  function valueOf(el) {
    if (el.type === "checkbox") return el.checked;
    if (el.type === "number") return el.value === "" ? "" : Number(el.value);
    if (el.tagName === "SELECT" && (el.value === "true" || el.value === "false")) return el.value === "true";
    return el.value;
  }
  function syncDefaults() {
    document.querySelectorAll("#sg-body [data-setting]").forEach((el) => {
      const key = el.dataset.setting;
      if (!(key in DEFAULTS)) return;
      const row = el.closest(".sg-row");
      if (!row) return;
      let button = row.querySelector(".sg-reset");
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.className = "sg-reset";
        button.dataset.sgReset = key;
        button.innerHTML = '<svg class="i" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/></svg>';
        row.querySelector(".sg-ctl").prepend(button);
      }
      const def = DEFAULTS[key];
      const modified = valueOf(el) !== def && !(el.type === "number" && el.value === "");
      row.classList.toggle("is-modified", modified);
      button.hidden = !modified;
      const shown = typeof def === "boolean" ? t(def ? "common.yes" : "common.no") : def === "" ? "—" : String(def);
      button.title = `${t("sg.default")} (${t("sg.default.was").toLowerCase()}: ${shown})`;
      button.setAttribute("aria-label", button.title);
    });
  }
  function onReset(button) {
    const key = button.dataset.sgReset;
    const el = document.querySelector(`#sg-body [data-setting="${key}"]`);
    if (!el || !(key in DEFAULTS)) return;
    const def = DEFAULTS[key];
    if (el.type === "checkbox") el.checked = !!def;
    else el.value = String(def);
    if (key === "host") delete document.getElementById("set-host-mode")?.dataset.custom;
    el.dispatchEvent(new Event("change", { bubbles: true }));
    syncDependent();
  }

  /* ---------------------------------------------------------------- save status */
  function setStatus(state, message) {
    const box = document.getElementById("settings-status");
    const text = document.getElementById("sg-status-text");
    if (!box || !text) return;
    clearTimeout(S.statusTimer);
    box.dataset.state = state;
    text.textContent = message ? `${t("sg.status." + state)}: ${message}` : t("sg.status." + state);
    box.title = message || "";
    if (state === "saved") {
      S.statusTimer = setTimeout(() => { box.dataset.state = "idle"; text.textContent = t("sg.status.idle"); }, 2200);
    }
  }
  function saving() { setStatus("saving"); }
  function saved(ok, message) {
    setStatus(ok ? "saved" : "error", ok ? "" : message);
    const row = S.changedRow;
    S.changedRow = null;
    if (row && ok) {
      row.classList.remove("sg-flash");
      void row.offsetWidth;
      row.classList.add("sg-flash");
      setTimeout(() => row.classList.remove("sg-flash"), 1300);
    }
    syncDependent();
  }

  /* ---------------------------------------------------------------- search */
  function rowText(row) {
    if (!row._sgText || row._sgLang !== LANG) {
      row._sgText = norm(`${row.textContent} ${row.dataset.k || ""}`);
      row._sgLang = LANG;
    }
    return row._sgText;
  }
  /* A word matches the start of a word ("ключ" finds «ключи», «ключей»; "порт" does not find «импорт»). */
  const WORD = /[0-9a-zа-я]/;
  function wordAt(text, word, from = 0) {
    for (let at = text.indexOf(word, from); at >= 0; at = text.indexOf(word, at + 1)) {
      if (at === 0 || !WORD.test(text[at - 1])) return at;
    }
    return -1;
  }
  function matches(text, words) { return words.every((word) => wordAt(text, word) >= 0); }
  function clearHighlights() { if (window.CSS && CSS.highlights) CSS.highlights.delete("sg-hit"); }
  function highlight(words) {
    clearHighlights();
    if (!words.length || !(window.CSS && CSS.highlights) || typeof Highlight !== "function") return;
    const ranges = [];
    document.querySelectorAll("#sg-body .sg-sec:not([hidden]) .sg-row:not([hidden]) :is(.sg-label, .sg-desc), #sg-body .sg-sec:not([hidden]) .sg-sec-head h3").forEach((el) => {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = norm(node.nodeValue);
        for (const word of words) {
          let at = wordAt(text, word);
          while (at >= 0 && ranges.length < 400) {
            const range = new Range();
            range.setStart(node, at);
            range.setEnd(node, at + word.length);
            ranges.push(range);
            at = wordAt(text, word, at + word.length);
          }
        }
      }
    });
    if (ranges.length) CSS.highlights.set("sg-hit", new Highlight(...ranges));
  }
  function restoreOpened() {
    S.opened.forEach((details) => { details.open = false; });
    S.opened.clear();
  }
  function runSearch() {
    const box = shell();
    if (!box) return;
    const words = norm(S.query).split(/\s+/).filter(Boolean);
    restoreOpened();
    if (!words.length) {
      box.classList.remove("searching");
      document.querySelectorAll("#sg-body .sg-row, #sg-body .sg-group, #sg-body details.sg-more").forEach((node) => { node.classList.remove("sg-miss"); });
      document.querySelectorAll("#sg-nav [data-hits]").forEach((node) => { delete node.dataset.hits; });
      document.getElementById("sg-found").hidden = true;
      document.getElementById("sg-empty").hidden = true;
      clearHighlights();
      select(current(), { scroll: false, keepSearch: true });
      syncDependent();
      return;
    }
    box.classList.add("searching");
    syncWide();
    let total = 0;
    document.querySelectorAll("#sg-body .sg-sec").forEach((sec) => {
      if (sec.dataset.sec === "models" || sec.dataset.sec === "cli") { sec.hidden = true; return; }
      const head = sec.querySelector(".sg-sec-head");
      const secHit = matches(norm(head ? head.textContent : ""), words);
      let shown = 0;
      sec.querySelectorAll(".sg-group").forEach((group) => {
        let groupShown = 0;
        const groupHit = secHit || matches(norm(group.querySelector(":scope > .sg-gtitle, :scope > summary")?.textContent || ""), words);
        group.querySelectorAll(".sg-row").forEach((row) => {
          const hit = groupHit || matches(rowText(row), words);
          row.classList.toggle("sg-miss", !hit);
          if (hit && !row.hidden) groupShown++;     /* rows hidden by state (a key field) stay hidden */
        });
        group.classList.toggle("sg-miss", !groupShown);
        shown += groupShown;
      });
      /* «Дополнительно» opens by itself when the match is inside, and disappears when nothing is. */
      sec.querySelectorAll("details.sg-more").forEach((details) => {
        const has = !!details.querySelector(".sg-row:not(.sg-miss):not([hidden])");
        details.classList.toggle("sg-miss", !has);
        if (has && !details.open) { details.open = true; S.opened.add(details); }
      });
      sec.hidden = !shown;
      total += shown;
      const nav = document.querySelector(`#sg-nav [data-sg-nav="${sec.dataset.sec}"]`);
      if (nav) { if (shown) nav.dataset.hits = String(shown); else delete nav.dataset.hits; }
    });
    const found = ELSEWHERE.filter((item) => matches(norm(`${t("sg.jump." + item.id)} ${t("sg.jump." + item.id + ".d")} ${item.k}`), words));
    /* A model name finds the model in the catalog. */
    const raw = norm(S.query);
    const modelHits = raw.length >= 2 && typeof modelsCache !== "undefined" ? modelsCache.filter((m) => norm(m.id).includes(raw)).length : 0;
    const list = document.getElementById("sg-found-list");
    list.innerHTML = (modelHits ? `<button type="button" class="sg-found-item" data-sg-models="1">
      <b>${esc(t("sg.jump.modelhits"))}</b><span>${esc(t("sg.jump.modelhits.d").replace("{n}", modelHits))}</span>
      <svg class="i" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>` : "") + found.map((item) => `<button type="button" class="sg-found-item" data-sg-go="${esc(item.tab || "")}" data-sg-sec="${esc(item.sec || "")}">
      <b>${esc(t("sg.jump." + item.id))}</b><span>${esc(t("sg.jump." + item.id + ".d"))}</span>
      <svg class="i" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>`).join("");
    document.getElementById("sg-found").hidden = !found.length && !modelHits;
    document.getElementById("sg-empty").hidden = total > 0 || found.length > 0 || modelHits > 0;
    highlight(words);
  }
  function clearSearch(rerun = true) {
    const input = document.getElementById("sg-search");
    if (input) input.value = "";
    S.query = "";
    if (rerun) runSearch();
    else {
      restoreOpened();
      shell()?.classList.remove("searching");
      document.querySelectorAll("#sg-body .sg-miss").forEach((node) => node.classList.remove("sg-miss"));
      document.querySelectorAll("#sg-nav [data-hits]").forEach((node) => { delete node.dataset.hits; });
      const found = document.getElementById("sg-found"); if (found) found.hidden = true;
      const empty = document.getElementById("sg-empty"); if (empty) empty.hidden = true;
      clearHighlights();
    }
  }
  function goTab(tab) {
    if (tab === "companion") { document.getElementById("companion-open")?.click(); return; }
    document.querySelector(`.nav-item[data-tab="${tab}"]`)?.click();
  }

  /* ---------------------------------------------------------------- wiring */
  function wire() {
    if (S.wired) return;
    const page = document.getElementById("tab-settings");
    if (!page) return;
    S.wired = true;
    document.getElementById("sg-nav").addEventListener("click", (event) => {
      const button = event.target.closest("[data-sg-nav]");
      if (button) select(button.dataset.sgNav);
    });
    page.addEventListener("click", (event) => {
      onSegClick(event);
      const reset = event.target.closest("[data-sg-reset]");
      if (reset) { S.changedRow = reset.closest(".sg-row"); onReset(reset); return; }
      const jump = event.target.closest("[data-sg-jump]");
      if (jump) { goTab(jump.dataset.sgJump); return; }
      const proxyClick = event.target.closest("[data-sg-click]");
      if (proxyClick) { document.querySelector(proxyClick.dataset.sgClick)?.click(); return; }
      const modelsGo = event.target.closest("[data-sg-models]");
      if (modelsGo) {
        const query = S.query;
        clearSearch(false);
        goTab("models");   /* r33: the catalog is its own tab in the left rail now */
        const filter = document.getElementById("models-filter");
        if (filter) { filter.value = query; filter.dispatchEvent(new Event("input", { bubbles: true })); filter.focus(); }
        return;
      }
      const go = event.target.closest("[data-sg-go], [data-sg-sec]");
      if (go && go.classList.contains("sg-found-item")) {
        const sec = go.dataset.sgSec;
        if (sec) { clearSearch(false); select(sec); }
        else goTab(go.dataset.sgGo);
      }
    });
    page.addEventListener("keydown", onSegKey);
    /* Remember which row changed, so the "saved" answer can light it up. */
    page.addEventListener("change", (event) => {
      const row = event.target.closest(".sg-row");
      if (row) S.changedRow = row;
      if ((event.target.id === "sg-theme" && window.MemuTheme) || event.target.id === "lang-select-2") {
        /* Saved in this browser only: no request, so the answer is immediate. */
        if (event.target.id === "sg-theme") MemuTheme.set(event.target.value);
        setTimeout(() => setStatus("saved"), 0);     /* after a language switch has relabelled the page */
        S.changedRow = null;
        row?.classList.add("sg-flash");
        setTimeout(() => row?.classList.remove("sg-flash"), 1300);
      }
      if (event.target.id === "set-host-mode") onHostMode(event);
      if (event.target.id === "set-balancing" || event.target.id === "set-keepalive") syncDependent();
    }, true);
    const toggle = document.getElementById("sg-pwd-toggle");
    toggle.addEventListener("click", () => {
      const row = document.getElementById("sg-pwd-row");
      row.hidden = !row.hidden;
      toggle.setAttribute("aria-expanded", String(!row.hidden));
      toggle.textContent = t(row.hidden ? "sg.pwd.btn" : "sg.pwd.hide");
      toggle.dataset.i18n = row.hidden ? "sg.pwd.btn" : "sg.pwd.hide";
      if (!row.hidden) document.getElementById("pwd-old").focus();
    });
    const input = document.getElementById("sg-search");
    input.addEventListener("input", () => {
      clearTimeout(S.searchTimer);
      S.searchTimer = setTimeout(() => { S.query = input.value.trim(); runSearch(); }, 70);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && input.value) { event.preventDefault(); event.stopPropagation(); clearSearch(); }
      if (event.key === "Enter") {
        const first = document.querySelector("#sg-body .sg-sec:not([hidden]) .sg-row:not(.sg-miss):not([hidden]) :is(input:not([type=hidden]), select:not([hidden]), button, .sg-seg [data-v].active)");
        if (first) { event.preventDefault(); first.focus(); }
      }
    });
    document.addEventListener("keydown", (event) => {
      if (!page.classList.contains("active")) return;
      if (document.getElementById("modal-back")?.classList.contains("visible")) return;
      const typing = event.target.closest && event.target.closest("input, textarea, select, [contenteditable]");
      const find = ((window.MemuKeys ? MemuKeys.latin(event) : event.key) === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey)
        || ((event.ctrlKey || event.metaKey) && !event.shiftKey && (event.key === "f" || event.key === "F"));
      if (find) { event.preventDefault(); input.focus(); input.select(); }
    });
    applyHtml();
    setStatus("idle");
  }

  function applyHtml() {
    document.querySelectorAll("#tab-settings [data-i18n-html]").forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  }

  /* Called when the Settings tab opens. */
  function show() {
    wire();
    if (!S.query) select(current(), { scroll: false });
    syncDependent();
  }
  /* Called by app.js after loadSettings() wrote the values into the controls. */
  function sync() {
    wire();
    syncDependent();
    if (S.query) runSearch();
  }
  function relabel() {
    applyHtml();
    const box = document.getElementById("settings-status");
    if (box) setStatus(box.dataset.state === "error" ? "idle" : box.dataset.state || "idle");
    document.querySelectorAll("#sg-body .sg-row").forEach((row) => { row._sgText = ""; });
    syncDependent();
    if (S.query) runSearch();
  }

  wire();
  return { show, sync, relabel, saving, saved, select, search(q) { const i = document.getElementById("sg-search"); i.value = q; S.query = q.trim(); runSearch(); }, DEFAULTS };
})();
