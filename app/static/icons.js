/* MemuAI Proxy — line icons.
   Buttons keep their old text glyphs in i18n and markup (✦ ⟳ ＋ ✓ ⧉ …): they are the
   fallback and some strings are shared with other code. This script swaps a leading
   or trailing glyph of a button for a crisp inline SVG, and watches the DOM so labels
   that i18n or a re-render writes later get the same treatment. Nothing here changes
   ids, classes or handlers. */
"use strict";
(function () {
  const P = {
    sparkles: '<path d="M12 3.5l1.7 4.8 4.8 1.7-4.8 1.7L12 16.5l-1.7-4.8L5.5 10l4.8-1.7z"/><path d="M18.5 15l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    refresh: '<path d="M20.5 11.5a8.5 8.5 0 0 0-15-4.7L3.5 9"/><path d="M3.5 4v5h5"/><path d="M3.5 12.5a8.5 8.5 0 0 0 15 4.7l2-2.2"/><path d="M20.5 20v-5h-5"/>',
    rotate: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="M20 6.5 9.5 17 4 11.5"/>',
    download: '<path d="M12 4v11"/><path d="m7 10.5 5 5 5-5"/><path d="M5 20h14"/>',
    upload: '<path d="M12 20V9"/><path d="m7 13.5 5-5 5 5"/><path d="M5 4h14"/>',
    copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>',
    power: '<path d="M12 3v8.5"/><path d="M17.7 6.8a8 8 0 1 1-11.4 0"/>',
    play: '<path d="M7.5 4.8v14.4a.8.8 0 0 0 1.2.7l11.6-7.2a.8.8 0 0 0 0-1.4L8.7 4.1a.8.8 0 0 0-1.2.7z"/>',
    pause: '<path d="M9 5.5v13M15 5.5v13"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    pencil: '<path d="M16.5 3.5a2.4 2.4 0 0 1 3.4 3.4L8 18.8l-4.5 1.2L4.7 15.5z"/><path d="m14.5 5.5 4 4"/>',
    key: '<circle cx="8" cy="15" r="4.5"/><path d="m11.2 11.8 8.3-8.3M16.5 6.5l2.5 2.5M14 9l2 2"/>',
    up: '<path d="m6 15 6-6 6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    top: '<path d="M6 4h12"/><path d="m7 14 5-5 5 5"/><path d="M12 9v11"/>',
    bottom: '<path d="M6 20h12"/><path d="m7 10 5 5 5-5"/><path d="M12 15V4"/>',
    left: '<path d="M19 12H5M11 18l-6-6 6-6"/>',
    right: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    music: '<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    alert: '<path d="M10.3 4.2 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    grip: '<circle cx="9" cy="6" r="1.2"/><circle cx="15" cy="6" r="1.2"/><circle cx="9" cy="12" r="1.2"/><circle cx="15" cy="12" r="1.2"/><circle cx="9" cy="18" r="1.2"/><circle cx="15" cy="18" r="1.2"/>',
    diamond: '<path d="M12 3.5 20.5 12 12 20.5 3.5 12z"/>',
    gauge: '<path d="M3.5 16a8.5 8.5 0 1 1 17 0"/><path d="m12 16 3.5-4.5"/><circle cx="12" cy="16" r="1.2"/>',
    /* navigation */
    home: '<rect x="3.5" y="3.5" width="7" height="8" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="5" rx="1.8"/><rect x="13.5" y="11.5" width="7" height="9" rx="1.8"/><rect x="3.5" y="14.5" width="7" height="6" rx="1.8"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.2A6.5 6.5 0 0 1 21.5 20"/>',
    cpu: '<rect x="5" y="5" width="14" height="14" rx="2.5"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9.5 2v3M14.5 2v3M9.5 19v3M14.5 19v3M2 9.5h3M2 14.5h3M19 9.5h3M19 14.5h3"/>',
    chat: '<path d="M20.5 14.5a2 2 0 0 1-2 2H8l-4.5 4v-14a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/><path d="M8 9h8M8 12.5h5"/>',
    scan: '<path d="M3.5 8V6A2.5 2.5 0 0 1 6 3.5h2M16 3.5h2A2.5 2.5 0 0 1 20.5 6v2M20.5 16v2a2.5 2.5 0 0 1-2.5 2.5h-2M8 20.5H6A2.5 2.5 0 0 1 3.5 18v-2"/><circle cx="11.5" cy="11.5" r="3.5"/><path d="m14 14 2.5 2.5"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.5 3.5 5.3 3.5 8.5s-1.1 6-3.5 8.5c-2.4-2.5-3.5-5.3-3.5-8.5s1.1-6 3.5-8.5z"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2.2"/><circle cx="10" cy="17" r="2.2"/>',
    logs: '<path d="M14.5 3.5H7A2.5 2.5 0 0 0 4.5 6v12A2.5 2.5 0 0 0 7 20.5h10a2.5 2.5 0 0 0 2.5-2.5V8.5z"/><path d="M14.5 3.5v5h5M8.5 12.5h7M8.5 16h5"/>',
    logout: '<path d="M14 4.5h3.5a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H14"/><path d="M10 16.5 5.5 12 10 7.5M5.5 12H15"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="m20.5 15.5-5-5L6 19.5"/>',
    link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
  };
  const GLYPH = {
    "✦": "sparkles", "⟳": "refresh", "⟲": "rotate", "＋": "plus", "+": "plus", "✓": "check",
    "↧": "download", "↥": "upload", "⧉": "copy", "⏻": "power", "▶": "play", "⏸": "pause",
    "■": "stop", "✕": "x", "✎": "pencil", "⎘": "key", "▲": "up", "▼": "down", "⤒": "top", "⤓": "bottom",
    "←": "left", "→": "right", "♫": "music", "⚠": "alert", "☰": "menu", "≡": "grip",
    "◇": "diamond", "◆": "diamond", "▣": "image", "◐": "power",
  };
  function svg(name, cls) {
    return `<svg class="i ${cls || ""}" data-i="${name}" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[name]}</svg>`;
  }
  /* ✦ means "AI companion" everywhere except the account usage/limits action */
  function iconFor(el, glyph) {
    if (glyph === "✦" && (el.matches("[data-usage]") || el.closest('[data-ctx="usage"]'))) return "gauge";
    return GLYPH[glyph];
  }
  function make(name, cls) {
    const t = document.createElement("template");
    t.innerHTML = svg(name, cls);
    return t.content.firstChild;
  }
  window.MemuIcons = { svg, names: Object.keys(P) };

  const BTN = "button, .btn";
  const WHOLE = ".ctx-ico, .drag-handle, .companion-mark, .pc-empty-mark, .comp-hello, .lm-caret, .p-icon, .pick-tile .t-ico, .chip-ico";
  /* Round 31: provider marks keep their own glyphs (✦ Genspark, ▲ Antigravity…); only «⚿» (Custom API),
     missing from many Windows fonts as a tofu box, becomes the key icon. */
  const PROVIDER_MARK = { "⚿": "key" };

  function decorateButton(el) {
    if (el.closest("svg") || el.matches("select, option")) return;
    if (el.childElementCount === 0 || el.firstChild?.nodeType === 3) {
      const first = el.firstChild;
      if (first && first.nodeType === 3) {
        const text = first.nodeValue;
        const m = /^\s*([✦⟳⟲＋+✓↧↥⧉⏻▶⏸■✕✎⎘▲▼⤒⤓←♫⚠☰◇◆▣])(?:\uFE0F)?(\s+|$)/.exec(text);
        if (m && GLYPH[m[1]] && !(m[1] === "+" && !m[2])) {
          const rest = text.slice(m[0].length);
          first.nodeValue = rest;
          el.insertBefore(make(iconFor(el, m[1])), first);
        }
      }
    }
    const last = el.lastChild;
    tidy(el);
    if (last && last.nodeType === 3) {
      const m = /(\s+)(→)\s*$/.exec(last.nodeValue);
      if (m) { last.nodeValue = last.nodeValue.slice(0, m.index); el.appendChild(make(GLYPH[m[2]])); }
    }
  }
  function tidy(el) {
    const only = el.childElementCount === 1 && el.firstElementChild.tagName.toLowerCase() === "svg" && !el.textContent.trim();
    el.classList.toggle("is-icon", only);
  }
  function decorateWhole(el) {
    const text = el.textContent.trim();
    if (el.childElementCount) return;
    if (el.matches(".p-icon, .pick-tile .t-ico, .chip-ico")) {
      if (!PROVIDER_MARK[text]) return;
      el.textContent = "";
      el.appendChild(make(PROVIDER_MARK[text]));
      return;
    }
    if (!GLYPH[text]) return;
    el.textContent = "";
    el.appendChild(make(iconFor(el, text)));
  }
  function scan(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches(BTN)) decorateButton(root);
    if (root.matches(WHOLE)) decorateWhole(root);
    root.querySelectorAll(BTN).forEach(decorateButton);
    root.querySelectorAll(WHOLE).forEach(decorateWhole);
  }
  let queued = new Set();
  let scheduled = false;
  function flush() {
    scheduled = false;
    const items = Array.from(queued); queued = new Set();
    items.forEach((node) => { if (node.isConnected) scan(node); });
  }
  function queue(node) {
    if (!node) return;
    if (node.nodeType === 3) node = node.parentElement;
    if (!node || node.nodeType !== 1) return;
    const owner = node.closest(BTN + "," + WHOLE);
    queued.add(owner || node);
    if (!scheduled) { scheduled = true; queueMicrotask(flush); }
  }
  /* topbar breadcrumb follows the active tab (the label keeps its i18n key) */
  function syncCrumb() {
    const page = document.getElementById("crumb-page");
    const label = document.querySelector(".nav-item.active[data-tab] .txt");
    if (!page || !label) return;
    page.dataset.i18n = label.dataset.i18n || "";
    page.textContent = label.textContent;
  }
  function start() {
    scan(document.body);
    syncCrumb();
    document.addEventListener("click", (e) => {
      if (e.target.closest && e.target.closest(".nav-item[data-tab]")) setTimeout(syncCrumb, 0);
    });
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "characterData") { queue(r.target); continue; }
        if (r.target.nodeType === 1 && (r.target.matches(BTN) || r.target.matches(WHOLE))) queue(r.target);
        r.addedNodes.forEach((n) => { if (n.nodeType === 1) queue(n); else if (n.nodeType === 3) queue(n.parentElement); });
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
