

(function () {
  "use strict";

  const UNDER = [
    "#panel-back.visible:not(.over-modules)",
    "#image-viewer.visible",
    ".msx-root:not([hidden])",
    "#code-palette:not([hidden])",
    ".prd-sheet.open", ".prd-sheet.visible",
  ];

  const SAFE = "#module-layer, #module-headbar, .module-headbar, .topbar, [data-module-open], #panel-back, .toast-container, .memu-titlebar, .memu-wc, [data-memu-layer], .context-menu, .pretty-select-menu, .msx-root, #image-viewer";
  const AUTO_WINDOW = 900;

  const st = { focus: false, parked: null, raf: 0, lastUnder: false };
  const MM = () => window.MemuModules;
  const t = (ru, en) => (document.documentElement.lang === "en" ? en : ru);

  function layer() { return document.getElementById("module-layer"); }
  function fullWindows() {
    const root = layer();
    if (!root || root.hidden) return [];
    return [...root.querySelectorAll('.module-window[data-full="1"]')].filter((el) => !el.hidden);
  }
  function underOpen() {
    try { return !!document.querySelector(UNDER.join(",")); } catch (error) { return false; }
  }
  function reducedMotion() {
    const root = document.documentElement;
    try { if (matchMedia("(prefers-reduced-motion: reduce)").matches) return true; } catch (error) {  }
    return root.dataset.motion === "off" || (document.body && document.body.classList.contains("reduce-motion"));
  }
  function pillOf(id) {
    const root = layer();
    return root ? root.querySelector(`[data-task="${CSS.escape(id)}"]`) : null;
  }

  function sync() {
    st.raf = 0;
    const on = fullWindows().length > 0;
    if (on !== st.focus) {
      st.focus = on;
      const root = document.documentElement;
      if (on) root.dataset.moduleFocus = "1"; else delete root.dataset.moduleFocus;
      const main = document.getElementById("main-content");
      if (main) main.inert = on;
    }
    const under = underOpen();
    if (under && !st.lastUnder && st.parked && st.parked.pending && Date.now() - st.parked.at < AUTO_WINDOW) {

      st.parked.auto = true;
      st.parked.pending = false;
    }
    if (under && !st.lastUnder && st.focus) park("overlay");
    else if (!under && st.lastUnder && st.parked && st.parked.auto) unpark();
    st.lastUnder = under;
    markPills();
  }
  function schedule() { if (!st.raf) st.raf = requestAnimationFrame(sync); }

  function animateTo(el, pill, reverse) {
    if (!el || !el.animate || reducedMotion()) return Promise.resolve();
    const a = el.getBoundingClientRect();
    const b = pill && pill.getBoundingClientRect ? pill.getBoundingClientRect() : { left: a.left + a.width / 2, top: innerHeight - 30, width: 120, height: 32 };
    const dx = (b.left + b.width / 2) - (a.left + a.width / 2);
    const dy = (b.top + b.height / 2) - (a.top + a.height / 2);
    const s = Math.max(0.04, Math.min(b.width / a.width, 0.25));
    const far = `translate(${dx}px, ${dy}px) scale(${s})`;
    const frames = [{ transform: "none", opacity: 1 }, { transform: far, opacity: 0 }];
    const anim = el.animate(reverse ? frames.reverse() : frames,
      { duration: reverse ? 240 : 220, easing: "cubic-bezier(.2, .8, .2, 1)" });
    return anim.finished.catch(() => {});
  }

  function park(reason) {
    const els = fullWindows();
    if (!els.length || !MM()) return false;
    const ids = els.map((el) => el.dataset.module);
    const auto = reason === "overlay";
    st.parked = { ids, auto, at: Date.now(), reason };
    for (const el of els) {

      const box = el.getBoundingClientRect();
      MM().minimize(el.dataset.module);
      flyGhost(box, pillOf(el.dataset.module));
    }
    markPills();
    return true;
  }
  function flyGhost(box, pill) {
    const root = layer();
    if (reducedMotion() || !root || !box.width) return;
    const base = root.getBoundingClientRect();
    const ghost = document.createElement("div");
    ghost.className = "module-focus-ghost";
    ghost.setAttribute("aria-hidden", "true");
    Object.assign(ghost.style, { left: (box.left - base.left) + "px", top: (box.top - base.top) + "px",
      width: box.width + "px", height: box.height + "px" });
    root.appendChild(ghost);
    animateTo(ghost, pill, false).then(() => ghost.remove());
  }
  function unpark() {
    const parked = st.parked;
    st.parked = null;
    if (!parked || !MM()) return;
    for (const id of parked.ids) {
      const state = MM().windowState(id);
      if (!state || !state.minimized) continue;
      const pill = pillOf(id);
      MM().restore(id);
      const el = document.querySelector(`.module-window[data-module="${CSS.escape(id)}"]`);
      animateTo(el, pill, true);
    }
    markPills();
  }
  function markPills() {
    const root = layer();
    if (!root) return;
    const ids = st.parked ? st.parked.ids : [];
    root.querySelectorAll("[data-task]").forEach((pill) => {
      const on = ids.includes(pill.dataset.task) && !!(MM() && MM().windowState(pill.dataset.task) || {}).minimized;
      if (pill.classList.contains("is-focus-parked") === on) return;
      pill.classList.toggle("is-focus-parked", on);
      if (on) pill.title = t("Вернуть во весь экран — модуль уступил место окну MemuAI", "Back to full screen — the module stepped aside for a MemuAI window");
    });
    if (st.parked && !ids.some((id) => (MM() && MM().windowState(id) || {}).minimized)) st.parked = null;
  }

  function onPointer(event) {
    if (!st.focus || event.button > 0) return;
    const target = event.target;
    if (!(target instanceof Element) || target.closest(SAFE)) return;
    if (!target.closest("#app")) return;
    if (park("chrome")) st.parked.pending = true;
  }

  function replayKey(moduleId, combo) {
    if (!st.focus || !combo || !(combo.ctrlKey || combo.altKey || combo.metaKey)) return false;
    const state = MM() && MM().windowState(moduleId);
    if (!state || state.state !== "expanded") return false;
    const init = { key: String(combo.key || ""), code: String(combo.code || ""), ctrlKey: !!combo.ctrlKey, altKey: !!combo.altKey,
      shiftKey: !!combo.shiftKey, metaKey: !!combo.metaKey, bubbles: true, cancelable: true };
    try { document.body.dispatchEvent(new KeyboardEvent("keydown", init)); } catch (error) { return false; }
    return true;
  }

  function boot() {
    const root = layer();
    if (!root || document.documentElement.hasAttribute("data-module-app")) return;
    const watch = new MutationObserver(schedule);
    watch.observe(root, { subtree: true, attributes: true, attributeFilter: ["data-full", "hidden"], childList: true });

    const bodyWatch = new MutationObserver((records) => {
      for (const record of records) for (const node of record.addedNodes) {
        if (node.nodeType === 1 && node.matches && node.matches(".msx-root, .prd-sheet")) watch.observe(node, { attributes: true, attributeFilter: ["class", "hidden"] });
      }
      schedule();
    });
    bodyWatch.observe(document.body, { childList: true });

    for (const sel of ["#panel-back", "#image-viewer", "#code-palette", ".msx-root", ".prd-sheet"]) {
      document.querySelectorAll(sel).forEach((node) => watch.observe(node, { attributes: true, attributeFilter: ["class", "hidden"] }));
    }
    document.addEventListener("pointerdown", onPointer, true);
    schedule();
  }

  window.MemuModuleFocus = {
    get active() { return st.focus; },
    get parked() { return st.parked ? st.parked.ids.slice() : []; },
    park: () => park("api"),
    unpark,
    replayKey,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
