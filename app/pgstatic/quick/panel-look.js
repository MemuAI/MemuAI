/* Round 30 — how a MemuAI panel looks in Quick Access (window grid, screen layer, the section built into the window).

   window.MemuPanelLook = { STYLES, DEFAULT, resolve(doc, panelId, {embed}), apply(el, look, {place}), faceHtml(panel, look,
   {size, place}), theme(look), label(style) }. The data is app/quick_panel_look.py: `look` (global), `looks[panelId]`
   (one panel's own, partial) and `embed.style` (the built-in section's default) — all in GET /api/quick/canvas.

   «card» keeps panels.js' own card (MemuPanels.cardHtml); the other styles are drawn here from MemuPanels.linesOf /
   miniOf: «compact» — one tight row (icon · name · value) plus small secondary lines, «text» — only lines of values,
   no background and no frame (on the screen layer with a soft shadow so it stays readable over any wallpaper),
   «icon» — a big icon and the value. Background opacity, font size, accent, radius, shadow and a forced light / dark /
   Windows theme are CSS variables + data attributes on the item element (pgstatic/quick/panel-look.css). */
(function () {
  "use strict";
  const STYLES = ["card", "compact", "text", "icon"];
  const DEFAULT = { style: "card", opacity: 100, font: 100, accent: "", theme: "app", titles: true, radius: 14, shadow: true, dim: 0, lines: 3 };
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const lang = () => (document.documentElement.lang === "en" ? "en" : "ru");
  const t = (ru, en) => (lang() === "en" ? en : ru);
  const LABELS = { card: ["Карточка", "Card"], compact: ["Компактно", "Compact"], text: ["Только текст", "Text only"], icon: ["Иконка + значение", "Icon + value"] };

  function resolve(doc, panelId, opts = {}) {
    const out = { ...DEFAULT, ...((doc && doc.look) || {}) };
    const embedStyle = opts.embed && doc && doc.embed ? doc.embed.style : "";
    if (STYLES.includes(embedStyle)) out.style = embedStyle;
    Object.assign(out, (doc && doc.looks && doc.looks[panelId]) || {});
    if (!STYLES.includes(out.style)) out.style = "card";
    return out;
  }
  const systemDark = () => !!(window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
  /** "light" | "dark" | "" (= follow the app's theme: nothing to force). */
  function theme(look) {
    if (look.theme === "light" || look.theme === "dark") return look.theme;
    if (look.theme === "auto") return systemDark() ? "dark" : "light";
    return "";
  }
  /** Put a look on an item element (the grid cell / the layer item / an embedded tile). */
  function apply(el, look, opts = {}) {
    if (!el) return;
    const d = el.dataset;
    d.plStyle = look.style;
    d.plTitles = look.titles === false ? "0" : "1";
    d.plShadow = look.shadow === false ? "0" : "1";
    d.plPlace = opts.place || "";
    const th = theme(look);
    if (th) d.plTheme = th; else delete d.plTheme;
    if (look.dim > 0) d.plDim = "1"; else delete d.plDim;
    el.style.setProperty("--pl-op", String(Math.max(0, Math.min(100, +look.opacity || 0)) / 100));
    el.style.setProperty("--pl-font", String(Math.max(80, Math.min(140, +look.font || 100)) / 100));
    el.style.setProperty("--pl-radius", Math.max(0, Math.min(24, +look.radius || 0)) + "px");
    el.style.setProperty("--pl-dim", String(1 - Math.max(0, Math.min(90, +look.dim || 0)) / 100));
    if (/^#[0-9a-f]{6}$/i.test(look.accent || "")) el.style.setProperty("--accent", look.accent);
    else el.style.removeProperty("--accent");
  }

  function stateOf(panel) {
    const hasData = Object.keys(panel.data || {}).length > 0;
    if (panel.status === "setup") return "setup";
    if (!hasData && (panel.status === "failed" || panel.wall)) return "wall";
    if (!hasData) return "building";
    return panel.wall ? "stale" : "live";
  }
  const unitHtml = (u) => (u ? `<small class="pl-u">${esc(u)}</small>` : "");
  // Round 31: a value that is a phrase (a headline of a news / RSS / AI panel, «Ближайший матч не указан») is not a
  // number: it gets an ellipsis and regular weight instead of being cut mid-letter at the tile's edge.
  const phrase = (v) => { const s = String(v == null ? "" : v); return s.length > 14 && /[A-Za-zА-Яа-яЁё]{3,}/.test(s) && /\s/.test(s); };
  // the chip label is often the title cut short («последняя версия Pyt», «Top 5 Hacker News…»): not a second caption
  const sameAsTitle = (label, title) => { const a = String(label || "").replace(/…$/, "").trim().toLowerCase(); return !!a && String(title || "").trim().toLowerCase().startsWith(a); };
  const ph = (l) => (phrase(l && l.value) ? " pl-phrase" : "");
  const trendHtml = (tr) => (tr === "up" || tr === "down" ? `<i class="pl-tr" data-trend="${tr}" aria-hidden="true">${tr === "up" ? "▲" : "▼"}</i>` : "");

  /** The face of a non-card style (null for «card»: the caller draws MemuPanels.cardHtml). */
  function faceHtml(panel, look, opts = {}) {
    if (!panel || look.style === "card" || !window.MemuPanels || typeof MemuPanels.linesOf !== "function") return null;
    const P = window.MemuPanels;
    const st = stateOf(panel);
    const lines = st === "live" || st === "stale" ? P.linesOf(panel, Math.max(1, Math.min(5, look.lines || 3))) : [];
    const mini = P.miniOf(panel);
    const icon = P.ico(st === "setup" ? "locate" : (mini.icon || P.iconFor(panel)));
    const title = esc(panel.title || "");
    const empty = st === "setup" ? t("Укажите место", "Set a place") : st === "building" ? t("Собираю…", "Gathering…")
      : st === "wall" ? t("Нет данных", "No data") : "—";
    const first = lines[0] || { label: panel.title, value: empty, unit: "" };
    const rest = lines.slice(1);
    const tip = esc(lines.map((l) => [l.label, l.value + (l.unit ? " " + l.unit : "")].filter(Boolean).join(": ")).join("\n") || panel.title);
    const common = `class="pl-face pl-${look.style}" data-state="${st}" data-panel-id="${esc(panel.id)}" title="${tip}"`;
    if (look.style === "icon") {
      return `<div ${common}><span class="pl-ic">${icon}</span><b class="pl-big">${esc(first.value)}${unitHtml(first.unit)}${trendHtml(first.trend)}</b>
        ${look.titles !== false ? `<span class="pl-cap">${title}</span>` : ""}</div>`;
    }
    if (look.style === "compact") {
      return `<div ${common}><div class="pl-row pl-main"><span class="pl-ic">${icon}</span>${look.titles !== false ? `<span class="pl-name">${title}</span>` : ""}
        <b class="pl-v">${esc(first.value)}${unitHtml(first.unit)}${trendHtml(first.trend)}</b></div>
        ${rest.map((l) => `<div class="pl-row pl-sub${ph(l)}"><span class="pl-l">${esc(l.label || "")}</span><b class="pl-v">${esc(l.value)}${unitHtml(l.unit)}${trendHtml(l.trend)}</b></div>`).join("")}</div>`;
    }
    // Round 32: «text» is real text — the panel's own template (or the automatic one) with its values (panel-text.js)
    if (window.MemuPanelText && (st === "live" || st === "stale")) {
      try { return window.MemuPanelText.faceHtml(panel, look, { state: st, place: opts.place || "", titles: look.titles !== false }); } catch (e) { /* the plain lines below */ }
    }
    // text: lines only — the panel's name as a quiet caption when titles are on
    const all = lines.length ? lines : [first];
    return `<div ${common}>${look.titles !== false ? `<div class="pl-cap">${title}</div>` : ""}
      ${all.map((l, i) => `<div class="pl-line${i === 0 ? " pl-first" : ""}${ph(l)}">${l.label && (!sameAsTitle(l.label, panel.title) || look.titles === false) ? `<span class="pl-l">${esc(l.label)}</span>` : ""}<b class="pl-v">${esc(l.value)}${unitHtml(l.unit)}${trendHtml(l.trend)}</b></div>`).join("")}</div>`;
  }
  /** One value line of a panel as plain text (the «Копировать значение» menu item). */
  function valueText(panel, look) {
    if (panel && look && look.style === "text" && window.MemuPanelText) { try { const s = window.MemuPanelText.plainText(panel, look); if (s) return s; } catch (e) { /* lines */ } }
    if (!panel || !window.MemuPanels || typeof MemuPanels.linesOf !== "function") return "";
    return MemuPanels.linesOf(panel, (look && look.lines) || 3).map((l) => [l.label, `${l.value}${l.unit ? " " + l.unit : ""}`].filter(Boolean).join(": ")).join("\n");
  }
  const label = (style) => { const p = LABELS[style] || LABELS.card; return t(p[0], p[1]); };
  /** The «Вид» segmented row of the item menus (pgstatic/memu-ctxmenu.js). */
  function menuSeg(look) {
    return { type: "seg", id: "pl-style", label: t("Вид", "Look"), icon: "grid", options: STYLES.map((s) => ({
      id: s, label: { card: t("Карт.", "Card"), compact: t("Комп.", "Comp."), text: t("Текст", "Text"), icon: t("Иконка", "Icon") }[s], title: label(s), pressed: look.style === s })) };
  }

  // Round 32: the text templates (panel-text.js + .css) come with the looks, whoever loaded this file; a page re-draws
  // its panels on the «memu-panel-text» event (until then «text» shows the plain value lines).
  (function loadText() {
    if (window.MemuPanelText || typeof document.createElement !== "function" || !document.head || document.querySelector("script[data-pt-js]")) return;
    const me = document.currentScript && document.currentScript.src ? document.currentScript.src : "/quick/panel-look.js";
    const base = me.replace(/panel-look\.js(\?.*)?$/, "");
    const v = "?v=1.0-r32";
    if (!document.querySelector("link[data-pt-css]")) {
      const link = document.createElement("link");
      link.rel = "stylesheet"; link.href = base + "panel-text.css" + v; link.dataset.ptCss = "1";
      document.head.appendChild(link);
    }
    const s = document.createElement("script");
    s.src = base + "panel-text.js" + v; s.dataset.ptJs = "1"; s.async = false;
    document.head.appendChild(s);
  })();

  window.MemuPanelLook = { STYLES, DEFAULT, resolve, apply, faceHtml, theme, label, menuSeg, valueText };
})();
