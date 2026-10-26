

(function () {
  "use strict";
  if (window.MemuCtxMenu) return;
  const ICONS = {
    refresh: '<path d="M20 6v5h-5"/><path d="M19.4 11A7.5 7.5 0 1 0 17 17.3"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    open: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    trash: '<path d="M3.5 6.5h17M8.5 6.5V4.5h7v2M6 6.5l1 13.5h10l1-13.5"/>',
    edit: '<path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z"/><path d="m14 8 3 3"/>',
    play: '<path d="M7 5.5v13a1 1 0 0 0 1.5.86l10.4-6.5a1 1 0 0 0 0-1.72L8.5 4.64A1 1 0 0 0 7 5.5z"/>',
    copy: '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
    fit: '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/><path d="M9 12h6M12 9v6"/>',
    move: '<path d="M12 3v18M3 12h18"/><path d="m9 6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    undo: '<path d="M9 7 4 12l5 5"/><path d="M4 12h10.5a5.5 5.5 0 0 1 0 11H12"/>',
    redo: '<path d="m15 7 5 5-5 5"/><path d="M20 12H9.5a5.5 5.5 0 0 0 0 11H12"/>',
    pin: '<path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/>',
    hide: '<path d="M3 3l18 18"/><path d="M10.6 6.1A9.6 9.6 0 0 1 12 6c5 0 8.5 4.5 9.5 6a15 15 0 0 1-2.7 3.3M6.4 6.9A14.7 14.7 0 0 0 2.5 12c1 1.5 4.5 6 9.5 6a9.3 9.3 0 0 0 4.6-1.2"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    home: '<path d="M4 11.5 12 5l8 6.5"/><path d="M6.5 10v9.5h11V10"/>',
    sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    appbar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 9.5h17"/>',
    layout: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M10 10v9.5"/>',
    grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7 14h10"/>',
    size: '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M14 8h2v2M10 16H8v-2M16 8l-3.5 3.5M8 16l3.5-3.5"/>',
    bolt: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
    chat: '<path d="M5 18.5V6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v7a2.5 2.5 0 0 1-2.5 2.5H9z"/>',
    front: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 4H6a2 2 0 0 0-2 2v10"/>',
    reset: '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v4.5h4.5"/>',

    ztop: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="6.5" y="7" width="11" height="3" rx="1"/>',
    zcomposer: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M8 9h8"/><rect x="6.5" y="12" width="11" height="4" rx="1.6"/>',
    zhome: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="6.5" y="7" width="11" height="3.2" rx="1.4"/><path d="M6.5 13.5h4.5M13 13.5h4.5M6.5 16.5h4.5M13 16.5h4.5"/>',
    zleft: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="6" y="7" width="4" height="10" rx="1"/>',
    zright: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="14" y="7" width="4" height="10" rx="1"/>',
    zside: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15M5.5 8h2M5.5 10.5h2"/>',
    zsbottom: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15M3.5 15h6"/>',
    zbar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 9h17M13 6.8h4.5"/>',
  };
  const svg = (name) => (ICONS[name] ? `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>` : "");
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const reduced = () => document.documentElement.dataset.motion === "off" || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  const CSS = `
.mcm { position: fixed; z-index: 2147483000; min-width: 216px; max-width: min(340px, calc(100vw - 16px)); padding: 5px; box-sizing: border-box;
  display: flex; flex-direction: column; gap: 1px; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin;
  border-radius: 13px; background: var(--panel-2, var(--panel, #232428)); color: var(--text, #eee);
  border: 1px solid color-mix(in srgb, var(--border, #3a3b40) 85%, transparent);
  box-shadow: 0 1px 0 color-mix(in srgb, #fff 6%, transparent) inset, 0 18px 46px -14px rgba(0, 0, 0, .55), 0 2px 8px -2px rgba(0, 0, 0, .25);
  font: 500 13px/1.25 var(--sans, system-ui, sans-serif); -webkit-user-select: none; user-select: none; outline: none; transform-origin: var(--mcm-origin, top left); }
.mcm:not([data-still]) { animation: mcm-in .16s cubic-bezier(.2, .9, .25, 1.06) both; }
@keyframes mcm-in { from { opacity: 0; transform: scale(.94) translateY(-2px); } to { opacity: 1; transform: none; } }
.mcm-head { display: flex; align-items: center; gap: 9px; padding: 6px 9px 7px; min-width: 0; }
.mcm-head-ico { width: 26px; height: 26px; flex: none; display: grid; place-items: center; border-radius: 8px; color: var(--accent, #d97757);
  background: color-mix(in srgb, var(--accent, #d97757) 14%, transparent); }
.mcm-head-ico svg { width: 15px; height: 15px; }
.mcm-head-text { min-width: 0; display: grid; gap: 1px; }
.mcm-head-text b { font: 650 13px/1.2 var(--sans, system-ui); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mcm-head-text small { font: 500 11px/1.2 var(--sans, system-ui); color: var(--muted, #999); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mcm-item { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 32px; padding: 6px 10px 6px 9px; box-sizing: border-box; border: 0; border-radius: 8px;
  background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; outline: none; }
.mcm-item svg, .mcm-seg-label svg { width: 16px; height: 16px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.mcm-item > svg { color: var(--muted, #999); }
.mcm-item .mcm-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mcm-item .mcm-hint { flex: none; margin-left: 12px; font: 600 11px/1 var(--sans, system-ui); color: var(--muted, #999); opacity: .85; letter-spacing: .01em; }
.mcm-item .mcm-check { flex: none; width: 15px; height: 15px; color: var(--accent, #d97757); }
.mcm-item .mcm-gap { flex: none; width: 15px; }
.mcm-item:hover, .mcm-item:focus-visible, .mcm-item.mcm-active { background: color-mix(in srgb, var(--text, #fff) 8%, transparent); }
.mcm-item:hover > svg, .mcm-item.mcm-active > svg { color: var(--text, #eee); }
.mcm-item:active { background: color-mix(in srgb, var(--text, #fff) 12%, transparent); }
.mcm-item.danger { color: var(--danger, #e5484d); }
.mcm-item.danger > svg { color: currentColor; }
.mcm-item.danger:hover, .mcm-item.danger.mcm-active { background: color-mix(in srgb, var(--danger, #e5484d) 13%, transparent); }
.mcm-item[aria-disabled="true"] { opacity: .45; cursor: default; }
.mcm-item[aria-disabled="true"]:hover { background: none; }
.mcm-sep { height: 1px; margin: 4px 7px; background: color-mix(in srgb, var(--border, #3a3b40) 80%, transparent); flex: none; }
.mcm-label-row { padding: 7px 10px 3px; font: 650 10.5px/1 var(--sans, system-ui); color: var(--muted, #999); text-transform: uppercase; letter-spacing: .06em; }
.mcm-seg { display: flex; align-items: center; gap: 10px; padding: 4px 6px 4px 9px; min-height: 34px; box-sizing: border-box; border-radius: 8px; }
.mcm-seg.mcm-active { background: color-mix(in srgb, var(--text, #fff) 5%, transparent); }
.mcm-seg-label { flex: 1; display: flex; align-items: center; gap: 10px; min-width: 0; color: inherit; white-space: nowrap; }
.mcm-seg-label svg { color: var(--muted, #999); }
.mcm-seg-btns { display: inline-flex; padding: 2px; gap: 2px; border-radius: 9px; background: color-mix(in srgb, var(--text, #fff) 6%, transparent); }
.mcm-seg-btns button { min-width: 30px; height: 25px; padding: 0 7px; border: 0; border-radius: 7px; background: none; color: var(--muted, #999);
  font: 650 11.5px/1 var(--sans, system-ui); cursor: pointer; outline: none; }
.mcm-seg-btns button:hover { color: var(--text, #eee); background: color-mix(in srgb, var(--text, #fff) 7%, transparent); }
.mcm-seg-btns button[aria-pressed="true"] { background: var(--accent, #d97757); color: var(--on-accent, #fff); }
.mcm-seg-btns button.mcm-active { box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent, #d97757) 55%, transparent); }
:root[data-theme="light"] .mcm { box-shadow: 0 18px 46px -16px rgba(0, 0, 0, .28), 0 2px 8px -2px rgba(0, 0, 0, .12); }
:root[data-design="glass"] .mcm { background: color-mix(in srgb, var(--panel, #232428) 72%, transparent); -webkit-backdrop-filter: blur(22px) saturate(1.4); backdrop-filter: blur(22px) saturate(1.4); }
:root[data-design="grid"] .mcm, :root[data-design="grid"] .mcm-item, :root[data-design="grid"] .mcm-seg-btns, :root[data-design="grid"] .mcm-seg-btns button { border-radius: 3px; }
:root[data-design="brutal"] .mcm { border: 2px solid var(--text, #111); box-shadow: 4px 4px 0 var(--text, #111); border-radius: 0; }
:root[data-design="terminal"] .mcm { font-family: var(--mono, ui-monospace, monospace); border-radius: 4px; }
:root[data-motion="off"] .mcm { animation: none !important; }
@media (prefers-reduced-motion: reduce) { .mcm { animation: none !important; } }`;

  let cur = null;
  function ensureCss() {
    if (document.getElementById("mcm-style")) return;
    const st = document.createElement("style");
    st.id = "mcm-style";
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }

  function rowHtml(it, i) {
    const type = it.type || "item";
    if (type === "sep") return '<div class="mcm-sep" role="separator"></div>';
    if (type === "label") return `<div class="mcm-label-row" role="presentation">${esc(it.label)}</div>`;
    if (type === "head") {
      return `<div class="mcm-head" role="presentation">${it.icon ? `<span class="mcm-head-ico">${svg(it.icon) || it.icon}</span>` : ""}<span class="mcm-head-text"><b>${esc(it.label)}</b>${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</span></div>`;
    }
    if (type === "seg") {
      const opts = (it.options || []).map((o) => `<button type="button" tabindex="-1" data-mcm-opt="${esc(o.id)}" aria-pressed="${o.pressed ? "true" : "false"}"${o.title ? ` title="${esc(o.title)}"` : ""}>${esc(o.label)}</button>`).join("");
      return `<div class="mcm-seg" role="group" data-mcm-i="${i}" data-mcm-stop="1"${it.label ? ` aria-label="${esc(it.label)}"` : ""}><span class="mcm-seg-label">${svg(it.icon)}${it.label ? `<span>${esc(it.label)}</span>` : ""}</span><span class="mcm-seg-btns">${opts}</span></div>`;
    }
    const checkable = it.checked != null;
    const mark = checkable ? (it.checked ? `<svg class="mcm-check" viewBox="0 0 24 24" aria-hidden="true">${ICONS.check}</svg>` : '<span class="mcm-gap"></span>') : "";
    return `<button type="button" tabindex="-1" class="mcm-item${it.danger ? " danger" : ""}" role="${checkable ? "menuitemcheckbox" : "menuitem"}"${checkable ? ` aria-checked="${it.checked ? "true" : "false"}"` : ""}${it.disabled ? ' aria-disabled="true"' : ""} data-mcm-i="${i}" data-mcm-stop="1"${it.title ? ` title="${esc(it.title)}"` : ""}>${svg(it.icon) || '<span class="mcm-gap" style="width:16px"></span>'}<span class="mcm-label">${esc(it.label)}</span>${it.hint ? `<span class="mcm-hint">${esc(it.hint)}</span>` : ""}${mark}</button>`;
  }

  function stops(el) { return Array.from(el.querySelectorAll("[data-mcm-stop]")).filter((n) => n.getAttribute("aria-disabled") !== "true"); }
  function setActive(el, node, focus = true) {
    el.querySelectorAll(".mcm-active").forEach((n) => n.classList.remove("mcm-active"));
    if (!node) return;
    node.classList.add("mcm-active");
    if (node.classList.contains("mcm-seg")) {
      const pressed = node.querySelector('[aria-pressed="true"]') || node.querySelector("button");
      if (pressed) pressed.classList.add("mcm-active");
    }
    if (focus) { try { node.focus({ preventScroll: true }); } catch (e) {  } if (node.tagName !== "BUTTON") el.focus({ preventScroll: true }); }
    try { node.scrollIntoView({ block: "nearest" }); } catch (e) {  }
  }

  function boundaryOf(o) {
    const b = o.boundary || {};
    const vw = window.innerWidth, vh = window.innerHeight;
    return { left: Math.max(0, b.left ?? 0), top: Math.max(0, b.top ?? 0), right: Math.min(vw, b.right ?? vw), bottom: Math.min(vh, b.bottom ?? vh) };
  }
  function place(el, o) {
    const pad = 6;
    const B = boundaryOf(o);
    el.style.maxHeight = Math.max(120, B.bottom - B.top - 2 * pad) + "px";
    el.style.left = "0px"; el.style.top = "0px";
    const w = el.offsetWidth, h = el.offsetHeight;
    let ax, ay, aw = 0, ah = 0;
    if (o.x != null && o.y != null) { ax = o.x; ay = o.y; }
    else {
      const r = o.anchor ? (typeof o.anchor.getBoundingClientRect === "function" ? o.anchor.getBoundingClientRect() : o.anchor) : { left: B.left, top: B.top, width: 0, height: 0 };
      ax = r.left; ay = r.top; aw = r.width || 0; ah = r.height || 0;
      if (aw > 120 && ah > 60) { ax = r.left + 12; ay = r.top + 12; aw = 0; ah = 0; }
    }
    let left = ax, top = ay + ah + (ah ? 4 : 2), ox = "left", oy = "top";
    if (left + w > B.right - pad) { left = (aw ? ax + aw : ax) - w; ox = "right"; }
    if (top + h > B.bottom - pad) { const up = ay - h - (ah ? 4 : 2); if (up >= B.top + pad || up > B.bottom - pad - h - top) { top = up; oy = "bottom"; } }
    left = Math.max(B.left + pad, Math.min(left, B.right - pad - w));
    top = Math.max(B.top + pad, Math.min(top, B.bottom - pad - h));
    el.style.left = Math.round(left) + "px";
    el.style.top = Math.round(top) + "px";
    el.style.setProperty("--mcm-origin", oy + " " + ox);
  }

  function close(reason = "api") {
    const c = cur;
    if (!c || c.closing) return false;
    c.closing = true;
    cur = null;
    detach();
    try { c.el.remove(); } catch (e) {  }
    if (c.owner && c.owner.setAttribute && c.owner.hasAttribute("aria-expanded")) c.owner.setAttribute("aria-expanded", "false");
    if (window.MemuLayers && c.layer) { try { MemuLayers.close(c.el, reason); } catch (e) {  } }
    const back = reason === "escape" || reason === "pick";
    if (back && c.opts.returnFocus !== false) {
      const target = c.opts.returnFocus || c.owner;
      if (target && target.isConnected && target.focus) { try { target.focus({ preventScroll: true }); } catch (e) {  } }
    }
    try { c.opts.onClose && c.opts.onClose(reason); } catch (e) { console.warn("[MemuCtxMenu]", e); }
    return true;
  }

  const inside = (t) => !!(cur && t && t.nodeType === 1 && cur.el.contains(t));
  function onDown(e) {
    if (!cur || inside(e.target)) return;
    const c = cur;
    close("outside");
    if (c.opts.swallowOutside) { e.preventDefault(); e.stopPropagation(); c.swallowClick = performance.now(); swallowNext(); }
  }
  function swallowNext() {
    const stop = (ev) => { ev.preventDefault(); ev.stopPropagation(); window.removeEventListener("click", stop, true); };
    window.addEventListener("click", stop, true);
    setTimeout(() => window.removeEventListener("click", stop, true), 450);
  }
  function onKey(e) {
    if (!cur) return;
    const el = cur.el;
    const list = stops(el);
    const at = list.findIndex((n) => n.classList.contains("mcm-active"));
    const k = e.key;
    const done = () => { e.preventDefault(); e.stopPropagation(); if (e.stopImmediatePropagation) e.stopImmediatePropagation(); };
    if (k === "Escape") { done(); close("escape"); return; }
    if (k === "Tab") { done(); if (list.length) setActive(el, list[(at + (e.shiftKey ? -1 : 1) + list.length) % list.length]); return; }
    if (k === "ArrowDown" || k === "ArrowUp" || k === "Home" || k === "End") {
      done();
      if (!list.length) return;
      let i = k === "Home" ? 0 : k === "End" ? list.length - 1 : at < 0 ? (k === "ArrowDown" ? 0 : list.length - 1) : (at + (k === "ArrowDown" ? 1 : -1) + list.length) % list.length;
      setActive(el, list[i]);
      return;
    }
    const node = list[at];
    if ((k === "ArrowLeft" || k === "ArrowRight") && node && node.classList.contains("mcm-seg")) {
      done();
      const btns = Array.from(node.querySelectorAll("button"));
      let j = btns.findIndex((b) => b.classList.contains("mcm-active"));
      j = Math.max(0, Math.min(btns.length - 1, (j < 0 ? 0 : j) + (k === "ArrowRight" ? 1 : -1)));
      btns.forEach((b, n) => b.classList.toggle("mcm-active", n === j));
      return;
    }
    if (k === "Enter" || k === " ") {
      if (!node) return;
      done();
      if (node.classList.contains("mcm-seg")) { const b = node.querySelector("button.mcm-active") || node.querySelector("button"); if (b) pick(node, b.dataset.mcmOpt); }
      else pick(node);
      return;
    }
    if (k.length === 1 && /\S/.test(k) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const ch = k.toLowerCase();
      const from = at + 1;
      for (let n = 0; n < list.length; n++) {
        const cand = list[(from + n) % list.length];
        const label = (cand.querySelector(".mcm-label, .mcm-seg-label") || cand).textContent.trim().toLowerCase();
        if (label.startsWith(ch)) { done(); setActive(el, cand); return; }
      }
    }
  }
  const onBlur = () => { if (cur) close("blur"); };
  const onResize = () => { if (cur) close("resize"); };
  const onScroll = (e) => { if (cur && !inside(e.target)) close("scroll"); };
  const onVis = () => { if (cur && document.hidden) close("hidden"); };
  function attach() {
    document.addEventListener("pointerdown", onDown, true);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("blur", onBlur);
    window.addEventListener("resize", onResize);
    document.addEventListener("scroll", onScroll, true);
    document.addEventListener("wheel", onScroll, { capture: true, passive: true });
    document.addEventListener("visibilitychange", onVis);
  }
  function detach() {
    document.removeEventListener("pointerdown", onDown, true);
    window.removeEventListener("keydown", onKey, true);
    window.removeEventListener("blur", onBlur);
    window.removeEventListener("resize", onResize);
    document.removeEventListener("scroll", onScroll, true);
    document.removeEventListener("wheel", onScroll, { capture: true, passive: true });
    document.removeEventListener("visibilitychange", onVis);
  }

  function pick(node, optId) {
    if (!cur) return;
    const c = cur;
    const it = c.items[+node.dataset.mcmI];
    if (!it || it.disabled) return;
    let keep = !!it.keep;
    if (it.type === "seg") {
      node.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mcmOpt === optId)));
    }
    let res;
    try { res = c.opts.onPick && c.opts.onPick(it.type === "seg" ? optId : it.id, it, optId); } catch (e) { console.warn("[MemuCtxMenu]", e); }
    if (res === false) keep = true;
    if (res === true) keep = false;
    if (!keep && cur === c) close("pick");
  }

  function render(c) {
    c.el.innerHTML = c.items.map(rowHtml).join("");
  }

  function open(o) {
    if (!o || !Array.isArray(o.items)) return null;
    ensureCss();
    close("replaced");
    const el = document.createElement("div");
    el.className = "mcm" + (o.className ? " " + o.className : "");
    el.setAttribute("role", "menu");
    el.tabIndex = -1;
    if (o.label) el.setAttribute("aria-label", o.label);
    if (reduced()) el.dataset.still = "1";
    const items = o.items.filter(Boolean);

    const clean = items.filter((it, i, all) => it.type !== "sep" || (i > 0 && i < all.length - 1 && all[i - 1].type !== "sep"));
    const c = { el, opts: o, items: clean, owner: o.owner || null, closing: false, layer: false };
    render(c);
    (o.container || document.body).appendChild(el);
    place(el, o);
    cur = c;
    el.addEventListener("pointerdown", (e) => { e.stopPropagation(); });
    el.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); });
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const opt = e.target.closest("[data-mcm-opt]");
      const node = e.target.closest("[data-mcm-i]");
      if (!node) return;
      if (node.classList.contains("mcm-seg")) { if (opt) pick(node, opt.dataset.mcmOpt); return; }
      pick(node);
    });
    el.addEventListener("pointermove", (e) => {
      const node = e.target.closest("[data-mcm-stop]");
      if (node && !node.classList.contains("mcm-active")) setActive(el, node, false);
    });
    if (c.owner && c.owner.setAttribute) c.owner.setAttribute("aria-expanded", "true");
    if (window.MemuLayers && typeof MemuLayers.open === "function") {
      try { MemuLayers.open({ el, anchor: c.owner || null, owner: el, close: (reason) => { if (cur === c) close(reason || "replaced"); }, focus: false }); c.layer = true; } catch (e) {  }
    }

    setTimeout(() => { if (cur === c) attach(); }, 0);
    requestAnimationFrame(() => {
      if (cur !== c) return;
      const first = stops(el)[0];
      if (o.keyboard && first) setActive(el, first); else el.focus({ preventScroll: true });
    });
    return {
      el,
      close: (reason) => (cur === c ? close(reason) : false),
      update(next) { if (cur !== c) return; c.items = (next || []).filter(Boolean); render(c); place(el, o); },
    };
  }

  window.MemuCtxMenu = {
    version: 1, open, close, icon: svg, ICONS,
    isOpen: (owner) => !!cur && (owner == null || cur.owner === owner),
    current: () => (cur ? { el: cur.el, owner: cur.owner } : null),
  };
})();
