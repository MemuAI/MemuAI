/* MemuAI 3.1 · Round 30 · zoom and pan for schemes (Mermaid) — used by diagrams.js.
 *
 * A wide scheme used to be squeezed into the width of the message, so its labels shrank to 6–8 px and could only
 * be read in fullscreen. Now the stage is a viewport:
 *   • by default the scheme is drawn at a readable size — never smaller than MIN_TEXT px of text; what does not fit
 *     is panned inside the card (drag with the mouse, scroll, swipe), the card does not grow wider than the message;
 *   • Ctrl+wheel (and a touchpad pinch, which arrives as Ctrl+wheel) zooms around the pointer, a touch pinch too;
 *   • double click zooms in at that point (at the maximum it goes back to the default);
 *   • buttons −, +, «Fit» (the whole scheme), «100%»; keys + − 0 when the scheme has the focus.
 * The size is applied to the SVG itself (width/height), not as a CSS transform: text stays sharp and the native
 * scrollbars are right. The zoom survives a redraw (theme change) through `host.__mdz`.
 */
(function () {
  "use strict";
  const MIN_TEXT = 11;          // px: below this the labels are not read, they are guessed
  const MAX_SCALE = 4;
  const maxHeight = () => Math.round(Math.min(560, Math.max(260, window.innerHeight * 0.62)));   // the card in a message
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en") || document.documentElement.lang === "en";
  const t = (ru, english) => (en() ? english : ru);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const ICON = {
    minus: '<path d="M5 12h14"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  };
  const svgIcon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

  /** Typical label size of the drawn scheme, in px at scale 1 (median of its texts). */
  function textSize(svg) {
    const sizes = [];
    for (const node of [...svg.querySelectorAll("text, tspan")].slice(0, 40)) {
      const size = parseFloat(getComputedStyle(node).fontSize);
      if (size > 0) sizes.push(size);
    }
    if (!sizes.length) return 14;
    sizes.sort((a, b) => a - b);
    return sizes[sizes.length >> 1];
  }

  /**
   * Turn `stage` (holding one <svg> of natural size width×height) into a zoomable viewport.
   * opts: { width, height, host (keeps the zoom between redraws), full (fills its parent: the fullscreen dialog) }
   */
  function attach(stage, opts) {
    const svg = stage.querySelector("svg");
    if (!svg || !(opts.width > 0 && opts.height > 0)) return null;
    const W = opts.width, H = opts.height;
    const host = opts.host || stage;
    stage.classList.add("mdz");
    stage.innerHTML = "";
    const scroll = document.createElement("div");
    scroll.className = "mdz-scroll";
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "img");
    scroll.setAttribute("aria-label", opts.label || t("Схема", "Scheme"));
    const canvas = document.createElement("div");
    canvas.className = "mdz-canvas";
    canvas.appendChild(svg);
    scroll.appendChild(canvas);
    const bar = document.createElement("div");
    bar.className = "mdz-ctl";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", t("Масштаб схемы", "Scheme zoom"));
    bar.innerHTML = `<button type="button" data-z="out" title="${t("Уменьшить (Ctrl+колесо, −)", "Zoom out (Ctrl+wheel, −)")}" aria-label="${t("Уменьшить", "Zoom out")}">${svgIcon("minus")}</button>`
      + `<button type="button" data-z="reset" class="mdz-pct" title="${t("Читаемый размер (0)", "Readable size (0)")}"></button>`
      + `<button type="button" data-z="in" title="${t("Увеличить (Ctrl+колесо, +, двойной щелчок)", "Zoom in (Ctrl+wheel, +, double click)")}" aria-label="${t("Увеличить", "Zoom in")}">${svgIcon("plus")}</button>`
      + `<i aria-hidden="true"></i>`
      + `<button type="button" data-z="fit" title="${t("Вся схема целиком", "The whole scheme")}" aria-label="${t("По размеру", "Fit")}">${svgIcon("fit")}</button>`
      + `<button type="button" data-z="one" class="mdz-one" title="${t("Натуральный размер", "Natural size")}">100%</button>`;
    stage.append(scroll, bar);
    svg.removeAttribute("style");
    svg.style.display = "block";
    svg.style.maxWidth = "none";

    // Round 36 (perf): the measuring (text size, width, scroll) forces a style + layout pass of the whole chat; with
    // a dozen schemes in a 300-message chat that was seconds on opening it. It now waits until the scheme is near
    // the screen (IntersectionObserver), each scheme once; until then the card keeps a provisional height.
    let label = 14;
    let scale = 1, user = false;

    const viewWidth = () => Math.max(80, stage.clientWidth || scroll.clientWidth || 600);
    function fitScale(both) {
      const w = viewWidth() - 16;
      const fitW = w / W;
      if (!both) return fitW;
      const h = (opts.full ? stage.clientHeight : maxHeight()) - 16;
      return Math.min(fitW, h / H);
    }
    const readable = () => MIN_TEXT / label;
    // Readable by default: as large as fits, but never below MIN_TEXT px of text, never above natural size.
    function defaultScale() {
      return clamp(Math.min(1, opts.full ? fitScale(true) : fitScale(false)), Math.min(1, readable()), 1);
    }
    const minScale = () => Math.min(0.5, fitScale(true), defaultScale());

    function sizeViewport() {
      if (opts.full) { scroll.style.height = ""; return; }
      // The card keeps the height of the default view: zooming does not make the message jump.
      scroll.style.height = Math.round(clamp(H * defaultScale() + 16, 96, maxHeight())) + "px";
    }
    function apply(next, anchor) {
      const before = anchor ? svg.getBoundingClientRect() : null;
      scale = clamp(next, minScale(), MAX_SCALE);
      svg.setAttribute("width", Math.round(W * scale));
      svg.setAttribute("height", Math.round(H * scale));
      if (anchor && before && before.width) {
        // Keep the point under the pointer where it was.
        const u = (anchor.x - before.left) / before.width, v = (anchor.y - before.top) / before.height;
        const after = svg.getBoundingClientRect();
        scroll.scrollLeft += after.left + u * after.width - anchor.x;
        scroll.scrollTop += after.top + v * after.height - anchor.y;
      }
      const pct = Math.round(scale * 100);
      bar.querySelector(".mdz-pct").textContent = pct + "%";
      bar.querySelector('[data-z="out"]').disabled = scale <= minScale() + 1e-3;
      bar.querySelector('[data-z="in"]').disabled = scale >= MAX_SCALE - 1e-3;
      const overflow = scroll.scrollWidth > scroll.clientWidth + 1 || scroll.scrollHeight > scroll.clientHeight + 1;
      stage.classList.toggle("mdz-pannable", overflow);
      // Without overflow a vertical swipe scrolls the chat; with it the fingers move the scheme.
      scroll.style.touchAction = overflow ? "none" : "pan-y";
      host.__mdz = { scale, user, x: centre().x, y: centre().y };
    }
    function centre() {
      return { x: (scroll.scrollLeft + scroll.clientWidth / 2) / Math.max(1, scroll.scrollWidth),
        y: (scroll.scrollTop + scroll.clientHeight / 2) / Math.max(1, scroll.scrollHeight) };
    }
    function centreOn(x, y) {
      scroll.scrollLeft = x * scroll.scrollWidth - scroll.clientWidth / 2;
      scroll.scrollTop = y * scroll.scrollHeight - scroll.clientHeight / 2;
    }
    function viewCentre() {
      const r = scroll.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    function zoomBy(factor, anchor) { user = true; apply(scale * factor, anchor || viewCentre()); }
    function reset() {
      user = false;
      apply(defaultScale());
      // Schemes grow from the top: start at the top, centred horizontally.
      scroll.scrollTop = 0;
      scroll.scrollLeft = (scroll.scrollWidth - scroll.clientWidth) / 2;
      host.__mdz = { scale, user, ...centre() };
    }
    function set(next) { user = true; apply(next, viewCentre()); }

    bar.addEventListener("click", (event) => {
      const button = event.target.closest("[data-z]");
      if (!button) return;
      event.stopPropagation();
      const what = button.dataset.z;
      if (what === "in") zoomBy(1.25);
      else if (what === "out") zoomBy(0.8);
      else if (what === "reset") reset();
      else if (what === "fit") { set(fitScale(true)); centreOn(0.5, 0.5); }
      else if (what === "one") set(1);
    });
    scroll.addEventListener("wheel", (event) => {
      if (!event.ctrlKey && !event.metaKey) return;          // a plain wheel scrolls (the scheme or the chat)
      event.preventDefault();
      const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
      zoomBy(Math.exp(-clamp(delta, -120, 120) * 0.0022), { x: event.clientX, y: event.clientY });
    }, { passive: false });
    scroll.addEventListener("dblclick", (event) => {
      event.preventDefault();
      if (scale >= MAX_SCALE - 1e-3) { reset(); return; }
      zoomBy(1.6, { x: event.clientX, y: event.clientY });
    });
    scroll.addEventListener("keydown", (event) => {
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      if (event.key === "+" || event.key === "=") { event.preventDefault(); zoomBy(1.25); }
      else if (event.key === "-" || event.key === "_") { event.preventDefault(); zoomBy(0.8); }
      else if (event.key === "0") { event.preventDefault(); reset(); }
    });

    // Mouse drag pans; one finger pans, two fingers pinch.
    const pointers = new Map();
    let drag = null, pinch = null;
    scroll.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, scale };
        drag = null;
        return;
      }
      if (!stage.classList.contains("mdz-pannable")) return;
      drag = { x: event.clientX, y: event.clientY, left: scroll.scrollLeft, top: scroll.scrollTop, moved: false, id: event.pointerId };
    });
    scroll.addEventListener("pointermove", (event) => {
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pinch && pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        user = true;
        apply(pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / pinch.dist, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
        return;
      }
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 3) return;
      if (!drag.moved) {
        drag.moved = true;
        stage.classList.add("mdz-dragging");
        try { scroll.setPointerCapture(event.pointerId); } catch (e) { /* the pointer is gone */ }
      }
      scroll.scrollLeft = drag.left - dx;
      scroll.scrollTop = drag.top - dy;
    });
    const release = (event) => {
      pointers.delete(event.pointerId);
      if (pointers.size < 2) pinch = null;
      if (drag && drag.id === event.pointerId) { drag = null; stage.classList.remove("mdz-dragging"); }
    };
    scroll.addEventListener("pointerup", release);
    scroll.addEventListener("pointercancel", release);
    scroll.addEventListener("scroll", () => { host.__mdz = { scale, user, ...centre() }; }, { passive: true });

    // The message column changes width (window resize, sidebar): the default view follows it.
    let lastWidth = 0;
    const resize = () => {
      const width = viewWidth();
      if (!stage.isConnected || Math.abs(width - lastWidth) < 2) return;
      lastWidth = width;
      sizeViewport();
      if (!user) reset(); else apply(scale);
    };
    function start() {
      label = textSize(svg);
      if (typeof ResizeObserver === "function") new ResizeObserver(() => requestAnimationFrame(resize)).observe(stage);
      lastWidth = viewWidth();
      sizeViewport();
      const kept = host.__mdz;
      if (kept && kept.user) { user = true; apply(kept.scale); centreOn(kept.x, kept.y); }
      else reset();
      stage.classList.add("mdz-ready");
    }
    if (opts.full || typeof IntersectionObserver !== "function") start();
    else {
      scroll.style.height = Math.round(clamp(H + 16, 96, maxHeight())) + "px";
      const seen = new IntersectionObserver((entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        seen.disconnect();
        // off the scrolling frame: idle time (at most 300 ms later), before the card reaches the screen
        const run = () => { if (stage.isConnected) start(); };
        if (typeof requestIdleCallback === "function") requestIdleCallback(run, { timeout: 300 }); else setTimeout(run, 0);
      }, { rootMargin: "900px 0px" });
      seen.observe(stage);
    }
    return { reset, zoomBy, set, get scale() { return scale; }, fit: () => set(fitScale(true)), get textSize() { return label; } };
  }

  window.MemuDiagramZoom = Object.freeze({ attach, MIN_TEXT });
})();
