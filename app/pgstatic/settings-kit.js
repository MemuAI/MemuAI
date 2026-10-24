/* MemuAI · Settings kit and shell runtime (Round 23).

   1. Component helpers (window.MemuSettingsKit / window.SX): small functions that
      return HTML strings for rows, sections, toggles, selects, segmented controls,
      sliders, fields, "Дополнительно" disclosures, sub-pages, notes and badges.
      Every id passed in is written literally (id="…"), callers bind by id.
   2. Shell runtime: navigation between pages and sub-pages with breadcrumbs,
      global fuzzy search (ru/en, keyboard-layout aware) that jumps to and
      highlights a setting, `when` dependencies, per-setting and per-section
      reset to defaults, the «Сохранено» indicator and keyboard navigation.

   The page markup itself is produced by app.js (renderSettingsPanel); this file
   never owns preference state — resets only set a control and dispatch the same
   input/change events a user would, so the existing handlers save it.
   Documented for other agents in round23/SETTINGS_KIT.md. */
(function () {
  "use strict";

  const SVG = {
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/></svg>',
    chev: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>',
    reset: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/></svg>',
    eye: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    danger: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
  };

  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) {}
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const attr = (name, value) => (value === undefined || value === null || value === "" || value === false ? "" : ` ${name}="${esc(value === true ? "" : value)}"`);
  const json = (value) => { try { return JSON.stringify(value); } catch (e) { return ""; } };

  /* ------------------------------------------------------------ helpers */

  function badge(kind) {
    if (!kind) return "";
    const k = String(kind).toLowerCase();
    if (k === "new") return `<span class="sx-badge sx-badge--new">${L("Новое", "New")}</span>`;
    if (k === "beta") return `<span class="sx-badge sx-badge--beta">Beta</span>`;
    return `<span class="sx-badge">${esc(kind)}</span>`;
  }

  function chip(text, tone = "muted", extra = "") {
    return `<span class="sx-chip" data-tone="${esc(tone)}"${extra}><i aria-hidden="true"></i>${esc(text)}</span>`;
  }

  function helpTip(text) {
    if (!text) return "";
    return `<span class="sx-help-tip" tabindex="0" role="note" aria-label="${esc(text)}">${SVG.info}<span class="sx-help-pop">${esc(text)}</span></span>`;
  }

  function resetButton() {
    return `<button type="button" class="sx-reset" data-sx-reset title="${esc(L("Вернуть значение по умолчанию", "Reset to default"))}" aria-label="${esc(L("Вернуть значение по умолчанию", "Reset to default"))}">${SVG.reset}</button>`;
  }

  function row(o = {}) {
    const key = o.key ? attr("data-sx-key", o.key) + ` data-sx-default='${esc(json(o.def))}'` : "";
    const cls = ["sx-row", "p-toggle", o.stack ? "sx-row--stack" : "", o.tone ? `sx-row--${o.tone}` : "", o.cls || ""].filter(Boolean).join(" ");
    return `<div class="${cls}"${attr("id", o.id)}${key}${attr("data-sx-k", o.keywords)}${attr("data-sx-when", o.when)}${o.attrs ? " " + o.attrs : ""}>
      <div class="sx-row-text"><div class="sx-label tl">${o.label || ""}${badge(o.badge)}${helpTip(o.help)}${o.key ? resetButton() : ""}</div>${o.desc ? `<div class="sx-desc td">${o.desc}</div>` : ""}${o.extra || ""}</div>
      ${o.control !== undefined && o.control !== "" ? `<div class="sx-control">${o.control}</div>` : ""}
    </div>`;
  }

  function section(o = {}) {
    const head = o.title || o.desc ? `<header class="sx-section-head">
        <h3 class="sx-section-title">${o.icon ? `<span class="sx-section-icon" aria-hidden="true">${o.icon}</span>` : ""}<span>${o.title || ""}</span>${badge(o.badge)}${o.reset ? `<button type="button" class="sx-section-reset" data-sx-reset-section>${SVG.reset}<span>${esc(L("Сбросить раздел", "Reset section"))}</span></button>` : ""}${o.aside ? `<span class="sx-section-aside">${o.aside}</span>` : ""}</h3>
        ${o.desc ? `<p class="sx-section-desc">${o.desc}</p>` : ""}
      </header>` : "";
    const body = o.card === false ? `<div class="sx-section-body">${o.body || ""}</div>` : `<div class="sx-card">${o.body || ""}</div>`;
    return `<section class="sx-section${o.cls ? " " + o.cls : ""}"${attr("id", o.id)}${attr("data-sx-k", o.keywords)}${attr("data-sx-when", o.when)} data-sx-section>${head}${body}</section>`;
  }

  function toggle(id, checked, attrs = "") {
    return `<label class="switch sx-switch"><input type="checkbox" role="switch" id="${esc(id)}" ${checked ? "checked" : ""} ${attrs}><span class="sl"></span></label>`;
  }

  function normOptions(options) {
    return (options || []).map((item) => Array.isArray(item) ? { value: item[0], label: item[1], group: item[2] } : item);
  }

  function select(id, options, value, o = {}) {
    const rows = normOptions(options);
    const opt = (item) => `<option value="${esc(item.value)}" ${String(item.value) === String(value) ? "selected" : ""} ${item.disabled ? "disabled" : ""}>${esc(item.label)}</option>`;
    let inner = "";
    if (rows.some((item) => item.group)) {
      const groups = new Map();
      rows.forEach((item) => { const g = item.group || ""; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(item); });
      inner = [...groups.entries()].map(([g, items]) => g ? `<optgroup label="${esc(g)}">${items.map(opt).join("")}</optgroup>` : items.map(opt).join("")).join("");
    } else inner = rows.map(opt).join("");
    const style = o.width ? ` style="width:${typeof o.width === "number" ? o.width + "px" : esc(o.width)};margin:0"` : ` style="margin:0"`;
    return `<select class="pinput compact sx-select" id="${esc(id)}"${style} ${o.attrs || ""}>${inner}</select>`;
  }

  function segmented(name, options, value, o = {}) {
    const rows = normOptions(options);
    return `<div class="sx-seg" role="radiogroup"${attr("id", o.id)}${attr("aria-label", o.label)} ${o.attrs || ""}>${rows.map((item) => `<label class="sx-seg-opt"${attr("title", item.title)}><input type="radio" name="${esc(name)}" value="${esc(item.value)}" ${String(item.value) === String(value) ? "checked" : ""} ${item.disabled ? "disabled" : ""}><span>${item.icon || ""}${esc(item.label)}</span></label>`).join("")}</div>`;
  }

  // signed:true shows «+3» / «−2» / «0» (rates and offsets around a neutral middle)
  function sliderText(value, unit, signed) {
    const n = Number(value);
    const v = signed && Number.isFinite(n) ? (n > 0 ? "+" + n : n < 0 ? "−" + Math.abs(n) : "0") : String(value);
    return v + (unit || "");
  }
  function slider(id, o = {}) {
    const value = o.value ?? o.min ?? 0;
    return `<div class="sx-slider"><input type="range" id="${esc(id)}" min="${esc(o.min ?? 0)}" max="${esc(o.max ?? 100)}" step="${esc(o.step ?? 1)}" value="${esc(value)}" ${o.attrs || ""}><output class="sx-slider-value" for="${esc(id)}" data-unit="${esc(o.unit || "")}"${o.signed ? ' data-signed="1"' : ""}${attr("id", o.outputId)}>${esc(sliderText(value, o.unit, o.signed))}</output></div>`;
  }

  function input(id, value, o = {}) {
    const style = o.width ? ` style="width:${typeof o.width === "number" ? o.width + "px" : esc(o.width)};margin:0"` : "";
    return `<input class="pinput sx-input${o.mono ? " sx-mono" : ""}" id="${esc(id)}" type="${esc(o.type || "text")}" value="${esc(value ?? "")}"${attr("placeholder", o.placeholder)}${style} ${o.attrs || ""}>`;
  }

  function secret(id, value, o = {}) {
    return `<div class="sx-secret"><input class="pinput sx-input sx-mono" id="${esc(id)}" type="password" value="${esc(value ?? "")}" autocomplete="off" spellcheck="false"${attr("placeholder", o.placeholder)} ${o.attrs || ""}><button type="button" class="sx-secret-eye" data-sx-reveal="${esc(id)}" aria-label="${esc(L("Показать", "Show"))}" title="${esc(L("Показать", "Show"))}">${SVG.eye}</button></div>`;
  }

  function textarea(id, value, o = {}) {
    return `<textarea class="ptexta sx-textarea" id="${esc(id)}" rows="${esc(o.rows || 4)}"${attr("placeholder", o.placeholder)} ${o.attrs || ""}>${esc(value ?? "")}</textarea>`;
  }

  function field(o = {}) {
    return `<div class="sx-field${o.cls ? " " + o.cls : ""}"${attr("id", o.id)}${attr("data-sx-when", o.when)}${attr("data-sx-k", o.keywords)}>
      ${o.label ? `<label class="sx-field-label field-label tl"${attr("for", o.forId)}>${o.label}${badge(o.badge)}</label>` : ""}
      ${o.control || ""}
      ${o.help ? `<div class="sx-help td">${o.help}</div>` : ""}
    </div>`;
  }

  function more(o = {}) {
    return `<details class="sx-more"${attr("id", o.id)}${attr("data-sx-when", o.when)} ${o.open ? "open" : ""}><summary><span>${esc(o.title || L("Дополнительно", "Advanced"))}</span>${o.hint ? `<small>${esc(o.hint)}</small>` : ""}<i aria-hidden="true">${SVG.chev}</i></summary><div class="sx-more-body">${o.body || ""}</div></details>`;
  }

  function link(o = {}) {
    return `<button type="button" class="sx-row sx-row--link" data-sx-open="${esc(o.sub)}"${attr("id", o.id)}${attr("data-sx-k", o.keywords)}${attr("data-sx-when", o.when)}>
      ${o.icon ? `<span class="sx-link-icon" aria-hidden="true">${o.icon}</span>` : ""}
      <span class="sx-row-text"><span class="sx-label tl">${o.label || ""}${badge(o.badge)}</span>${o.desc ? `<span class="sx-desc td">${o.desc}</span>` : ""}</span>
      ${o.value ? `<span class="sx-link-value">${o.value}</span>` : ""}<span class="sx-chev" aria-hidden="true">${SVG.chev}</span>
    </button>`;
  }

  function sub(o = {}) {
    return `<div class="sx-sub" data-sx-sub="${esc(o.id)}" data-sx-title="${esc(o.title || "")}" hidden>
      <div class="sx-sub-head"><button type="button" class="sx-sub-back" data-sx-back aria-label="${esc(L("Назад", "Back"))}">${SVG.back}</button>
        <div><div class="sx-sub-title" role="heading" aria-level="3">${esc(o.title || "")}${badge(o.badge)}</div>${o.desc ? `<p class="sx-sub-desc">${o.desc}</p>` : ""}</div></div>
      ${o.body || ""}
    </div>`;
  }

  function note(text, tone = "info") {
    const ic = tone === "warn" ? SVG.warn : tone === "ok" ? SVG.check : tone === "danger" ? SVG.danger : SVG.info;
    return `<div class="sx-note sx-note--${esc(tone)}"><span class="sx-note-icon" aria-hidden="true">${ic}</span><div>${text}</div></div>`;
  }

  function actions(html, cls = "") {
    return `<div class="sx-actions${cls ? " " + cls : ""}">${html}</div>`;
  }

  function danger(o = {}) {
    return `<section class="sx-section sx-danger" data-sx-section${attr("id", o.id)}${attr("data-sx-k", o.keywords)}>
      <header class="sx-section-head"><h3 class="sx-section-title"><span class="sx-section-icon" aria-hidden="true">${SVG.danger}</span><span>${o.title || L("Опасная зона", "Danger zone")}</span></h3>${o.desc ? `<p class="sx-section-desc">${o.desc}</p>` : ""}</header>
      <div class="sx-card">${o.body || ""}</div></section>`;
  }

  function list(o = {}) {
    const items = o.items || [];
    if (!items.length) return `<div class="sx-list sx-list--empty"${attr("id", o.id)}>${o.empty || L("Пока пусто", "Nothing here yet")}</div>`;
    return `<div class="sx-list"${attr("id", o.id)}>${items.map((item) => `<div class="sx-list-item"${attr("data-id", item.id)}><div class="sx-list-main"><b>${item.title || ""}</b>${item.meta ? `<small>${item.meta}</small>` : ""}</div>${item.actions ? `<div class="sx-list-actions">${item.actions}</div>` : ""}</div>`).join("")}</div>`;
  }

  function card(body, cls = "") { return `<div class="sx-card${cls ? " " + cls : ""}">${body}</div>`; }

  /* ------------------------------------------------------------ shell state */

  const state = {
    page: "",
    sub: {},           // pageId -> sub id
    query: "",
    index: null,
    results: [],
    active: 0,
    extra: [],         // registered search entries
    opener: null,      // app.js: (pageId, opts) => void
    pages: [],         // [{ id, title, desc, icon, group, keywords }]
    savedTimer: 0,
  };

  function shell() { return document.querySelector("#panel .sx-shell"); }
  function body() { return document.querySelector("#panel .sx-shell .settings-body"); }

  function configure(o = {}) {
    if (typeof o.openPage === "function") state.opener = o.openPage;
    if (Array.isArray(o.pages)) state.pages = o.pages;
  }

  /* ---------------------------------------------------------- dependencies */

  function controlValue(id) {
    const el = document.getElementById(id);
    if (!el) return { exists: false };
    if (el.type === "checkbox") return { exists: true, checked: el.checked, value: el.checked ? "1" : "" };
    return { exists: true, checked: !!el.value, value: String(el.value) };
  }
  function evalCondition(expr) {
    return String(expr).split("&").every((part) => {
      part = part.trim();
      if (!part) return true;
      if (part[0] === "@") {
        const m = part.slice(1).match(/^([^=!]+)(!?=)(.*)$/);
        if (!m) return true;
        const checked = document.querySelector(`input[type=radio][name="${CSS.escape(m[1])}"]:checked`);
        const value = checked ? checked.value : "";
        const hit = m[3].split("|").includes(value);
        return m[2] === "=" ? hit : !hit;
      }
      if (part[0] === "!") { const v = controlValue(part.slice(1)); return v.exists ? !v.checked : true; }
      const m = part.match(/^([^=!]+)(!?=)(.*)$/);
      if (!m) { const v = controlValue(part); return v.exists ? v.checked : true; }
      const v = controlValue(m[1]);
      if (!v.exists) return true;
      const hit = m[3].split("|").includes(v.value);
      return m[2] === "=" ? hit : !hit;
    });
  }
  function refreshDeps(root) {
    root = root || body();
    if (!root) return;
    root.querySelectorAll("[data-sx-when]").forEach((node) => {
      const show = evalCondition(node.dataset.sxWhen);
      if (node.hidden === show) node.hidden = !show;
    });
  }

  /* ----------------------------------------------------------------- reset */

  function rowControl(row) {
    if (row.dataset.sxControl) return document.getElementById(row.dataset.sxControl);
    const box = row.querySelector(":scope > .sx-control") || row;
    const radios = box.querySelectorAll("input[type=radio]");
    if (radios.length) return { radios: [...radios] };
    // the type-to-filter field inside a searchable select (.mpick-search) is not the row's value
    return box.querySelector("input:not([type=hidden]):not(.mpick-search), select, textarea");
  }
  function readControl(control) {
    if (!control) return undefined;
    if (control.radios) { const c = control.radios.find((r) => r.checked); return c ? c.value : ""; }
    if (control.type === "checkbox") return control.checked;
    return control.value;
  }
  function sameValue(current, def) {
    if (typeof def === "boolean") return !!current === def;
    if (typeof def === "number") return Number(current) === def;
    return String(current ?? "") === String(def ?? "");
  }
  function rowDefault(row) {
    try { return JSON.parse(row.dataset.sxDefault); } catch (e) { return undefined; }
  }
  function refreshModified(root) {
    root = root || body();
    if (!root) return;
    root.querySelectorAll("[data-sx-key]").forEach((row) => {
      const control = rowControl(row);
      const def = rowDefault(row);
      if (!control || def === undefined) return;
      row.classList.toggle("sx-modified", !sameValue(readControl(control), def));
    });
  }
  function fire(el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); }
  function resetRow(row) {
    const control = rowControl(row), def = rowDefault(row);
    if (!control || def === undefined) return false;
    if (sameValue(readControl(control), def)) return false;
    if (control.radios) {
      const target = control.radios.find((r) => r.value === String(def));
      if (target) { target.checked = true; fire(target, "change"); }
    } else if (control.type === "checkbox") {
      control.checked = !!def; fire(control, "change");
    } else {
      control.value = String(def);
      if (control.type === "range") fire(control, "input");
      fire(control, "change");
    }
    return true;
  }

  /* ------------------------------------------------------------ saved pill */

  function saved(ok = true) {
    const pill = document.querySelector("#panel .sx-saved");
    if (!pill) return;
    clearTimeout(state.savedTimer);
    pill.dataset.state = ok ? "ok" : "err";
    pill.querySelector("span").textContent = ok ? L("Сохранено", "Saved") : L("Не сохранено", "Not saved");
    pill.classList.remove("show"); void pill.offsetWidth; pill.classList.add("show");
    state.savedTimer = setTimeout(() => pill.classList.remove("show"), ok ? 1600 : 4000);
  }

  /* ------------------------------------------------------------- sub-pages */

  function activePane() { return document.querySelector("#panel .sx-shell .settings-tab-pane.active"); }

  function clearSubMarks(pane) {
    pane.querySelectorAll(".sx-sub-hidden").forEach((node) => node.classList.remove("sx-sub-hidden"));
    pane.querySelectorAll(".sx-sub").forEach((node) => { node.hidden = true; });
    pane.classList.remove("sx-in-sub");
  }

  function openSub(id, { focus = true, scroll = true, animate = true } = {}) {
    const pane = activePane();
    if (!pane) return false;
    clearSubMarks(pane);
    const target = id ? pane.querySelector(`.sx-sub[data-sx-sub="${CSS.escape(id)}"]`) : null;
    const pageId = pane.dataset.page || state.page;
    if (!target) { state.sub[pageId] = ""; crumbs(); if (scroll && body()) body().scrollTop = 0; return false; }
    target.hidden = false;
    // Hide every sibling along the path from the sub-page up to the pane.
    let node = target;
    while (node && node !== pane) {
      const parent = node.parentElement;
      if (!parent) break;
      [...parent.children].forEach((child) => { if (child !== node) child.classList.add("sx-sub-hidden"); });
      node = parent;
    }
    pane.classList.add("sx-in-sub");
    state.sub[pageId] = id;
    crumbs();
    if (scroll && body()) body().scrollTop = 0;
    if (animate) animateIn(target);
    // Round 25: focus moves at once — the opener has just been hidden, and until a later frame
    // focus sat on <body>, outside the shell, so an early Escape closed the whole dialog.
    if (focus) target.querySelector(".sx-sub-back")?.focus({ preventScroll: true, focusVisible: false });
    if (window.enhanceSelects) try { window.enhanceSelects(target); } catch (e) {}
    refreshDeps(target); refreshModified(target);
    shell()?.dispatchEvent(new CustomEvent("sx:sub", { detail: { page: pageId, sub: id } }));
    return true;
  }

  function closeSub() {
    const pane = activePane();
    if (!pane) return false;
    const pageId = pane.dataset.page || state.page;
    const current = state.sub[pageId];
    if (!current) return false;
    clearSubMarks(pane);
    state.sub[pageId] = "";
    crumbs();
    animateIn(pane);
    const opener = pane.querySelector(`[data-sx-open="${CSS.escape(current)}"]`);
    requestAnimationFrame(() => {
      if (opener) { opener.scrollIntoView({ block: "center" }); opener.focus({ preventScroll: true }); }
      else if (body()) body().scrollTop = 0;
    });
    return true;
  }

  function animateIn(node) {
    if (!node || reducedMotion()) return;
    node.classList.remove("sx-enter"); void node.offsetWidth; node.classList.add("sx-enter");
  }
  function reducedMotion() {
    const root = document.documentElement;
    return root.dataset.motion === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function pageMeta(id) { return state.pages.find((p) => p.id === id) || { id, title: id }; }

  function crumbs() {
    const host = document.querySelector("#panel .sx-crumbs");
    if (!host) return;
    const pane = activePane();
    const pageId = pane?.dataset.page || state.page;
    const meta = pageMeta(pageId);
    const subId = state.sub[pageId];
    const subNode = subId && pane ? pane.querySelector(`.sx-sub[data-sx-sub="${CSS.escape(subId)}"]`) : null;
    const parts = [`<button type="button" class="sx-crumb sx-crumb-root" data-sx-crumb="nav">${esc(L("Настройки", "Settings"))}</button>`];
    if (state.query) parts.push(`<span class="sx-crumb-sep">${SVG.chev}</span><span class="sx-crumb is-current">${esc(L("Поиск", "Search"))}</span>`);
    else {
      parts.push(`<span class="sx-crumb-sep">${SVG.chev}</span>` + (subNode
        ? `<button type="button" class="sx-crumb" data-sx-crumb="page">${esc(meta.title)}</button>`
        : `<span class="sx-crumb is-current" aria-current="page">${esc(meta.title)}</span>`));
      if (subNode) parts.push(`<span class="sx-crumb-sep">${SVG.chev}</span><span class="sx-crumb is-current" aria-current="page">${esc(subNode.dataset.sxTitle || subId)}</span>`);
    }
    host.innerHTML = parts.join("");
  }

  /* ---------------------------------------------------------------- search */

  const EN_RU = { q: "й", w: "ц", e: "у", r: "к", t: "е", y: "н", u: "г", i: "ш", o: "щ", p: "з", "[": "х", "]": "ъ", a: "ф", s: "ы", d: "в", f: "а", g: "п", h: "р", j: "о", k: "л", l: "д", ";": "ж", "'": "э", z: "я", x: "ч", c: "с", v: "м", b: "и", n: "т", m: "ь", ",": "б", ".": "ю", "`": "ё" };
  const RU_EN = Object.fromEntries(Object.entries(EN_RU).map(([k, v]) => [v, k]));
  function swapLayout(text) {
    const s = String(text).toLowerCase();
    const latin = /[a-z]/.test(s), cyr = /[а-яё]/.test(s);
    if (latin && !cyr) return [...s].map((c) => EN_RU[c] || c).join("");
    if (cyr && !latin) return [...s].map((c) => RU_EN[c] || c).join("");
    return "";
  }
  function norm(text) {
    return String(text || "").toLowerCase().replace(/ё/g, "е").replace(/[«»"“”„()\[\]{}.,:;!?·—–\-/\\|+*]/g, " ").replace(/\s+/g, " ").trim();
  }
  function lev1(a, b) {
    // true when edit distance <= 1 (insert/delete/substitute/transpose)
    if (a === b) return true;
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    let i = 0; while (i < la && i < lb && a[i] === b[i]) i++;
    if (la === lb) return a.slice(i + 1) === b.slice(i + 1) || (a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2));
    return la > lb ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
  }
  function tokenScore(token, words, text) {
    if (!token) return 0;
    if (words.some((w) => w === token)) return 10;
    if (words.some((w) => w.startsWith(token))) return 8;
    if (text.includes(token)) return token.length >= 4 ? 3.5 : 2.5;
    if (token.length >= 4 && words.some((w) => lev1(token, w.slice(0, token.length)) || lev1(token, w))) return 3;
    if (token.length >= 3) {
      // subsequence inside one word ("уведм" → "уведомления")
      for (const w of words) {
        let j = 0;
        for (let k = 0; k < w.length && j < token.length; k++) if (w[k] === token[j]) j++;
        if (j === token.length && w[0] === token[0]) return 2;
      }
    }
    return 0;
  }
  function scoreEntry(tokens, entry) {
    let total = 0;
    for (const token of tokens) {
      const label = tokenScore(token, entry.labelWords, entry.labelN) * 3;
      const rest = Math.max(tokenScore(token, entry.descWords, entry.descN), tokenScore(token, entry.kwWords, entry.kwN) * 1.4, tokenScore(token, entry.ctxWords, entry.ctxN) * 0.6);
      const best = Math.max(label, rest);
      if (!best) return 0;
      total += best;
    }
    if (entry.labelN.startsWith(tokens.join(" "))) total += 12;
    if (entry.kind === "page") total += 4;
    return total;
  }

  function textOf(node) { return node ? node.textContent.replace(/[✓›]/g, " ").replace(/\s+/g, " ").trim() : ""; }
  function firstOf(node, selectors) {
    for (const sel of selectors) { const hit = node.querySelector(":scope " + sel); if (hit) return hit; }
    return null;
  }
  function makeEntry(o) {
    const labelN = norm(o.label), descN = norm(o.desc), kwN = norm(o.keywords), ctxN = norm([o.context, o.subTitle].filter(Boolean).join(" "));
    return { ...o, labelN, descN, kwN, ctxN,
      labelWords: labelN.split(" ").filter(Boolean), descWords: descN.split(" ").filter(Boolean),
      kwWords: kwN.split(" ").filter(Boolean), ctxWords: ctxN.split(" ").filter(Boolean) };
  }

  function buildIndex() {
    const root = body();
    const entries = [];
    if (!root) return entries;
    const seen = new Set();
    state.pages.forEach((page) => entries.push(makeEntry({ kind: "page", page: page.id, label: page.title, desc: page.desc || "", keywords: page.keywords || "", context: page.groupTitle || "" })));
    const rowSel = ".sx-row, .p-toggle, .tg-row, .sx-field, .notification-settings-row, .sound-settings-head, .voice-model-card, .transfer-card, [data-sx-k]";
    root.querySelectorAll(".settings-tab-pane").forEach((pane) => {
      const pageId = pane.dataset.page || pane.id.replace(/^pane-/, "");
      pane.querySelectorAll(rowSel).forEach((node) => {
        if (seen.has(node)) return;
        if (node.closest(".sx-results")) return;
        // One entry per row: skip a row nested in an already indexed row.
        const outer = node.parentElement?.closest(rowSel);
        if (outer && pane.contains(outer) && !outer.matches("section, details, .sx-section, [data-sx-section]")) return;
        let label = "";
        const labelNode = firstOf(node, [".sx-label", ".tl", ".settings-title", "b", "label"]);
        // data-sx-label names a picker whose first <b>/<label> is just one of its options (the design and accent grids).
        if (node.dataset.sxLabel) label = node.dataset.sxLabel;
        else if (node.matches(".sx-section, section")) label = textOf(node.querySelector(".sx-section-title, .settings-title"));
        else label = textOf(labelNode);
        if (!label) return;
        seen.add(node);
        const desc = textOf(firstOf(node, [".sx-desc", ".td", "p", "small"]));
        const sectionNode = node.closest(".sx-section, .settings-section, .tg-card, details");
        let context = "";
        if (sectionNode && sectionNode !== node) {
          const titleNode = sectionNode.querySelector(".sx-section-title > span:not([class]), .sx-more > summary > span, .settings-title, .tg-card-title, summary");
          // a <summary> may carry a hint (<small>) after its title: index only the title
          if (titleNode && titleNode.matches("summary")) {
            const own = titleNode.querySelector(":scope > span, :scope > b");
            context = own ? textOf(own) : [...titleNode.childNodes].filter((n) => !(n.nodeType === 1 && n.matches("small, .sx-more-hint"))).map((n) => n.textContent).join(" ").replace(/\s+/g, " ").trim();
          } else context = textOf(titleNode);
        }
        const subNode = node.closest(".sx-sub");
        entries.push(makeEntry({ kind: node.matches(".sx-section, section") ? "section" : "row", page: pageId, sub: subNode ? subNode.dataset.sxSub : "",
          subTitle: subNode ? subNode.dataset.sxTitle : "", label: label.slice(0, 140), desc: desc.slice(0, 220),
          keywords: node.dataset.sxK || "", context, node }));
      });
    });
    state.extra.forEach((item) => {
      const en = isEn();
      entries.push(makeEntry({ kind: "row", page: item.page, sub: item.sub || "", label: en ? (item.label_en || item.label_ru) : (item.label_ru || item.label_en),
        desc: en ? (item.desc_en || item.desc_ru || "") : (item.desc_ru || item.desc_en || ""), keywords: item.keywords || "", anchor: item.anchor || "", context: "" }));
    });
    return entries;
  }

  function search(query) {
    const q = norm(query);
    if (!q) return [];
    if (!state.index) state.index = buildIndex();
    const variants = [q];
    const swapped = norm(swapLayout(query));
    if (swapped && swapped !== q) variants.push(swapped);
    let best = [];
    variants.forEach((variant, vi) => {
      const tokens = variant.split(" ").filter(Boolean);
      const scored = [];
      state.index.forEach((entry) => {
        const s = scoreEntry(tokens, entry) * (vi ? 0.9 : 1);
        if (s > 0) scored.push({ entry, score: s, tokens });
      });
      if (scored.length > best.length || (vi === 0 && scored.length)) best = vi === 0 || !best.length ? scored : best;
    });
    best.sort((a, b) => b.score - a.score || a.entry.labelN.length - b.entry.labelN.length);
    const out = [], keys = new Set();
    for (const hit of best) {
      const k = hit.entry.page + "|" + hit.entry.sub + "|" + hit.entry.labelN + "|" + hit.entry.kind;
      if (keys.has(k)) continue;
      keys.add(k); out.push(hit);
      if (out.length >= 40) break;
    }
    return out;
  }

  function mark(text, tokens) {
    let html = esc(text);
    const ts = [...new Set(tokens)].filter((t) => t.length >= 2).sort((a, b) => b.length - a.length);
    if (!ts.length) return html;
    // Highlight on the normalized text, mapping ё→е; accept a small mismatch.
    const pattern = ts.map((t) => t.replace(/е/g, "[её]").replace(/[.*+?^${}()|\\]/g, "\\$&")).join("|");
    try { html = html.replace(new RegExp(`(${pattern})`, "gi"), "<mark>$1</mark>"); } catch (e) {}
    return html;
  }

  function renderResults() {
    const host = document.querySelector("#panel .sx-results");
    const main = document.querySelector("#panel .sx-main");
    if (!host || !main) return;
    const searching = !!state.query.trim();
    main.classList.toggle("is-searching", searching);
    host.hidden = !searching;
    crumbs();
    if (!searching) { host.innerHTML = ""; return; }
    const hits = state.results;
    const input = document.querySelector("#panel #sx-search");
    if (!hits.length) {
      host.innerHTML = `<div class="sx-results-empty"><div class="sx-results-empty-icon">${SVG.search}</div><b>${esc(L("Ничего не нашлось", "Nothing found"))}</b><p>${esc(L("Попробуйте другое слово — например «тема», «голос», «ключ» или «Telegram».", "Try another word — for example “theme”, “voice”, “key” or “Telegram”."))}</p></div>`;
      input?.removeAttribute("aria-activedescendant");
      return;
    }
    host.innerHTML = `<div class="sx-results-count">${esc(L(`Найдено: ${hits.length}`, `${hits.length} result${hits.length === 1 ? "" : "s"}`))}</div><div class="sx-results-list" role="listbox" id="sx-results-list" aria-label="${esc(L("Результаты поиска", "Search results"))}">${hits.map((hit, i) => {
      const e = hit.entry, meta = pageMeta(e.page);
      const path = [meta.title, e.subTitle, e.context && e.context !== e.label ? e.context : ""].filter(Boolean).map(esc).join(` <i>›</i> `);
      return `<button type="button" role="option" class="sx-result${i === state.active ? " is-active" : ""}" id="sx-result-${i}" data-sx-result="${i}" aria-selected="${i === state.active}">
        <span class="sx-result-icon" aria-hidden="true">${meta.icon || ""}</span>
        <span class="sx-result-text"><span class="sx-result-path">${e.kind === "page" ? esc(L("Раздел", "Section")) : path}</span><b>${mark(e.label, hit.tokens)}</b>${e.desc ? `<small>${mark(e.desc.slice(0, 160), hit.tokens)}</small>` : ""}</span>
        <span class="sx-result-go" aria-hidden="true">${SVG.chev}</span></button>`;
    }).join("")}</div>`;
    input?.setAttribute("aria-activedescendant", `sx-result-${state.active}`);
  }

  function setActive(index) {
    if (!state.results.length) return;
    state.active = (index + state.results.length) % state.results.length;
    document.querySelectorAll("#panel .sx-result").forEach((node, i) => {
      node.classList.toggle("is-active", i === state.active);
      node.setAttribute("aria-selected", String(i === state.active));
    });
    const current = document.getElementById(`sx-result-${state.active}`);
    current?.scrollIntoView({ block: "nearest" });
    document.querySelector("#panel #sx-search")?.setAttribute("aria-activedescendant", `sx-result-${state.active}`);
  }

  function setQuery(value) {
    state.query = value;
    state.results = search(value);
    state.active = 0;
    renderResults();
  }

  function clearSearch() {
    const input = document.querySelector("#panel #sx-search");
    if (input) input.value = "";
    setQuery("");
  }

  function revealNode(node) {
    // open parent <details>, then flash
    let parent = node.parentElement;
    while (parent) {
      if (parent.tagName === "DETAILS" && !parent.open) parent.open = true;
      if (parent.hidden && parent.matches("[data-sx-when]")) parent.hidden = false;
      parent = parent.parentElement;
    }
  }

  function flash(node) {
    if (!node) return;
    node.classList.remove("sx-flash"); void node.offsetWidth; node.classList.add("sx-flash");
    setTimeout(() => node.classList.remove("sx-flash"), 2200);
  }

  function jumpTo(entry) {
    if (!entry) return;
    clearSearch();
    open(entry.page, { sub: entry.sub || "", keepQuery: false });
    requestAnimationFrame(() => {
      let node = entry.node && entry.node.isConnected ? entry.node : null;
      if (!node && entry.anchor) node = document.querySelector(entry.anchor);
      if (!node && entry.kind !== "page") {
        // The pane was re-rendered: find the row again by its label.
        const pane = activePane();
        node = [...(pane?.querySelectorAll(".sx-label, .tl, .settings-title") || [])].find((el) => norm(el.textContent) === entry.labelN)?.closest(".sx-row, .p-toggle, .tg-row, .sx-field, section") || null;
      }
      if (!node) { activePane()?.querySelector(".sx-page-title")?.focus?.({ preventScroll: true }); return; }
      // A link row («GitHub ›») stands for its sub-page: open it instead of just highlighting the link.
      if (node.matches("[data-sx-open]") && node.dataset.sxOpen) { openSub(node.dataset.sxOpen); return; }
      revealNode(node);
      node.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
      flash(node);
      const control = node.querySelector("input:not([type=hidden]):not([disabled]), select, textarea, button:not(.sx-reset):not([disabled]), .pretty-select-button");
      const target = node.querySelector(".pretty-select-button") || control;
      setTimeout(() => target?.focus({ preventScroll: true }), reducedMotion() ? 0 : 260);
    });
  }

  /* ------------------------------------------------------------- navigation */

  function open(pageId, o = {}) {
    if (!o.keepQuery && state.query) clearSearch();
    if (typeof state.opener === "function") state.opener(pageId, o);
  }

  // Called by app.js after it switched panes.
  function pageShown(pageId, o = {}) {
    state.page = pageId;
    const sh = shell();
    if (sh) {
      sh.classList.remove("sx-nav-open");
      sh.classList.toggle("sx-quiet", o.animate === false);
      // keep the active page visible in a long sidebar (scroll only the nav, never the dialog)
      const nav = sh.querySelector(".sx-nav"), btn = nav && nav.querySelector(".settings-tab-btn.active");
      if (nav && btn) {
        const n = nav.getBoundingClientRect(), r = btn.getBoundingClientRect();
        if (r.bottom > n.bottom - 8) nav.scrollTop += r.bottom - n.bottom + 24;
        else if (r.top < n.top + 8) nav.scrollTop -= n.top - r.top + 24;
      }
    }
    const sub = o.sub !== undefined ? o.sub : state.sub[pageId] || "";
    if (sub) openSub(sub, { focus: false, scroll: o.scroll !== false, animate: o.animate !== false });
    else { const pane = activePane(); if (pane) clearSubMarks(pane); state.sub[pageId] = ""; crumbs(); }
    // Round 25: an in-place re-render (o.animate === false) must not replay the fade from opacity 0.
    if (o.animate !== false) animateIn(activePane());
    refreshDeps(); refreshModified();
    state.index = null;
  }

  function invalidate() { state.index = null; }

  /* ------------------------------------------------------------- mounting */

  function mount(o = {}) {
    const sh = shell();
    if (!sh) return;
    if (o.page) state.page = o.page;
    state.index = null;
    const input = sh.querySelector("#sx-search");

    sh.addEventListener("input", (event) => {
      const t = event.target;
      if (t.id === "sx-search") { setQuery(t.value); return; }
      if (t.type === "range") {
        const out = sh.querySelector(`output.sx-slider-value[for="${CSS.escape(t.id)}"]`);
        if (out) out.textContent = sliderText(t.value, out.dataset.unit || "", out.dataset.signed === "1");
      }
      refreshDeps(); refreshModified();
    });
    sh.addEventListener("change", (event) => {
      if (event.target.id === "sx-search") return;
      refreshDeps(); refreshModified();
    });
    sh.addEventListener("click", (event) => {
      const t = event.target;
      const result = t.closest("[data-sx-result]");
      if (result) { jumpTo(state.results[+result.dataset.sxResult]?.entry); return; }
      const opener = t.closest("[data-sx-open]");
      if (opener && sh.contains(opener)) { event.preventDefault(); openSub(opener.dataset.sxOpen); return; }
      if (t.closest("[data-sx-back]")) { closeSub(); return; }
      const crumb = t.closest("[data-sx-crumb]");
      if (crumb) {
        if (crumb.dataset.sxCrumb === "page") closeSub();
        else if (crumb.dataset.sxCrumb === "nav") { if (state.query) clearSearch(); sh.classList.add("sx-nav-open"); sh.querySelector(".settings-tab-btn.active")?.focus(); }
        return;
      }
      if (t.closest(".sx-nav-toggle")) { sh.classList.toggle("sx-nav-open"); return; }
      const reveal = t.closest("[data-sx-reveal]");
      if (reveal) {
        const field = document.getElementById(reveal.dataset.sxReveal);
        if (field) {
          const show = field.type === "password";
          field.type = show ? "text" : "password";
          reveal.classList.toggle("is-on", show);
          reveal.setAttribute("aria-label", show ? L("Скрыть", "Hide") : L("Показать", "Show"));
        }
        return;
      }
      const reset = t.closest("[data-sx-reset]");
      if (reset) {
        event.preventDefault(); event.stopPropagation();
        const row = reset.closest("[data-sx-key]");
        if (row && resetRow(row)) { flash(row); refreshModified(); }
        return;
      }
      const resetSection = t.closest("[data-sx-reset-section]");
      if (resetSection) {
        event.preventDefault();
        const sec = resetSection.closest("[data-sx-section]");
        const rows = [...(sec?.querySelectorAll("[data-sx-key]") || [])].filter((row) => row.classList.contains("sx-modified"));
        if (!rows.length) { saved(true); return; }
        const title = textOf(sec.querySelector(".sx-section-title > span"));
        const go = () => { rows.forEach(resetRow); refreshModified(); rows.forEach(flash); };
        if (typeof window.askConfirm === "function") {
          window.askConfirm(L(`Сбросить «${title}»?`, `Reset “${title}”?`), L(`Будет восстановлено значений по умолчанию: ${rows.length}.`, `${rows.length} setting(s) will return to their defaults.`), L("Сбросить", "Reset"))
            .then((yes) => { if (yes) requestAnimationFrame(go); });
        } else go();
      }
    });
    sh.addEventListener("keydown", (event) => {
      const t = event.target;
      const prettyOpen = document.querySelector(".pretty-select.open");
      if (t.id === "sx-search") {
        if (event.key === "ArrowDown") { event.preventDefault(); setActive(state.active + 1); return; }
        if (event.key === "ArrowUp") { event.preventDefault(); setActive(state.active - 1); return; }
        if (event.key === "Enter") { event.preventDefault(); const hit = state.results[state.active]; if (hit) jumpTo(hit.entry); return; }
        if (event.key === "Escape" && t.value) { event.preventDefault(); event.stopPropagation(); clearSearch(); return; }
      }
      if (event.key === "Escape" && !prettyOpen) {
        if (state.query) { event.preventDefault(); event.stopPropagation(); clearSearch(); input?.focus(); return; }
        if (sh.classList.contains("sx-nav-open") && sh.classList.contains("sx-narrow-able")) { /* fallthrough to close */ }
        if (state.sub[state.page] && !t.closest("input, textarea, select")) { event.preventDefault(); event.stopPropagation(); closeSub(); return; }
      }
      if ((event.ctrlKey || event.metaKey) && !event.altKey && (event.code === "KeyF" || event.key === "f" || event.key === "а")) {
        event.preventDefault(); input?.focus(); input?.select(); return;
      }
      if (event.altKey && event.key === "ArrowLeft" && state.sub[state.page]) { event.preventDefault(); event.stopPropagation(); closeSub(); return; }
      if (t.closest(".sx-nav") && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        const items = [...sh.querySelectorAll(".sx-nav .settings-tab-btn")];
        let i = items.indexOf(t.closest(".settings-tab-btn"));
        if (event.key === "Home") i = 0; else if (event.key === "End") i = items.length - 1;
        else i = Math.max(0, Math.min(items.length - 1, i + (event.key === "ArrowDown" ? 1 : -1)));
        event.preventDefault(); items[i]?.focus(); items[i]?.click(); items[i]?.focus();
        return;
      }
      if ((window.MemuKeys ? MemuKeys.latin(event) : event.key) === "/" && !t.closest("input, textarea, select, [contenteditable]")) { event.preventDefault(); input?.focus(); }
    });
    // Other scripts (modules.js, docs.js) switch panes by toggling classes
    // directly; follow them so the breadcrumbs and sub-page state stay right.
    const bodyNode = sh.querySelector(".settings-body");
    if (bodyNode && window.MutationObserver) {
      new MutationObserver(() => {
        const pane = activePane();
        const id = pane?.dataset.page;
        if (id && id !== state.page) {
          state.page = id;
          sh.dataset.page = id;
          sh.querySelectorAll(".settings-tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.setTab === id));
          crumbs();
        }
        state.index = null;
      }).observe(bodyNode, { subtree: true, attributes: true, attributeFilter: ["class"], childList: true });
    }
    if (o.query) { input.value = o.query; setQuery(o.query); }
    // The search field is the natural first stop (Raycast/Linear style); a
    // fresh opening focuses it instead of the close button.
    if (o.focusSearch) setTimeout(() => {
      if (!sh.isConnected) return;
      // In a narrow window the sidebar (and its search) is folded away: focus the page title instead of the
      // close button, so nothing wears a focus ring right after opening.
      const visible = input && (typeof input.checkVisibility === "function" ? input.checkVisibility({ visibilityProperty: true, opacityProperty: true }) : input.offsetParent !== null);
      if (visible) input.focus({ preventScroll: true });
      else { const title = activePane()?.querySelector(".sx-page-title"); if (title) { if (!title.hasAttribute("tabindex")) title.setAttribute("tabindex", "-1"); title.focus({ preventScroll: true }); } }
    }, 0);
    else crumbs();
    refreshDeps(); refreshModified();
  }

  function registerSearch(entries) {
    (entries || []).forEach((item) => {
      if (!item || !item.page) return;
      const key = item.page + "|" + (item.label_ru || item.label_en);
      if (!state.extra.some((x) => x.page + "|" + (x.label_ru || x.label_en) === key)) state.extra.push(item);
    });
    state.index = null;
  }

  const api = {
    esc, row, section, toggle, select, segmented, slider, input, secret, textarea, field, more, link, sub, note, badge, chip,
    actions, danger, list, card, icons: SVG,
    configure, mount, open, openSub, closeSub, pageShown, saved, refreshDeps, refreshModified, registerSearch, invalidate,
    search: (q) => search(q).map((hit) => ({ page: hit.entry.page, sub: hit.entry.sub, label: hit.entry.label, kind: hit.entry.kind, score: hit.score })),
    jump: (q) => { const hit = search(q)[0]; if (hit) jumpTo(hit.entry); return !!hit; },
    state,
  };
  window.MemuSettingsKit = api;
  if (!window.SX) window.SX = api;
})();
