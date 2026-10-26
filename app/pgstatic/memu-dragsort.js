

(function () {
  "use strict";
  if (window.MemuDragSort) return;

  const THRESHOLD = 5;
  const EDGE = 64;
  const MAX_SPEED = 26;

  let drag = null;
  let swallowClick = 0;

  const scrollableOf = (node) => {
    for (let n = node; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      if (n.nodeType !== 1) continue;
      const style = getComputedStyle(n);
      if (/(auto|scroll)/.test(style.overflowY) && n.scrollHeight > n.clientHeight + 2) return n;
    }
    return null;
  };

  function speedFor(y, rect) {
    const edge = Math.min(EDGE, rect.height / 4);
    let d = 0;
    if (y < rect.top + edge) d = -(rect.top + edge - y);
    else if (y > rect.bottom - edge) d = y - (rect.bottom - edge);
    if (!d) return 0;

    const k = Math.min(1, Math.abs(d) / (edge + 24));
    return Math.sign(d) * Math.max(2, Math.round(MAX_SPEED * k * k + (Math.abs(d) > edge ? 6 : 0)));
  }

  function ghostFor(item, text) {
    const chip = document.createElement("div");
    chip.className = "memu-drag-ghost";
    chip.setAttribute("aria-hidden", "true");
    chip.textContent = text;
    const rect = item.getBoundingClientRect();
    chip.style.maxWidth = Math.max(140, Math.min(320, rect.width)) + "px";
    document.body.appendChild(chip);
    return chip;
  }

  function setTarget(next) {
    const prev = drag.target;
    if (prev && next && prev.el === next.el && prev.cls === next.cls) return;
    if (prev && prev.el) prev.el.classList.remove(prev.cls);
    drag.target = next && next.el ? next : null;
    if (drag.target) drag.target.el.classList.add(drag.target.cls);
  }

  function retarget() {
    if (!drag || !drag.started) return;
    const under = document.elementFromPoint(drag.x, drag.y);
    let next = null;
    try { next = under ? drag.opts.target(under, drag.x, drag.y, drag.item) : null; } catch (e) { next = null; }
    setTarget(next);
  }

  function tick() {
    if (!drag || !drag.started) return;
    const box = drag.scroller;
    if (box && box.isConnected) {

      const now = performance.now(), dt = Math.min(200, Math.max(1, now - (drag.at || now - 16.7)));
      drag.at = now;
      let speed = Math.round(speedFor(drag.y, box.getBoundingClientRect()) * dt / 16.7);

      if (!drag.armed) { if (!speed || Math.abs(drag.y - drag.sy) > 24) drag.armed = true; else speed = 0; }
      if (speed) {
        const before = box.scrollTop;
        box.scrollTop += speed;
        if (box.scrollTop !== before) retarget();
      }
    }
    drag.frame = requestAnimationFrame(tick);
  }

  function begin() {
    const { item, opts } = drag;
    drag.started = true;
    drag.scroller = (opts.scroller && opts.scroller(item)) || scrollableOf(item);
    const text = ((opts.label && opts.label(item)) || item.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80);
    drag.ghost = ghostFor(item, text || "…");
    item.classList.add("dragging");
    document.documentElement.classList.add("memu-dragging");
    try { window.getSelection()?.removeAllRanges(); } catch (e) {}
    try { opts.start && opts.start(item); } catch (e) { console.error("MemuDragSort start", e); }
    document.addEventListener("scroll", retarget, true);
    drag.frame = requestAnimationFrame(tick);
  }

  function moveGhost() {
    if (drag.ghost) drag.ghost.style.transform = `translate(${Math.round(drag.x + 14)}px, ${Math.round(drag.y + 10)}px)`;
  }

  function finish(dropped) {
    const d = drag;
    drag = null;
    window.removeEventListener("pointermove", onMove, true);
    window.removeEventListener("pointerup", onUp, true);
    window.removeEventListener("pointercancel", onCancel, true);
    window.removeEventListener("keydown", onKey, true);
    window.removeEventListener("blur", onCancel);
    document.removeEventListener("selectstart", noSelect, true);
    if (!d || !d.started) return;
    cancelAnimationFrame(d.frame);
    document.removeEventListener("scroll", retarget, true);
    document.documentElement.classList.remove("memu-dragging");
    d.item.classList.remove("dragging");
    if (d.ghost) d.ghost.remove();
    const target = d.target;
    if (target && target.el) target.el.classList.remove(target.cls);
    swallowClick = Date.now();
    let done = false;
    if (dropped && target && target.el) {
      try { d.opts.drop(d.item, target); done = true; } catch (e) { console.error("MemuDragSort drop", e); }
    }
    try { d.opts.end && d.opts.end(d.item, done); } catch (e) { console.error("MemuDragSort end", e); }
  }

  function onMove(event) {
    if (!drag || event.pointerId !== drag.pointer) return;
    drag.x = event.clientX; drag.y = event.clientY;
    if (!drag.started) {
      if (Math.hypot(drag.x - drag.sx, drag.y - drag.sy) < THRESHOLD) return;
      begin();
    }
    event.preventDefault();
    moveGhost();
    retarget();
  }

  function noSelect(event) { if (drag) event.preventDefault(); }
  function onUp(event) { if (drag && event.pointerId === drag.pointer) finish(true); }
  function onCancel() { if (drag) finish(false); }
  function onKey(event) {
    if (!drag || !drag.started || event.key !== "Escape") return;
    event.preventDefault(); event.stopPropagation();
    finish(false);
  }

  document.addEventListener("click", (event) => {
    if (swallowClick && Date.now() - swallowClick < 400) { event.preventDefault(); event.stopPropagation(); }
    swallowClick = 0;
  }, true);

  function bind(root, opts) {
    if (!root || !opts || !opts.item) return () => {};
    const down = (event) => {
      if (drag || event.button !== 0 || event.pointerType === "touch" || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const item = event.target.closest && event.target.closest(opts.item);
      if (!item || !root.contains(item)) return;
      if (opts.handle && !event.target.closest(opts.handle)) return;
      if (opts.ignore && event.target.closest(opts.ignore)) return;
      if (event.target.closest("input, textarea, select, [contenteditable='true']")) return;
      drag = { item, opts, pointer: event.pointerId, sx: event.clientX, sy: event.clientY, x: event.clientX, y: event.clientY,
        started: false, target: null, frame: 0, ghost: null, scroller: null };
      window.addEventListener("pointermove", onMove, true);
      window.addEventListener("pointerup", onUp, true);
      window.addEventListener("pointercancel", onCancel, true);
      window.addEventListener("keydown", onKey, true);
      window.addEventListener("blur", onCancel);
      document.addEventListener("selectstart", noSelect, true);
    };

    const native = (event) => { const item = event.target.closest && event.target.closest(opts.item); if (item && root.contains(item)) event.preventDefault(); };
    root.addEventListener("pointerdown", down);
    root.addEventListener("dragstart", native);
    return () => { root.removeEventListener("pointerdown", down); root.removeEventListener("dragstart", native); };
  }

  window.MemuDragSort = { bind, get active() { return !!(drag && drag.started); } };

  function sidebar() {
    const side = document.querySelector(".side .side-scroll") || document.querySelector(".side");
    if (!side) return;
    const call = (name, ...args) => (typeof window[name] === "function" ? window[name](...args) : undefined);

    bind(side, {
      item: "#chats-pinned [data-chat], #chat-folders [data-chat], #chats-list [data-chat]",
      ignore: "[data-chat-menu], button",
      scroller: () => side,
      label: (item) => item.querySelector(".ci-title")?.textContent || item.textContent,
      target: (el, x, y, item) => {
        const folder = el.closest("[data-folder-drop]");
        if (folder && side.contains(folder)) {
          return folder.contains(item) ? null : { el: folder, cls: "drop-target", folder: folder.dataset.folderDrop };
        }
        const group = el.closest("#group-chats");
        const list = document.getElementById("chats-list");
        if (group && list && !list.contains(item)) return { el: list, cls: "drop-target", folder: "" };
        return null;
      },
      drop: (item, target) => call("moveChatFolderNow", item.dataset.chat, target.folder),
    });

    bind(side, {
      item: "[data-section]",
      handle: "[data-section-handle]",
      ignore: "button, [data-section-move]",
      scroller: () => side,
      label: (item) => item.querySelector("[data-section-handle] .side-label, [data-section-handle]")?.textContent || "",
      target: (el, x, y, item) => {
        const group = el.closest("[data-section]");
        if (!group || group === item || !side.contains(group)) return null;
        const rect = group.getBoundingClientRect();
        const before = (y - rect.top) < Math.min(rect.height / 2, 90);
        return { el: group, cls: before ? "section-drop" : "section-drop-after", before, key: group.dataset.section };
      },
      start: (item) => item.classList.add("section-dragging"),
      end: (item) => item.classList.remove("section-dragging"),
      drop: (item, target) => {
        const from = item.dataset.section;
        if (typeof window.sectionOrder !== "function" || !from || from === target.key) return;
        const order = window.sectionOrder().filter((key) => key !== from);
        const at = order.indexOf(target.key);
        if (at < 0) return;
        order.splice(target.before ? at : at + 1, 0, from);
        call("saveSectionOrder", order);
        call("applySectionOrder");
      },
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", sidebar, { once: true });
  else sidebar();
})();
