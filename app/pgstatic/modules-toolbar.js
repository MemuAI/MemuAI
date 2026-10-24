/* Round 30: one header for module windows.
 *
 * A module does not draw its own top bar any more. It hands its actions to the
 * MemuAI window through the bridge (Memu.toolbar.set → action "toolbar"), and
 * modules.js renders them with this file:
 *   - floating / snapped window — in the window's single title row, between the
 *     module name and the window buttons;
 *   - expanded sheet — in the MemuAI top bar itself (the sheet loses its own row).
 *
 * Module data is untrusted: labels go in as text, icons are either a name from
 * the host set or SVG rebuilt element by element from a whitelist.
 */
"use strict";

const MemuModuleToolbar = (() => {
  const LIMIT = 16;
  const MENU_LIMIT = 40;
  const KINDS = new Set(["button", "toggle", "meter", "text", "separator"]);

  // Icons the host has on top of app.js ICONS (names modules commonly need).
  const EXTRA = {
    note: '<svg viewBox="0 0 24 24"><path d="M5 4h14v12l-4 4H5z"/><path d="M15 20v-4h4M8 9h8M8 13h5"/></svg>',
    panel: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
    filter: '<svg viewBox="0 0 24 24"><path d="M4 7h16M7 12h10M10 17h4"/></svg>',
    star: '<svg viewBox="0 0 24 24"><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
    upload: '<svg viewBox="0 0 24 24"><path d="M12 21V9m-5 5 5-5 5 5M5 3h14"/></svg>',
    kebab: '<svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>',
    sort: '<svg viewBox="0 0 24 24"><path d="M7 4v16m-3-3 3 3 3-3M17 20V4m-3 3 3-3 3 3"/></svg>',
    grid: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    help: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17h.01"/></svg>',
    history: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
    sliders: '<svg viewBox="0 0 24 24"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
    tag: '<svg viewBox="0 0 24 24"><path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="7.5" r="1.3"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg>',
    overflow: '<svg viewBox="0 0 24 24"><path d="m6 7 5 5-5 5M13 7l5 5-5 5"/></svg>',
    minus: '<svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg>',
  };

  const SVG_NS = "http://www.w3.org/2000/svg";
  const SVG_TAGS = new Set(["svg", "g", "path", "circle", "rect", "line", "polyline", "polygon", "ellipse"]);
  const SVG_ATTRS = new Set(["d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height",
    "points", "viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "fill-rule",
    "clip-rule", "opacity", "fill-opacity", "stroke-opacity", "stroke-dasharray", "transform"]);
  // Geometry and paint only: no url(), no javascript:, no entities.
  const SAFE_VALUE = /^[#%\w\s.,()+-]*$/;

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const t = (ru, english) => (en() ? english : ru);

  function clean(value, max) {
    return String(value == null ? "" : value).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, max);
  }

  function cleanId(value) {
    return String(value == null ? "" : value).replace(/[^\w.:-]/g, "").slice(0, 60);
  }

  function normalizeEntry(raw) {
    if (!raw || typeof raw !== "object") return null;
    if (raw.sep || raw.kind === "separator") return { sep: true };
    const id = cleanId(raw.id);
    const label = clean(raw.label, 80);
    if (!id || !label) return null;
    return { id, label, icon: typeof raw.icon === "string" ? raw.icon.slice(0, 4000) : "",
      danger: !!raw.danger, checked: !!raw.checked, disabled: !!raw.disabled, hint: clean(raw.hint, 24) };
  }

  /** Untrusted spec from the bridge → a clean one. */
  function normalize(spec) {
    const source = spec && typeof spec === "object" ? spec : {};
    const items = (Array.isArray(source.items) ? source.items : []).slice(0, LIMIT).map((raw) => {
      if (!raw || typeof raw !== "object") return null;
      const kind = KINDS.has(raw.kind) ? raw.kind : (raw.sep ? "separator" : "button");
      if (kind === "separator") return { kind, id: "" };
      const id = cleanId(raw.id);
      if (!id) return null;
      const item = {
        id, kind,
        icon: typeof raw.icon === "string" ? raw.icon.slice(0, 4000) : "",
        label: clean(raw.label, 60),
        title: clean(raw.title, 160),
        pressed: !!raw.pressed,
        disabled: !!raw.disabled,
        dot: !!raw.dot,
        primary: !!raw.primary,
        pin: !!raw.pin,
        showLabel: !!raw.showLabel,
        value: Math.max(0, Math.min(1, Number(raw.value) || 0)),
        tone: ["", "ok", "warn", "danger"].includes(raw.tone) ? raw.tone : "",
        menu: Array.isArray(raw.menu) ? raw.menu.slice(0, MENU_LIMIT).map(normalizeEntry).filter(Boolean) : null,
      };
      if (!item.icon && !item.label && kind !== "meter") return null;
      return item;
    }).filter(Boolean);
    return { title: clean(source.title, 120), status: clean(source.status, 120), items };
  }

  function sanitizeSvg(markup) {
    if (typeof DOMParser === "undefined") return null;
    let doc;
    try { doc = new DOMParser().parseFromString(markup, "image/svg+xml"); } catch (error) { return null; }
    const src = doc && doc.documentElement;
    if (!src || src.nodeName.toLowerCase() !== "svg" || doc.getElementsByTagName("parsererror").length) return null;
    let budget = 64;
    const copy = (node) => {
      const tag = String(node.nodeName || "").toLowerCase();
      if (!SVG_TAGS.has(tag) || budget-- <= 0) return null;
      const out = document.createElementNS(SVG_NS, tag);
      Array.from(node.attributes || []).forEach((attr) => {
        const name = attr.name;
        if (SVG_ATTRS.has(name) && SAFE_VALUE.test(attr.value) && !/url|script/i.test(attr.value)) out.setAttribute(name, attr.value.slice(0, 2000));
      });
      Array.from(node.children || []).forEach((child) => { const c = copy(child); if (c) out.appendChild(c); });
      return out;
    };
    const svg = copy(src);
    if (svg && !svg.getAttribute("viewBox")) svg.setAttribute("viewBox", "0 0 24 24");
    return svg;
  }

  function iconMarkup(name) {
    if (typeof ICONS !== "undefined" && ICONS[name]) return ICONS[name];
    if (name === "refresh" && typeof ICONS !== "undefined" && ICONS.retry && !EXTRA.refresh) return ICONS.retry;
    return EXTRA[name] || "";
  }

  /** A trusted <span class="ui-icon"> for a host icon name or sanitized SVG. */
  function iconNode(value) {
    const name = String(value || "").trim();
    if (!name) return null;
    const span = document.createElement("span");
    span.className = "ui-icon";
    if (/^<svg[\s>]/i.test(name)) {
      const svg = sanitizeSvg(name);
      if (!svg) return null;
      span.appendChild(svg);
      return span;
    }
    const markup = iconMarkup(name);
    if (!markup) return null;
    span.innerHTML = markup; // host-owned markup only
    return span;
  }

  function hasIcon(name) { return !!iconMarkup(String(name || "")); }

  /* Menus are drawn by app.js showContextMenu, which takes ICONS names. A host
     extra or a module's SVG (rebuilt from the whitelist, then serialized) is
     registered there under its own key, at most a few hundred of them. */
  const registered = new Map();
  function menuIcon(value) {
    const name = String(value || "").trim();
    if (!name || typeof ICONS === "undefined") return "";
    if (ICONS[name] && !/^mtb-/.test(name)) return name;
    if (registered.has(name)) return registered.get(name);
    let markup = "";
    if (/^<svg[\s>]/i.test(name)) {
      const svg = sanitizeSvg(name);
      if (svg && typeof XMLSerializer !== "undefined") markup = new XMLSerializer().serializeToString(svg);
    } else markup = EXTRA[name] || "";
    if (!markup || registered.size > 300) return "";
    const key = "mtb-" + registered.size;
    ICONS[key] = markup;
    registered.set(name, key);
    return key;
  }

  function itemButton(item, onPick) {
    if (item.kind === "separator") {
      const sep = document.createElement("span");
      sep.className = "mtb-sep";
      sep.setAttribute("aria-hidden", "true");
      return sep;
    }
    if (item.kind === "text") {
      const text = document.createElement("span");
      text.className = "mtb-text";
      text.textContent = item.label;
      if (item.title) text.title = item.title;
      return text;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mtb-btn";
    button.dataset.mtb = item.id;
    const tip = item.title || item.label;
    if (tip) { button.title = tip; button.setAttribute("aria-label", tip); }
    if (item.kind === "meter") {
      button.classList.add("mtb-meter");
      if (item.tone) button.dataset.tone = item.tone;
      const bar = document.createElement("span");
      bar.className = "mtb-meter-bar";
      const fill = document.createElement("i");
      fill.style.width = (item.value * 100).toFixed(1) + "%";
      bar.appendChild(fill);
      const label = document.createElement("span");
      label.className = "mtb-meter-label";
      label.textContent = item.label;
      button.append(bar, label);
    } else {
      const glyph = iconNode(item.icon);
      if (glyph) button.appendChild(glyph);
      if (item.label && (!glyph || item.primary || item.showLabel)) {
        const label = document.createElement("span");
        label.className = "mtb-label";
        label.textContent = item.label;
        button.appendChild(label);
        button.classList.add("has-label");
      }
      if (!glyph && item.label) button.classList.add("text-only");
    }
    if (item.kind === "toggle") button.setAttribute("aria-pressed", item.pressed ? "true" : "false");
    if (item.primary) button.classList.add("primary");
    if (item.dot) button.classList.add("dot");
    if (item.menu) button.setAttribute("aria-haspopup", "menu");
    button.disabled = item.disabled;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (item.menu) openMenu(item, button, onPick);
      else onPick(item, "", button);
    });
    return button;
  }

  function openMenu(item, anchor, onPick) {
    const rect = anchor.getBoundingClientRect();
    const entries = item.menu.map((entry) => {
      if (entry.sep) return { sep: true };
      if (entry.disabled) return { info: true, label: entry.label };
      return { label: entry.label, icon: entry.checked ? "check" : menuIcon(entry.icon),
        danger: entry.danger, hint: entry.hint, run: () => onPick(item, entry.id, anchor) };
    });
    if (typeof showContextMenu === "function") showContextMenu(entries, rect.right - 4, rect.bottom + 4);
  }

  /** Overflow: the actions that do not fit go to one «more» menu. */
  function fit(container) {
    if (!container || !container.isConnected) return;
    const spec = container._mtbSpec;
    const onPick = container._mtbPick;
    if (!spec) return;
    const nodes = Array.from(container.querySelectorAll(":scope > [data-mtb], :scope > .mtb-sep, :scope > .mtb-text"));
    nodes.forEach((node) => { node.hidden = false; });
    let more = container.querySelector(":scope > .mtb-overflow");
    if (more) more.hidden = true;
    // In a window header the actions do not shrink (modules.css): the subtitle gives way first, and an action
    // folds only when the whole header still overflows. Measuring the strip alone folded the token meter while
    // the chat title still had room to give (r30 live chat).
    const host = container.closest(".module-headbar, .module-window-bar");
    const overflows = () => container.scrollWidth > container.clientWidth + 1 || (!!host && host.scrollWidth > host.clientWidth + 1);
    if (!overflows()) return;
    if (!more) {
      more = document.createElement("button");
      more.type = "button";
      more.className = "mtb-btn mtb-overflow";
      more.setAttribute("aria-haspopup", "menu");
      const glyph = iconNode("overflow");
      if (glyph) more.appendChild(glyph);
      container.appendChild(more);
    }
    more.title = t("Ещё действия модуля", "More module actions");
    more.setAttribute("aria-label", more.title);
    more.hidden = false;
    const hidden = [];
    // Hide from the start of the list: the rightmost actions sit next to the
    // window buttons and are the ones people reach for (panel, menu).
    for (let i = 0; i < spec.items.length && overflows(); i += 1) {
      const item = spec.items[i];
      if (item.pin) continue;
      const node = nodes[i];
      if (!node) continue;
      node.hidden = true;
      if (item.kind !== "separator") hidden.push(item);
    }
    more.onclick = (event) => {
      event.stopPropagation();
      const rect = more.getBoundingClientRect();
      const entries = [];
      hidden.forEach((item) => {
        if (item.kind === "text") { entries.push({ info: true, label: item.label }); return; }
        if (item.menu) {
          entries.push({ sep: true }, { info: true, label: item.title || item.label });
          item.menu.forEach((entry) => {
            if (entry.sep) entries.push({ sep: true });
            else if (!entry.disabled) entries.push({ label: entry.label, icon: entry.checked ? "check" : menuIcon(entry.icon), danger: entry.danger, run: () => onPick(item, entry.id, more) });
          });
          entries.push({ sep: true });
          return;
        }
        const label = item.kind === "meter" ? [item.title, item.label].filter(Boolean).join(" · ") : (item.title || item.label);
        entries.push({ label: (item.kind === "toggle" && item.pressed ? "✓ " : "") + label,
          icon: menuIcon(item.icon), run: () => onPick(item, "", more) });
      });
      if (typeof showContextMenu === "function") showContextMenu(entries, rect.right - 4, rect.bottom + 4);
    };
  }

  /** Render a clean spec's items into container; onPick(item, value, anchor). */
  function render(container, spec, onPick) {
    if (!container) return;
    container._mtbSpec = spec;
    container._mtbPick = onPick;
    container.replaceChildren(...spec.items.map((item) => itemButton(item, onPick)));
    container.hidden = !spec.items.length;
    if (typeof ResizeObserver !== "undefined" && !container._mtbObserver) {
      container._mtbObserver = new ResizeObserver(() => fit(container));
      container._mtbObserver.observe(container.parentElement || container);
    }
    fit(container);
  }

  return { normalize, render, fit, iconNode, hasIcon, sanitizeSvg, LIMIT };
})();
window.MemuModuleToolbar = MemuModuleToolbar;
