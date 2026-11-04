"use strict";
/* MemuAI personalities: the gallery in Settings → Chat & replies and helpers for the composer.
   The catalogue itself comes from the server (internal prompts "personality-<id>", app/personalities.py),
   so a user-edited personality text and every surface (Telegram, Mini App, Quick Ask) stay in sync. */
const MemuPersonalities = (() => {
  const SVG = (body) => `<svg viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
  const OWN = {
    smile: SVG('<circle cx="12" cy="12" r="9.5"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9.5h.01M15 9.5h.01"/>'),
    help: SVG('<circle cx="12" cy="12" r="9.5"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>'),
    graduation: SVG('<path d="M22 10 12 5 2 10l10 5 10-5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/>'),
    messages: SVG('<path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/><path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>'),
    trending: SVG('<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>'),
    compass: SVG('<circle cx="12" cy="12" r="9.5"/><path d="m16.2 7.8-2.1 6.3-6.3 2.1 2.1-6.3z"/>'),
    flask: SVG('<path d="M10 2v7.5a2 2 0 0 1-.2.9L4.7 20.6a1 1 0 0 0 .9 1.4h12.8a1 1 0 0 0 .9-1.4l-5.1-10.2a2 2 0 0 1-.2-.9V2"/><path d="M8.5 2h7M7 16h10"/>'),
    scale: SVG('<path d="m16 16 3-8 3 8c-.9.6-1.9 1-3 1s-2.1-.4-3-1z"/><path d="m2 16 3-8 3 8c-.9.6-1.9 1-3 1s-2.1-.4-3-1z"/><path d="M7 21h10M12 3v18M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>'),
    baby: SVG('<path d="M9 12h.01M15 12h.01"/><path d="M10 16c.5.3 1.2.5 2 .5s1.5-.2 2-.5"/><path d="M19 6.3a9 9 0 0 1 1.8 3.9 2 2 0 0 1 0 3.6 9 9 0 0 1-17.6 0 2 2 0 0 1 0-3.6A9 9 0 0 1 12 3c2 0 3.5 1.1 3.5 2.5S14.6 8 13.5 8c-.8 0-1.5-.4-1.5-1"/>'),
    sparkles: SVG('<path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="m19 15 .8 2.2 2.2.8-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/><path d="m5 2 .6 1.4L7 4l-1.4.6L5 6l-.6-1.4L3 4l1.4-.6z"/>'),
    feather: SVG('<path d="M20.2 12.2a6 6 0 0 0-8.5-8.5L5 10.5V19h8.5z"/><path d="M16 8 2 22M17.5 15H9"/>'),
    lightbulb: SVG('<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6M10 22h4"/>'),
    drama: SVG('<path d="M2 12a5 5 0 0 0 5 5 8 8 0 0 1 5 2 8 8 0 0 1 5-2 5 5 0 0 0 5-5V7h-5a8 8 0 0 0-5 2 8 8 0 0 0-5-2H2z"/><path d="M6 11c1.5 0 3 .5 3 2-2 0-3 0-3-2zM18 11c-1.5 0-3 .5-3 2 2 0 3 0 3-2z"/>'),
    heart: SVG('<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z"/>'),
    cat: SVG('<path d="M12 5c.7 0 1.4.1 2 .3 1.8-2 5-2.8 6.4-2.3 1.4.6-.4 7-.4 7 .6 1.1 1 2.2 1 3.4C21 17.9 17 21 12 21s-9-3-9-7.6c0-1.2.5-2.4 1-3.4 0 0-1.9-6.4-.5-7 1.4-.6 4.7.2 6.5 2.2A9 9 0 0 1 12 5z"/><path d="M8 14v.5M16 14v.5M11.3 16.3h1.5L12 17z"/>'),
    anchor: SVG('<circle cx="12" cy="5" r="3"/><path d="M12 22V8M5 12H2a10 10 0 0 0 20 0h-3"/>'),
    waves: SVG('<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>'),
    flame: SVG('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>'),
    smirk: SVG('<circle cx="12" cy="12" r="9.5"/><path d="M9 15.2c2 .8 4.5.6 6.5-1.2"/><path d="M8 9.5h2M14 9h2"/>'),
    leaf: SVG('<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.5 19 2c1 2 2 4.2 2 8 0 5.5-4.8 10-10 10z"/><path d="M2 21c0-3 1.9-5.4 5.1-6 2.4-.5 4.9-2 5.9-3"/>'),
    home: SVG('<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>'),
  };
  const CATS = [["all", "Все", "All"], ["basic", "Основные", "Essentials"], ["work", "Работа и учёба", "Work & learning"],
    ["creative", "Творчество", "Creative"], ["fun", "Весёлые", "Playful"]];
  const state = { cat: "", query: "" };
  /* Mirror of app/personalities.py SHARED_RULE (a test keeps them equal). */
  const SHARED_RULE = "The personality changes tone, rhythm and wording only — never facts, accuracy, the quality of the work, consent or safety. On serious or sensitive topics (health, grief, safety, money, law) keep the style subtle and be plainly helpful. Code, commands and data stay exact and unstyled.";

  const isEn = () => typeof PG_LANG !== "undefined" && PG_LANG === "en";
  const L = (ru, en) => (isEn() ? en : ru);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function iconSvg(name) {
    if (OWN[name]) return OWN[name];
    try { if (typeof ICONS !== "undefined" && ICONS[name]) return ICONS[name]; } catch (e) { /* no app icons */ }
    return OWN.smile;
  }
  function rows() {
    const list = (typeof S !== "undefined" && S.prompts && S.prompts.internal_prompts) || [];
    return list.filter((item) => item && String(item.id || "").startsWith("personality-")).map((item) => ({
      id: String(item.id).slice("personality-".length),
      name: isEn() ? String(item.name || "").replace(/ personality$/i, "") : String(item.name_ru || item.name || "").replace(/^Личность:\s*/i, ""),
      description: isEn() ? item.description || "" : item.description_ru || item.description || "",
      icon: item.icon || "smile", category: item.category || "basic",
    }));
  }
  function current() {
    try { return String((typeof pref === "function" ? pref("ai_personality", "friendly") : "friendly") || "friendly"); } catch (e) { return "friendly"; }
  }
  function find(id) { return rows().find((row) => row.id === id) || rows().find((row) => row.id === "friendly") || null; }
  function label(id) { const row = find(id || current()); return row ? row.name : L("Дружелюбная", "Friendly"); }

  function optionsHtml(selected) {
    const list = rows();
    if (!list.length) return `<option value="friendly">${escape(L("Дружелюбная", "Friendly"))}</option><option value="pragmatic"${selected === "pragmatic" ? " selected" : ""}>${escape(L("Прагматичная", "Pragmatic"))}</option>`;
    return CATS.slice(1).map(([id, ru, en]) => {
      const items = list.filter((row) => row.category === id);
      if (!items.length) return "";
      return `<optgroup label="${escape(L(ru, en))}">${items.map((row) => `<option value="${escape(row.id)}"${row.id === selected ? " selected" : ""}>${escape(row.name)}</option>`).join("")}</optgroup>`;
    }).join("");
  }

  function cardsHtml(selected) {
    const q = state.query.trim().toLowerCase();
    const list = rows().filter((row) => (state.cat === "all" || row.category === state.cat)
      && (!q || (row.name + " " + row.description + " " + row.id).toLowerCase().includes(q)));
    if (!list.length) return `<p class="pers-empty">${escape(L("Ничего не найдено", "Nothing found"))}</p>`;
    return list.map((row) => `<button type="button" class="pers-card${row.id === selected ? " on" : ""}" role="radio" aria-checked="${row.id === selected}" data-pers="${escape(row.id)}" data-cat="${escape(row.category)}">
        <span class="pers-ico" aria-hidden="true">${iconSvg(row.icon)}</span>
        <span class="pers-copy"><b>${escape(row.name)}</b><small>${escape(row.description)}</small></span>
        <span class="pers-check" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg></span>
      </button>`).join("");
  }

  function galleryHtml() {
    const selected = current();
    const row = find(selected);
    if (!state.cat) state.cat = row && row.category !== "basic" ? row.category : "basic";
    return `<div class="pers-head">
        <span class="pers-ico big" aria-hidden="true">${iconSvg(row ? row.icon : "smile")}</span>
        <span class="pers-copy"><small>${escape(L("Сейчас", "Current"))}</small><b>${escape(row ? row.name : selected)}</b><span>${escape(row ? row.description : "")}</span></span>
        <button type="button" class="pers-edit" data-pers-edit>${escape(L("Изменить текст", "Edit text"))}</button>
      </div>
      <div class="pers-tools">
        <div class="pers-tabs" role="tablist">${CATS.map(([id, ru, en]) => `<button type="button" role="tab" class="pers-tab${state.cat === id ? " on" : ""}" aria-selected="${state.cat === id}" data-pers-cat="${id}">${escape(L(ru, en))}</button>`).join("")}</div>
        <input type="search" class="pers-search" data-pers-search placeholder="${escape(L("Найти личность", "Find a personality"))}" value="${escape(state.query)}" aria-label="${escape(L("Найти личность", "Find a personality"))}">
      </div>
      <div class="pers-grid" role="radiogroup" aria-label="${escape(L("Личность MemuAI", "MemuAI personality"))}">${cardsHtml(selected)}</div>
      <p class="pers-note">${escape(L("Личность меняет тон и манеру ответов во всех чатах, в Telegram и в расширении браузера — но не точность и не безопасность.",
        "A personality changes the tone and manner of replies in every chat, in Telegram and in the browser extension — never accuracy or safety."))}</p>`;
  }

  async function choose(id, host, options) {
    if (!id) return;
    const select = document.getElementById("st-personality");
    if (select && select.value !== id) { select.value = id; select.dispatchEvent(new Event("change", { bubbles: true })); }
    else if (typeof savePreferences === "function") await savePreferences({ ai_personality: id });
    if (options && typeof options.onPick === "function") options.onPick(id);
    render(host, options);
  }

  function render(host, options = {}) {
    if (!host) return;
    const keepFocus = document.activeElement && document.activeElement.matches && document.activeElement.matches("[data-pers-search]");
    host.innerHTML = galleryHtml();
    host.querySelectorAll("[data-pers]").forEach((button) => { button.onclick = () => choose(button.dataset.pers, host, options); });
    host.querySelectorAll("[data-pers-cat]").forEach((button) => { button.onclick = () => { state.cat = button.dataset.persCat; render(host, options); }; });
    const search = host.querySelector("[data-pers-search]");
    if (search) {
      search.oninput = () => { state.query = search.value; const grid = host.querySelector(".pers-grid"); if (grid) { grid.innerHTML = cardsHtml(current()); grid.querySelectorAll("[data-pers]").forEach((b) => { b.onclick = () => choose(b.dataset.pers, host, options); }); } };
      if (keepFocus) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); }
    }
    const edit = host.querySelector("[data-pers-edit]");
    if (edit) edit.onclick = () => {
      if (typeof options.onEdit === "function") return options.onEdit("personality-" + current());
      if (typeof openSettingsPage === "function") openSettingsPage("prompts");
    };
    const grid = host.querySelector(".pers-grid");
    if (grid) grid.onkeydown = (event) => {
      const cards = [...grid.querySelectorAll("[data-pers]")];
      const index = cards.indexOf(document.activeElement);
      if (index < 0) return;
      const step = { ArrowRight: 1, ArrowDown: 2, ArrowLeft: -1, ArrowUp: -2 }[event.key];
      if (!step) return;
      event.preventDefault();
      const next = cards[Math.max(0, Math.min(cards.length - 1, index + step))];
      if (next) next.focus();
    };
  }

  function mount(host, options = {}) { render(host, options); }

  return { mount, render, rows, label, current, optionsHtml, iconSvg, icons: OWN, SHARED_RULE };
})();
window.MemuPersonalities = MemuPersonalities;
