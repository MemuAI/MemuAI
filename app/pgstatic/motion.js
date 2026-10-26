

"use strict";
const MemuMotion = (() => {
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const SPRING = "linear(0, 0.006, 0.025 2.8%, 0.101 6.1%, 0.539 18.9%, 0.721 25.3%, 0.849 31.5%, 0.937 38.1%, 0.968 41.8%, 0.991 45.7%, 1.006 50.1%, 1.015 55%, 1.017 63.9%, 1.001 85.9%, 1)";
  const EASE_OUT = "cubic-bezier(.16, 1, .3, 1)";
  let springOk = false;
  try { springOk = CSS.supports("animation-timing-function", "linear(0, 1)"); } catch (_) {}
  const spring = () => (springOk ? SPRING : EASE_OUT);

  function level() {
    if (media && media.matches) return "off";
    if (document.body && document.body.classList.contains("reduce-motion")) return "off";
    const value = root.dataset.motion;
    return value === "off" || value === "reduced" ? value : "full";
  }
  const full = () => level() === "full";
  const ready = () => document.body && !document.body.classList.contains("booting")
    && performance.now() > 600 && document.visibilityState === "visible";

  let lastPointer = { x: -1, y: -1, t: 0, target: null };
  let swapping = null;

  function swap(update, kind = "") {
    const run = () => { try { update(); } catch (error) { console.error(error); } };
    const motion = level();
    if (motion === "off" || typeof document.startViewTransition !== "function" || !ready()) { run(); return; }

    if (swapping) { try { swapping.skipTransition(); } catch (_) {} }
    let reveal = false;
    if (kind === "theme" && motion === "full") {
      const fresh = performance.now() - lastPointer.t < 1200 && lastPointer.x >= 0;
      const button = document.getElementById("btn-theme");
      let x = innerWidth - 40, y = 24;
      if (fresh) { x = lastPointer.x; y = lastPointer.y; }
      else if (button && button.offsetParent) {
        const r = button.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top + r.height / 2;
      }
      const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      root.style.setProperty("--m-vt-x", Math.round(x) + "px");
      root.style.setProperty("--m-vt-y", Math.round(y) + "px");
      root.style.setProperty("--m-vt-r", Math.ceil(radius) + "px");
      reveal = true;
    }
    root.classList.add("m-vt", reveal ? "m-vt-reveal" : "m-vt-fade");
    let transition;
    try { transition = document.startViewTransition(run); }
    catch (_) { root.classList.remove("m-vt", "m-vt-reveal", "m-vt-fade"); run(); return; }
    swapping = transition;
    const done = () => {
      if (swapping === transition) swapping = null;
      root.classList.remove("m-vt", "m-vt-reveal", "m-vt-fade");
    };
    transition.finished.then(done, done);
    transition.ready.catch(() => {});
    transition.updateCallbackDone.catch(() => {});
  }

  const PRESS = [
    "button:not(:disabled)", ".pbtn", ".chip-sug", ".side-btn", ".cbtn", ".send", ".iconbtn", ".bar-icon",
    ".dd-btn", ".code-chip", ".work-action", ".work-mode-pill", ".tog", ".tool-chip", ".plus-row",
    ".profile-card", ".settings-tab-btn", "[role=button]", ".theme-card", ".design-card", ".msg-tool", ".ci-more",
    ".panel-close", ".preview-btn", ".live-ctl", ".live-talk", ".jump-latest", ".btn-new",
  ].join(",");

  const NO_PRESS = ".chat-item, .msg, .panel, .composer, .module-window, .code-editor, .dd-pop, .context-menu, [data-no-press]";
  let pressed = null;

  function pressDown(event) {
    if (event.button !== 0 || !full()) return;
    const el = event.target instanceof Element ? event.target.closest(PRESS) : null;
    if (!el || el.matches(NO_PRESS) || el.closest("[data-no-press]")) return;
    if (el.matches(":disabled, [aria-disabled=true]")) return;
    const r = el.getBoundingClientRect();
    if (r.width > 520 || r.height > 160) return;
    const depth = r.width < 48 && r.height < 48 ? 0.9 : r.width < 140 ? 0.95 : 0.975;
    release();
    try {
      const animation = el.animate([{ scale: 1 }, { scale: depth }], { duration: 90, easing: "cubic-bezier(.3, 0, .6, 1)", fill: "forwards" });
      pressed = { el, animation, depth };
    } catch (_) {}
  }
  function release() {
    if (!pressed) return;
    const { el, animation, depth } = pressed;
    pressed = null;
    try {
      const back = el.animate([{ scale: depth }, { scale: 1 }], { duration: 300, easing: spring() });
      animation.cancel();
      back.onfinish = back.oncancel = () => back.cancel();
    } catch (_) { try { animation.cancel(); } catch (__) {} }
  }

  const POPOVERS = "#context-menu, #model-dd-pop, #app-menu-pop, .pretty-select-menu, .msg-more-pop, .profile-menu-pop, #slash-menu, .plus-menu, .module-layout-menu, .voice-menu, .memu-menu";
  const shown = new WeakSet();
  function isShown(el) {
    if (el.hidden) return false;
    const style = el.style.display;
    if (style === "none") return false;
    return el.offsetParent !== null || getComputedStyle(el).position === "fixed" && getComputedStyle(el).display !== "none";
  }
  function originFor(el) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    let x, y;
    const recent = performance.now() - lastPointer.t < 900 && lastPointer.x >= 0;
    if (recent) {
      x = lastPointer.x - r.left; y = lastPointer.y - r.top;
    } else {

      x = r.width / 2; y = r.top > innerHeight / 2 ? r.height : 0;
    }
    x = Math.max(0, Math.min(r.width, x)); y = Math.max(0, Math.min(r.height, y));

    if (y > 8 && y < r.height - 8 && (x < 2 || x > r.width - 2)) {  }
    el.style.setProperty("--m-ox", Math.round(x) + "px");
    el.style.setProperty("--m-oy", Math.round(y) + "px");
  }
  function checkPopover(el) {
    const visible = isShown(el);
    if (visible && !shown.has(el)) { shown.add(el); originFor(el); }
    else if (!visible && shown.has(el)) shown.delete(el);
  }

  const LIST_ROOTS = ["#chats-pinned", "#chat-folders", "#chats-list", "#memes-list"];
  const ROW = ".chat-item[data-chat], section.chat-folder[data-folder-drop], section.meme-block[data-meme]";
  const rowKey = (el) => (el.dataset.chat ? "c:" + el.dataset.chat : el.dataset.folderDrop ? "f:" + el.dataset.folderDrop
    : el.dataset.meme ? "m:" + el.dataset.meme : "");
  let rowCache = new Map();
  let listContext = "";
  let listQueued = false;

  const scroller = () => document.querySelector(".side-scroll");
  function snapshotRows() {
    const sc = scroller();
    if (!sc) return new Map();
    const base = sc.getBoundingClientRect();
    const map = new Map();
    map.present = new Set();
    for (const selector of LIST_ROOTS) {
      const host = document.querySelector(selector);
      if (!host) continue;
      host.querySelectorAll(ROW).forEach((el) => {
        const key = rowKey(el);
        if (!key || map.has(key)) return;
        map.present.add(key);
        const r = el.getBoundingClientRect();
        if (!r.height) return;
        map.set(key, { el, top: r.top - base.top + sc.scrollTop, left: r.left - base.left, width: r.width, height: r.height });
      });
    }
    return map;
  }
  function contextKey() {
    const search = document.getElementById("chat-search");
    const mode = document.body.className.match(/\b(work|code)-mode\b/);
    return (mode ? mode[1] : "chat") + "|" + (search ? search.value.trim() : "");
  }
  function choreograph() {
    listQueued = false;
    const sc = scroller();
    if (!sc) return;
    const next = snapshotRows();
    const context = contextKey();
    const previous = rowCache;
    rowCache = next;
    const sameContext = context === listContext;
    listContext = context;
    if (!previous.size || !sameContext || !full() || !ready()) return;
    const added = [], moved = [], removed = [];
    next.forEach((row, key) => {
      const old = previous.get(key);
      if (!old) added.push(row);
      else if (Math.abs(old.top - row.top) > 1 || Math.abs(old.left - row.left) > 1) moved.push([old, row]);
    });

    previous.forEach((row, key) => { if (!next.has(key) && !(next.present && next.present.has(key))) removed.push(row); });

    if (added.length + removed.length > 6 || moved.length > 40) return;
    const viewTop = sc.scrollTop - 80, viewBottom = sc.scrollTop + sc.clientHeight + 80;
    const inView = (row) => row.top + row.height > viewTop && row.top < viewBottom;
    const movedBy = new Map(moved.map(([old, row]) => [row.el, (old.top - row.top) + "," + (old.left - row.left)]));
    moved.forEach(([old, row]) => {
      if (!inView(row) && !inView(old)) return;
      const dy = old.top - row.top, dx = old.left - row.left;

      const parent = row.el.parentElement && row.el.parentElement.closest(ROW);
      if (parent && movedBy.get(parent) === dy + "," + dx) return;
      try {
        row.el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
          { duration: 360, easing: spring(), composite: "add" });
      } catch (_) {}
    });
    added.forEach((row, index) => {
      if (!inView(row)) return;
      try {
        row.el.animate([
          { opacity: 0, transform: "translateX(-10px) scale(.98)" },
          { opacity: 1, transform: "none" },
        ], { duration: 320, delay: Math.min(index, 4) * 30, easing: spring(), fill: "backwards" });
      } catch (_) {}
    });
    removed.forEach((row) => {
      if (!inView(row) || !row.el) return;
      const base = sc.getBoundingClientRect();
      const ghost = row.el.cloneNode(true);
      ghost.removeAttribute("id");
      ghost.setAttribute("aria-hidden", "true");
      ghost.classList.add("m-ghost");
      Object.assign(ghost.style, {
        position: "fixed", margin: "0", pointerEvents: "none", zIndex: "40",
        top: Math.round(base.top + row.top - sc.scrollTop) + "px", left: Math.round(base.left + row.left) + "px",
        width: Math.round(row.width) + "px", height: Math.round(row.height) + "px", boxSizing: "border-box",
      });

      document.body.appendChild(ghost);
      try {
        const anim = ghost.animate([
          { opacity: 1, transform: "none" },
          { opacity: 0, transform: "translateX(-14px) scale(.96)" },
        ], { duration: 220, easing: "cubic-bezier(.4, 0, 1, 1)", fill: "forwards" });
        anim.onfinish = anim.oncancel = () => ghost.remove();
      } catch (_) { ghost.remove(); }
      setTimeout(() => ghost.remove(), 600);
    });
  }
  function queueChoreography() {
    if (listQueued) return;
    listQueued = true;

    queueMicrotask(choreograph);
  }

  let imageSource = null;
  function rememberImage(event) {
    const img = event.target instanceof Element ? event.target.closest("img") : null;
    if (!img || img.closest("#image-viewer")) return;
    imageSource = { rect: img.getBoundingClientRect(), time: performance.now() };
  }
  function zoomViewer(viewer) {
    const img = viewer.querySelector("#image-viewer-img");
    if (!img || level() === "off") return;
    const source = imageSource && performance.now() - imageSource.time < 1500 ? imageSource.rect : null;
    imageSource = null;
    const play = () => requestAnimationFrame(() => {
      const target = img.getBoundingClientRect();
      if (!target.width || !target.height) return;
      try {
        if (source && full()) {
          const sx = source.width / target.width, sy = source.height / target.height;
          const dx = source.left + source.width / 2 - (target.left + target.width / 2);
          const dy = source.top + source.height / 2 - (target.top + target.height / 2);
          img.animate([
            { transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`, opacity: .6, borderRadius: "14px" },
            { transform: "none", opacity: 1, borderRadius: "4px" },
          ], { duration: 420, easing: spring() });
        } else {
          img.animate([{ transform: "scale(.94)", opacity: 0 }, { transform: "none", opacity: 1 }],
            { duration: full() ? 260 : 140, easing: EASE_OUT });
        }
      } catch (_) {}
    });
    if (img.complete && img.naturalWidth && img.style.width) play();
    else img.addEventListener("load", () => setTimeout(play, 0), { once: true });
  }

  const SHEEN = [".chip-sug", ".btn-new", ".send", ".pbtn", ".composer", ".chat-item", ".side-btn",
    ".settings-tab-btn", ".module-card", ".rs-card", ".dd-item", ".work-action", ".code-chip", ".profile-card",
    ".theme-card", ".design-card", ".msg-user .bubble"].join(",");
  let sheenTarget = null, sheenEvent = null, sheenQueued = false;
  function sheenFrame() {
    sheenQueued = false;
    const event = sheenEvent;
    if (!event) return;
    const el = event.target instanceof Element ? event.target.closest(SHEEN) : null;
    if (sheenTarget && sheenTarget !== el) sheenTarget.classList.remove("m-lit");
    sheenTarget = el;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", Math.round(event.clientX - r.left) + "px");
    el.style.setProperty("--my", Math.round(event.clientY - r.top) + "px");
    el.classList.add("m-lit");
  }
  function sheenMove(event) {
    if (event.pointerType === "touch") return;

    if (root.dataset.design !== "glass" || !full()) {
      if (sheenTarget) { sheenTarget.classList.remove("m-lit"); sheenTarget = null; }
      return;
    }
    sheenEvent = event;
    if (!sheenQueued) { sheenQueued = true; requestAnimationFrame(sheenFrame); }
  }

  function syncPaused() {
    const paused = document.visibilityState !== "visible" || !document.hasFocus();
    root.classList.toggle("m-paused", paused);
  }

  function init() {
    if (!root.dataset.motion) {
      try {
        const cached = localStorage.getItem("memu_motion");
        if (cached === "reduced" || cached === "off") root.dataset.motion = cached;
      } catch (_) {}
    }
    root.classList.toggle("m-vt-supported", typeof document.startViewTransition === "function");
    const pointer = (event) => {
      lastPointer = { x: event.clientX, y: event.clientY, t: performance.now(), target: event.target };
    };
    document.addEventListener("pointerdown", (event) => { pointer(event); pressDown(event); rememberImage(event); }, { capture: true, passive: true });
    document.addEventListener("contextmenu", pointer, { capture: true, passive: true });
    document.addEventListener("pointerup", release, { capture: true, passive: true });
    document.addEventListener("pointercancel", release, { capture: true, passive: true });
    document.addEventListener("dragstart", release, { capture: true, passive: true });
    window.addEventListener("blur", () => { release(); syncPaused(); });
    window.addEventListener("focus", syncPaused);
    document.addEventListener("visibilitychange", syncPaused);
    document.addEventListener("pointermove", sheenMove, { passive: true });
    document.addEventListener("pointerleave", () => { if (sheenTarget) sheenTarget.classList.remove("m-lit"); sheenTarget = null; });
    syncPaused();

    const observer = new MutationObserver((records) => {
      let lists = false;
      for (const record of records) {
        const target = record.target;
        if (!(target instanceof Element)) continue;
        if (record.type === "attributes") {
          if (target.matches(POPOVERS)) checkPopover(target);
          else if (target.id === "image-viewer" && record.attributeName === "class" && target.classList.contains("visible")
            && !(record.oldValue || "").includes("visible")) zoomViewer(target);
        } else if (record.type === "childList") {
          if (!lists && target.closest && target.closest(LIST_ROOTS.join(","))) lists = true;
          record.addedNodes.forEach((node) => {
            if (node instanceof Element && node.matches(POPOVERS)) checkPopover(node);
          });
        }
      }
      if (lists) queueChoreography();
    });
    observer.observe(document.body, {
      subtree: true, childList: true, attributes: true, attributeOldValue: true,
      attributeFilter: ["class", "style", "hidden"],
    });

    requestAnimationFrame(() => { rowCache = snapshotRows(); listContext = contextKey(); });

    let refresh = 0;
    const later = () => { clearTimeout(refresh); refresh = setTimeout(() => { if (!listQueued) rowCache = snapshotRows(); }, 120); };
    document.addEventListener("scroll", (event) => { if (event.target === scroller()) later(); }, { capture: true, passive: true });
    window.addEventListener("resize", later, { passive: true });
    document.addEventListener("transitionend", (event) => {
      if (event.target instanceof Element && event.target.matches(".side, .chat-folder-items, .chat-folder")) later();
    }, { passive: true });
  }
  if (document.body) init(); else document.addEventListener("DOMContentLoaded", init, { once: true });

  return Object.freeze({ swap, level, spring: () => spring() });
})();
window.MemuMotion = MemuMotion;
