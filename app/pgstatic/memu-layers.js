/* MemuAI — единый менеджер всплывающих слоёв (Round 24, r24-quick-settings).

   window.MemuLayers:
     open({el, anchor, owner?, close(reason), kind?, focus?}) → handle   — регистрирует открытый слой.
         Открытие нового слоя закрывает все остальные (кроме «родителя»: если anchor лежит внутри уже
         открытого слоя — это вложенное меню). close(reason) обязан спрятать слой (reason: "outside" |
         "escape" | "blur" | "resize" | "scroll" | "detached" | "replaced" | "nav" | "api").
     close(el | handle, reason?)   — закрыть конкретный слой;   closeAll(reason?) — закрыть все.
     isOpen(el?)                    — открыт ли (любой) слой.
     place(menu, anchor, {gap, maxHeight, minWidth, width, boundary, align}) — позиционирование ВНУТРИ вьюпорта
         (и внутри boundary, если задан): вниз, а если не влезает и сверху больше места — вверх.
     listKeys(menu, itemSelector, event) → bool — стрелки/Home/End/Enter для списка; true = событие обработано.
     addCloser(fn) — внешний «закрыватель» (старые меню, у которых своя логика), вызывается в closeAll.
     float(el, anchor, opts) / unfloat(el) — r41: перенести слой в общий верхний слой (body, fixed, флип) и вернуть.
     sweep() — убрать «висящие» узлы: меню, перенесённые в <body>, чей владелец исчез из DOM.

   Глобально: pointerdown вне слоя и его якоря, Escape (закрывает верхний слой и гасит событие, чтобы
   настройки не закрыли заодно подстраницу), blur окна, resize, прокрутка вне слоя, скрытие вкладки,
   смена раздела настроек (sx:sub / клик по разделу), исчезновение якоря из DOM (перерисовка). */
(function () {
  "use strict";
  if (window.MemuLayers) return;
  const stack = [];            // [{el, anchor, owner, close, kind, focus}]
  const closers = new Set();
  let observer = null;
  let sweepFrame = 0;

  const contains = (node, target) => !!(node && target && (node === target || (node.contains && node.contains(target))));
  const find = (x) => stack.findIndex((l) => l === x || l.el === x || l.anchor === x);

  function safeClose(layer, reason) {
    try { layer.close && layer.close(reason); } catch (e) { /* закрыватель не должен ломать остальные */ }
    // Слой, перенесённый в body, чей якорь пропал, удаляем совсем — иначе он «висит» навсегда.
    if (reason === "detached" && layer.el && layer.el.parentNode === document.body && !(layer.anchor && layer.anchor.isConnected)) {
      try { layer.el.remove(); } catch (e) {}
    }
    if (layer.el && layer.el.isConnected) {
      try { layer.el.setAttribute("data-layer-closed", "1"); } catch (e) {}
    }
    if (layer.anchor && layer.anchor.setAttribute && layer.anchor.hasAttribute("aria-expanded")) {
      try { layer.anchor.setAttribute("aria-expanded", "false"); } catch (e) {}
    }
  }

  function close(x, reason = "api") {
    const i = find(x);
    if (i < 0) return false;
    // Закрываем и всех «детей» выше по стеку.
    const removed = stack.splice(i);
    for (let k = removed.length - 1; k >= 0; k--) safeClose(removed[k], reason);
    if (!stack.length) stopObserver();
    return true;
  }

  function closeAll(reason = "api") {
    const removed = stack.splice(0);
    for (let k = removed.length - 1; k >= 0; k--) safeClose(removed[k], reason);
    stopObserver();
    closers.forEach((fn) => { try { fn(reason); } catch (e) {} });
    sweep();
  }

  function open(o) {
    if (!o || !o.el) return null;
    const existing = find(o.el);
    if (existing >= 0) stack.splice(existing, 1);
    // Родитель — открытый слой, внутри которого лежит якорь нового (вложенное меню).
    let keep = 0;
    for (let i = 0; i < stack.length; i++) if (contains(stack[i].el, o.anchor)) keep = i + 1;
    const removed = stack.splice(keep);
    for (let k = removed.length - 1; k >= 0; k--) safeClose(removed[k], "replaced");
    if (!keep) closers.forEach((fn) => { try { fn("replaced", o.el); } catch (e) {} });
    const layer = { el: o.el, anchor: o.anchor || null, owner: o.owner || o.anchor || null, close: o.close, kind: o.kind || "menu", focus: o.focus !== false, items: o.items || "" };
    try { o.el.removeAttribute("data-layer-closed"); } catch (e) {}
    if (layer.anchor && layer.anchor.setAttribute) try { layer.anchor.setAttribute("aria-expanded", "true"); } catch (e) {}
    stack.push(layer);
    startObserver();
    return layer;
  }

  function top() { prune(); return stack[stack.length - 1] || null; }

  /* Слой, который чужой код уже спрятал (display:none / убрал из DOM), не должен «съедать» Escape и клики. */
  function alive(l) {
    const el = l.el;
    if (!el || !el.isConnected || el.hidden) return false;
    if (el.getClientRects && !el.getClientRects().length) return false;
    try { const cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none") return false; } catch (e) {}
    return true;
  }
  function prune() {
    for (let i = stack.length - 1; i >= 0; i--) if (!alive(stack[i])) stack.splice(i, 1);
    if (!stack.length) stopObserver();
  }

  /* --- исчезновение якоря (перерисовка) --- */
  function checkDetached() {
    for (let i = stack.length - 1; i >= 0; i--) {
      const l = stack[i];
      const ownerGone = l.owner && !l.owner.isConnected;
      const elGone = !l.el.isConnected;
      if (ownerGone || elGone) close(l, "detached");
    }
    sweep();
  }
  function startObserver() {
    if (observer || !window.MutationObserver || !document.body) return;
    observer = new MutationObserver(() => {
      if (sweepFrame) return;
      sweepFrame = requestAnimationFrame(() => { sweepFrame = 0; checkDetached(); });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
  function stopObserver() { if (observer) { observer.disconnect(); observer = null; } }

  /* «Висящие» узлы: меню pretty-select / mpick / любые [data-layer-owner], перенесённые в body,
     чей владелец уже не в документе. Безопасно вызывать сколько угодно раз. */
  function sweep() {
    if (!document.body) return;
    document.querySelectorAll("body > .pretty-select-menu, body > [data-layer-orphanable]").forEach((menu) => {
      const owner = menu._layerOwner || null;
      const open = stack.some((l) => l.el === menu);
      if (open && owner && owner.isConnected) return;
      if (!owner || !owner.isConnected) { menu.remove(); return; }
      if (!open) { menu.classList.remove("visible"); if (menu.hasAttribute("data-memu-layer")) { unfloat(menu); return; } try { owner.appendChild(menu); } catch (e) { menu.remove(); } }
    });
  }

  /* --- позиционирование --- */
  function place(menu, anchor, o = {}) {
    if (!menu || !anchor) return;
    const gap = o.gap == null ? 6 : o.gap;
    const pad = 8;
    const r = anchor.getBoundingClientRect();
    const b = o.boundary ? o.boundary.getBoundingClientRect() : null;
    const vw = window.innerWidth, vh = window.innerHeight;
    const lower = Math.min(vh - pad, b ? b.bottom : vh - pad);
    const upper = Math.max(pad, b ? b.top : pad);
    const maxH = o.maxHeight || 320;
    menu.style.maxHeight = "";
    const csv = getComputedStyle(menu);
    const bordersV = (parseFloat(csv.borderTopWidth) || 0) + (parseFloat(csv.borderBottomWidth) || 0);
    const natural = Math.min(maxH, Math.ceil((menu.scrollHeight || maxH) + bordersV));
    const below = lower - r.bottom - gap;
    const above = r.top - upper - gap;
    const up = below < natural && above > below;
    const avail = Math.max(64, up ? above : below);
    const height = Math.min(natural, avail);
    // r29d-chat4: measure the natural width (a width left from the last opening or a pending layout made
    // scrollWidth a few px short) and add the borders — scrollWidth excludes them, the menu is border-box,
    // so the longest option was cut with «…» by 2–6 px («Только по просьбе, иначе предлож…»).
    menu.style.width = "";
    const cs = getComputedStyle(menu);
    const borders = (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0);
    const natural_w = Math.ceil((menu.scrollWidth || 0) + borders);
    const width = o.width ? Math.min(o.width, vw - pad * 2) : Math.min(Math.max(r.width, o.minWidth || 0, Math.min(natural_w, 420)), vw - pad * 2);
    menu.style.position = "fixed";
    menu.style.width = width + "px";
    menu.style.maxHeight = Math.min(maxH, avail) + "px";
    menu.style.overflowY = "auto";
    // a scrolling list loses its scrollbar's width: give it back so options are not cut
    const lost = menu.offsetWidth - menu.clientWidth - borders;
    if (lost > 0 && menu.scrollHeight > menu.clientHeight) menu.style.width = Math.min(width + lost, vw - pad * 2) + "px";
    const placedW = parseFloat(menu.style.width) || width;
    let left = o.align === "end" ? r.right - placedW : r.left;
    left = Math.max(pad, Math.min(left, vw - placedW - pad));
    let topPx = up ? r.top - gap - height : r.bottom + gap;
    topPx = Math.max(pad, Math.min(topPx, vh - pad - height));
    menu.style.left = Math.round(left) + "px";
    menu.style.top = Math.round(topPx) + "px";
    menu.dataset.side = up ? "top" : "bottom";
  }

  /* --- r41-ui: общий верхний слой ---
     Меню, оставленное внутри своей строки, жило в её stacking context: соседние поля (sx-row, заголовки секций)
     и сам диалог настроек (#panel-back) рисовались ПОВЕРХ него, а overflow прокручиваемого предка обрезал его.
     float() переносит слой в <body> (position:fixed, z-index верхнего слоя, флип у краёв), unfloat() возвращает
     домой и стирает позиционирование. Владелец (_layerOwner) нужен sweep(): сирота без владельца удаляется. */
  const TOP_Z = 100100;
  function float(el, anchor, o = {}) {
    if (!el || !anchor) return;
    if (el.parentNode !== document.body) {
      if (!el._layerOwner) el._layerOwner = o.owner || el.parentNode;
      el.setAttribute("data-layer-orphanable", "");
      document.body.appendChild(el);
    }
    el.setAttribute("data-memu-layer", o.kind || "menu");
    el.style.position = "fixed";
    el.style.zIndex = String(TOP_Z);
    place(el, anchor, o);
  }
  function unfloat(el) {
    if (!el) return;
    ["position", "left", "top", "width", "maxHeight", "overflowY", "zIndex"].forEach((k) => { el.style[k] = ""; });
    el.removeAttribute("data-memu-layer");
    delete el.dataset.side;
    if (el.parentNode !== document.body) return;
    const owner = el._layerOwner;
    if (owner && owner.isConnected) owner.appendChild(el); else el.remove();
  }

  /* --- клавиатура для списков --- */
  function listKeys(menu, itemSelector, event) {
    const items = Array.from(menu.querySelectorAll(itemSelector)).filter((n) => !n.disabled && n.getAttribute("aria-disabled") !== "true");
    if (!items.length) return false;
    let i = items.findIndex((n) => n.classList.contains("active"));
    if (i < 0) i = items.findIndex((n) => n.classList.contains("selected") || n.getAttribute("aria-selected") === "true");
    const k = event.key;
    if (k === "ArrowDown" || k === "ArrowUp" || k === "Home" || k === "End" || k === "PageDown" || k === "PageUp") {
      if (k === "Home") i = 0; else if (k === "End") i = items.length - 1;
      else if (k === "PageDown") i = Math.min(items.length - 1, (i < 0 ? 0 : i) + 8);
      else if (k === "PageUp") i = Math.max(0, (i < 0 ? 0 : i) - 8);
      else i = i < 0 ? (k === "ArrowDown" ? 0 : items.length - 1) : (i + (k === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((n, j) => n.classList.toggle("active", j === i));
      const cur = items[i];
      if (!cur.id) cur.id = "ml-opt-" + Math.random().toString(36).slice(2, 9);
      menu.setAttribute("aria-activedescendant", cur.id);
      try { cur.scrollIntoView({ block: "nearest" }); } catch (e) {}
      event.preventDefault();
      return true;
    }
    if ((k === "Enter" || k === " ") && i >= 0) { event.preventDefault(); items[i].click(); return true; }
    return false;
  }

  /* --- глобальные обработчики --- */
  function onPointerDown(ev) {
    prune();
    if (!stack.length) return;
    const t = ev.target;
    // Закрываем все слои, вне которых (и вне их якорей) был клик — сверху вниз.
    for (let i = stack.length - 1; i >= 0; i--) {
      const l = stack[i];
      if (contains(l.el, t) || contains(l.anchor, t)) break;
      close(l, "outside");
    }
  }
  function onKey(ev) {
    if (!stack.length) return;
    const l = top();
    if (!l) return;
    // Стрелки/Enter для меню, у которых нет своего фокуса (контекстное меню): только если фокус не в поле ввода.
    if (ev.key !== "Escape") {
      const a = document.activeElement;
      const typing = a && (a.matches && a.matches("input, textarea, select, [contenteditable=''], [contenteditable='true']")) && !contains(l.el, a);
      if (l.items && !typing && !ev.altKey && !ev.ctrlKey && !ev.metaKey && listKeys(l.el, l.items, ev)) ev.stopPropagation();
      return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    close(l, "escape");
    if (l.focus && l.anchor && l.anchor.focus && l.anchor.isConnected) try { l.anchor.focus({ preventScroll: true }); } catch (e) {}
  }
  function onScroll(ev) {
    if (!stack.length) return;
    const t = ev.target;
    if (stack.some((l) => contains(l.el, t))) return;
    closeAll("scroll");
  }

  document.addEventListener("pointerdown", onPointerDown, true);
  // Захват на window — раньше обработчиков настроек (Esc закрывает только меню, не подстраницу).
  window.addEventListener("keydown", onKey, true);
  document.addEventListener("scroll", onScroll, true);
  window.addEventListener("blur", () => { if (stack.length) closeAll("blur"); });
  window.addEventListener("resize", () => { if (stack.length) closeAll("resize"); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && stack.length) closeAll("blur"); });
  window.addEventListener("hashchange", () => closeAll("nav"));
  window.addEventListener("popstate", () => closeAll("nav"));
  document.addEventListener("sx:sub", () => closeAll("nav"), true);
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t && t.closest && t.closest(".settings-tab-btn, .sx-sub-back, [data-sx-sub]")) closeAll("nav");
  }, true);
  // Страховка: раз в несколько секунд убираем меню-сироты (например, после innerHTML без нашего ведома).
  setInterval(() => { if (document.body) { if (stack.length) checkDetached(); else sweep(); } }, 3000);

  window.MemuLayers = {
    version: 1, open, close, closeAll, isOpen: (el) => (el ? find(el) >= 0 : stack.length > 0),
    place, float, unfloat, TOP_Z, listKeys, sweep, top,
    addCloser: (fn) => { if (typeof fn === "function") closers.add(fn); return () => closers.delete(fn); },
    _stack: stack,
  };
})();
