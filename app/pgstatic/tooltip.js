

"use strict";
const MemuTooltip = (() => {
  const SHOW_DELAY = 450;
  const WARM_DELAY = 60;
  const WARM_WINDOW = 600;
  const GAP = 8;

  const CONTROL = "button, a[href], label, summary, .sx-row, [role='button'], [role='option'], [role='menuitem'], [role='menuitemradio'], [role='tab'], [role='link']";
  const SCAN_LIMIT = 60;
  let tip = null;
  let target = null;
  let timer = 0;
  let watch = 0;
  let lastHidden = 0;
  let clipTimer = 0;

  const CLIP_WAIT = 150;
  let observer = null;

  const escape = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const SHORTCUT = /^(?:(?:Ctrl|Alt|Shift|Win|Cmd|Enter|Esc|Escape|Tab|Space|Del|Delete|Home|End|PgUp|PgDn|F\d{1,2}|↑|↓|←|→)(?:\s*\+\s*|$))+|^(?:Ctrl|Alt|Shift)\s*\+\s*\S+$|^\S$|^Alt\+\d…\d$/i;

  // The hint lives in the browser's top layer when it can (popover), so menus,
  // dialogs and overlays with a huge z-index never cover it; the CSS z-index is
  // the fallback for engines without popovers.
  const TOP_LAYER = typeof HTMLElement !== "undefined" && typeof HTMLElement.prototype.showPopover === "function";
  const MENU = "[role='menu'], [role='listbox'], .context-menu, .dd-pop, .app-menu-pop, .memu-help-menu, .profile-menu-pop, .pretty-select-menu, .mpick-pop";

  function element() {
    if (tip && tip.isConnected) return tip;
    tip = document.createElement("div");
    tip.className = "memu-tooltip";
    tip.id = "memu-tooltip";
    tip.setAttribute("role", "tooltip");
    if (TOP_LAYER) tip.setAttribute("popover", "manual");
    tip.hidden = true;
    document.body.appendChild(tip);
    return tip;
  }
  function raise() {
    if (!TOP_LAYER || !tip) return;
    try {
      if (tip.matches(":popover-open")) tip.hidePopover();
      tip.showPopover();
    } catch (_) { /* the z-index keeps it above the page */ }
  }
  function lower() {
    if (!TOP_LAYER || !tip) return;
    try { if (tip.matches(":popover-open")) tip.hidePopover(); } catch (_) { /* already hidden */ }
  }

  function clipped(el) {
    const style = getComputedStyle(el);
    const clamp = style.webkitLineClamp && style.webkitLineClamp !== "none";
    if (style.textOverflow !== "ellipsis" && !clamp) return false;
    return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
  }

  function wordsOf(el) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: (text) => (text.parentElement.closest("[aria-hidden='true']") && !el.closest("[aria-hidden='true']")
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    let out = "", prevBox = null;

    const boxOf = (node) => {
      for (let n = node.parentElement; n && n !== el; n = n.parentElement) {
        const display = getComputedStyle(n).display;
        if (display !== "inline" && display !== "contents") return n;
      }
      return el;
    };
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      const box = boxOf(text);
      if (prevBox && box !== prevBox && out && !/\s$/.test(out) && !/^\s/.test(text.data)) out += " ";
      out += text.data; prevBox = box;
    }
    return out.replace(/\s+/g, " ").trim();
  }

  function overflowText(host) {
    const found = [];
    const lines = [];
    [host, ...Array.from(host.querySelectorAll("*")).slice(0, SCAN_LIMIT)].forEach((el) => {
      if (found.some((outer) => outer.contains(el)) || !clipped(el)) return;
      found.push(el);
      const words = wordsOf(el);
      if (words && !lines.includes(words)) lines.push(words);
    });
    return lines.join("\n");
  }

  function textOf(node) {
    const title = node.getAttribute("title");
    if (title !== null) {
      if (title.trim()) {
        node.dataset.tip = title;
        const label = node.getAttribute("aria-label") || node.getAttribute("aria-labelledby");
        if (!label && !node.textContent.trim() && /^(BUTTON|A|SUMMARY)$/.test(node.tagName)) {
          node.setAttribute("aria-label", title.split(" — ")[0]);
        }
      }
      node.removeAttribute("title");
    }
    if (node.dataset.tip === undefined) return overflowText(node);
    return node.dataset.tip.trim();
  }

  function render(text) {
    if (text.includes("\n")) return `<span class="memu-tooltip-text">${escape(text)}</span>`;
    const parts = text.split(/\s+[—–]\s+/);
    const last = parts.length > 1 ? parts[parts.length - 1].trim() : "";
    const keys = last && last.length <= 24 && SHORTCUT.test(last) ? last : "";
    const label = keys ? parts.slice(0, -1).join(" — ") : text;
    const kbd = keys ? `<span class="memu-tooltip-keys">${keys.split(/\s*\+\s*/).map((key) => `<kbd>${escape(key)}</kbd>`).join("")}</span>` : "";
    return `<span class="memu-tooltip-text">${escape(label)}</span>${kbd}`;
  }

  function place(node) {
    const box = node.getBoundingClientRect();
    // Measure at the corner: near a screen edge the hint would shrink and wrap.
    tip.style.left = "0px";
    tip.style.top = "0px";
    const own = { width: tip.offsetWidth, height: tip.offsetHeight };
    // An item of an open menu gets its hint beside the menu, not over the next items.
    const menu = node.closest(MENU);
    if (menu && menu !== node) {
      const frame = menu.getBoundingClientRect();
      let left = frame.right + GAP;
      let side = "right";
      if (left + own.width > window.innerWidth - 6) { left = frame.left - own.width - GAP; side = "left"; }
      let top = Math.max(6, Math.min(box.top + box.height / 2 - own.height / 2, window.innerHeight - own.height - 6));
      if (left < 6) {
        // No room at the sides: under or over the whole menu.
        left = Math.max(6, Math.min(box.left + box.width / 2 - own.width / 2, window.innerWidth - own.width - 6));
        if (frame.bottom + GAP + own.height <= window.innerHeight - 6) { top = frame.bottom + GAP; side = "below"; }
        else if (frame.top - GAP - own.height >= 6) { top = frame.top - GAP - own.height; side = "above"; }
        else left = -1;
      }
      if (left >= 0) {
        tip.style.left = `${Math.round(left)}px`;
        tip.style.top = `${Math.round(top)}px`;
        tip.dataset.side = side;
        return;
      }
    }
    let top = box.bottom + GAP;
    let side = "below";
    if (top + own.height > window.innerHeight - 6) { top = box.top - own.height - GAP; side = "above"; }
    let left = box.left + box.width / 2 - own.width / 2;
    left = Math.max(6, Math.min(left, window.innerWidth - own.width - 6));
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(Math.max(6, top))}px`;
    tip.dataset.side = side;
  }

  function menuOpen(node) {
    if (node.closest('[aria-expanded="true"]')) return true;
    const dd = node.closest(".dropdown");
    const pop = dd && dd.querySelector(".dd-pop");
    return !!(pop && pop.style.display === "block");
  }

  function show(node) {
    const text = textOf(node);
    if (!text || !node.isConnected || menuOpen(node)) return;
    element();
    tip.innerHTML = render(text);
    tip.hidden = false;
    tip.classList.remove("visible");
    raise();
    place(node);
    requestAnimationFrame(() => tip && tip.classList.add("visible"));
    // B-ONB-M7: remember the previous value so hide() can restore it
    // instead of deleting an aria-describedby the page itself set.
    if (node.getAttribute("aria-describedby") !== tip.id) {
      node.dataset.memuPrevDescribedby = node.getAttribute("aria-describedby") || "";
    }
    node.setAttribute("aria-describedby", tip.id);
    clearInterval(watch);

    watch = setInterval(() => { if (!target || !target.isConnected) hide(); }, 250);
  }

  function follow(node) {
    observer = observer || new MutationObserver(() => {
      if (!target) return;
      const text = textOf(target);
      if (!tip || tip.hidden) return;
      if (!text) { hide(); return; }
      const html = render(text);
      if (tip.innerHTML !== html) { tip.innerHTML = html; place(target); }
    });
    observer.disconnect();
    observer.observe(node, { attributes: true, attributeFilter: ["title", "data-tip"] });
  }

  function hide() {
    clearTimeout(timer);
    clearTimeout(clipTimer);
    clearInterval(watch);
    if (observer) observer.disconnect();
    if (target && target.getAttribute("aria-describedby") === "memu-tooltip") {
      const prev = target.dataset.memuPrevDescribedby;
      if (prev) target.setAttribute("aria-describedby", prev);
      else target.removeAttribute("aria-describedby");
      delete target.dataset.memuPrevDescribedby;
    }
    if (tip && !tip.hidden) { lower(); tip.hidden = true; tip.classList.remove("visible"); lastHidden = Date.now(); }
    target = null;
  }

  function schedule(node, delay) {
    if (node === target) return;
    hide();
    target = node;
    if (node.hasAttribute("title")) textOf(node);
    follow(node);
    const warm = Date.now() - lastHidden < WARM_WINDOW;
    timer = setTimeout(() => { if (target === node) show(node); }, warm ? WARM_DELAY : delay);
  }

  function clippedHost(start) {
    const control = start.closest(CONTROL);
    if (control) return control;
    for (let el = start, depth = 0; el && el !== document.body && depth < 3; el = el.parentElement, depth += 1) {
      if (clipped(el)) return el;
    }
    return null;
  }

  function candidate(start, quick = false) {
    if (!start || !start.closest || start.closest(".memu-tooltip")) return null;
    const titled = start.closest("[title], [data-tip]");
    if (titled && titled.dataset.tip === "" && !titled.hasAttribute("title")) return null;
    const typing = start.isContentEditable || /^(TEXTAREA|SELECT)$/.test(start.tagName)
      || start.tagName === "INPUT" && !/^(radio|checkbox)$/.test(start.type);
    const node = titled || (typing ? null : quick ? start.closest(CONTROL) : clippedHost(start));
    if (!node || node.tagName === "IFRAME" || node === document.documentElement || node === document.body) return null;
    return node;
  }

  function bind() {
    document.addEventListener("pointerover", (event) => {
      if (event.pointerType === "touch") return;
      const start = event.target;
      const node = candidate(start, true);
      if (node) { schedule(node, SHOW_DELAY); return; }
      if (target && !target.contains(start)) hide();
      clearTimeout(clipTimer);
      if (!start || !start.closest) return;
      clipTimer = setTimeout(() => {
        if (!start.isConnected || !start.matches(":hover")) return;
        const late = candidate(start);
        if (late) schedule(late, Math.max(0, SHOW_DELAY - CLIP_WAIT));
      }, CLIP_WAIT);
    }, true);
    document.addEventListener("pointerout", (event) => {
      if (target && (!event.relatedTarget || !target.contains(event.relatedTarget))) hide();
    }, true);
    document.addEventListener("focusin", (event) => {
      const node = candidate(event.target);
      if (!node) return;
      let visible = false;
      try { visible = event.target.matches(":focus-visible"); } catch (_) { visible = false; }
      if (visible) schedule(node, SHOW_DELAY + 150);
    }, true);
    document.addEventListener("focusout", hide, true);
    ["pointerdown", "wheel", "dragstart"].forEach((name) => document.addEventListener(name, hide, { capture: true, passive: true }));

    document.addEventListener("scroll", () => {
      if (!tip || tip.hidden || !target) return;
      const box = target.getBoundingClientRect();
      if (!target.isConnected || box.bottom < 0 || box.top > window.innerHeight) hide();
      else place(target);
    }, { capture: true, passive: true });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" || !event.ctrlKey) hide(); }, true);
    window.addEventListener("blur", hide);
    window.addEventListener("resize", hide);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, { once: true });
  else bind();

  return Object.freeze({ hide, show: (node) => { target = node; follow(node); show(node); } });
})();
window.MemuTooltip = MemuTooltip;
