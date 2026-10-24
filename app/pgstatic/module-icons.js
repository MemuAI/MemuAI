/* Round 39 — module icons (server: app/module_icons.py).
 *
 * window.MemuModuleIcon:
 *   html(row)          — the icon of a module row: its picture (row.icon_url: the person's icon or the manifest's
 *                        SVG/PNG) or, for a module with only a glyph name, the glyph on a MemuAI squircle in
 *                        row.icon_bg / a gradient picked from the id. Every copy carries data-mico="<id>" so a
 *                        change repaints all of them in place (sidebar, pills, window bar, cards…).
 *   png(row, size)     — the same icon drawn on a canvas (PNG data URL) — module app .ico, shortcuts.
 *   picker(row)        — «Change icon»: the MemuAI set, a glyph on a gradient, an emoji on a gradient, a file
 *                        (SVG/PNG/ICO/WebP), reset to the module's own icon.
 *   repaint(id)        — redraw every copy of one module's icon from the fresh catalogue.
 * Loaded before modules.js; nothing runs until a module icon is drawn (no timers, no observers).
 */
(function () {
  "use strict";
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const t = (ru, english) => (en() ? english : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const ic = (name) => (typeof icon === "function" ? icon(name) : "");
  const M = () => window.MemuModules;
  const note = (text, type) => { try { if (typeof toast === "function") toast(text, type || "ok"); } catch (_) { /* no toast */ } };

  /* The MemuAI family gradients (the built-in module icons use the same pairs). */
  const GRADIENTS = [
    { id: "memu", a: "#f08c62", b: "#8752cf", ru: "MemuAI", en: "MemuAI" },
    { id: "berry", a: "#ff6a88", b: "#9b3fd1", ru: "Ягода", en: "Berry" },
    { id: "sunset", a: "#f45fb3", b: "#fb9440", ru: "Закат", en: "Sunset" },
    { id: "coral", a: "#ff9a5a", b: "#e0407a", ru: "Коралл", en: "Coral" },
    { id: "amber", a: "#ffbe4a", b: "#ee6a3a", ru: "Янтарь", en: "Amber" },
    { id: "mint", a: "#3ad49a", b: "#168f9f", ru: "Мята", en: "Mint" },
    { id: "lagoon", a: "#29c3d6", b: "#4258d6", ru: "Лагуна", en: "Lagoon" },
    { id: "indigo", a: "#5f86f7", b: "#6142d4", ru: "Индиго", en: "Indigo" },
    { id: "violet", a: "#c56cf0", b: "#5b45d8", ru: "Фиалка", en: "Violet" },
    { id: "graphite", a: "#6b7180", b: "#2a2d36", ru: "Графит", en: "Graphite" },
  ];
  const BUILTINS = ["music", "translator", "studio", "design", "benchmarks", "catalog", "api-models", "characters", "learn"];   // r40-learn: + MemuLearn
  const GLYPHS = ["work", "sparkle", "chat", "text", "zap", "image", "bot", "palette", "globe", "search", "list", "clock",
    "bell", "user", "users", "monitor", "terminal", "target", "mic", "headphones", "music", "chart", "folder", "brain",
    "download", "send", "pin", "edit", "settings", "keyboard", "moon", "sun", "check", "focus"];
  const EMOJI = ["🎵", "🎨", "🌐", "📊", "🤖", "🔔", "👥", "🖼️", "⚡", "📝", "🧠", "🎮", "📚", "🚀", "💡", "🔥", "⭐", "🧪", "🗂️", "🎬", "🌙", "☕", "🛠️", "🧩"];

  const hash = (text) => [...String(text || "")].reduce((h, ch) => (h * 31 + ch.codePointAt(0)) >>> 0, 7);
  const gradientById = (id) => GRADIENTS.find((g) => g.id === id) || GRADIENTS[0];
  function colorsOf(row) {
    const bg = (row && Array.isArray(row.icon_bg)) ? row.icon_bg.filter((c) => /^#[0-9a-f]{6}$/i.test(c)) : [];
    if (bg.length >= 2) return [bg[0], bg[bg.length - 1]];
    if (bg.length === 1) return [bg[0], `color-mix(in srgb, ${bg[0]} 60%, #1b1030)`];
    if (row && /^#[0-9a-f]{6}$/i.test(row.accent || "")) return [row.accent, `color-mix(in srgb, ${row.accent} 55%, #2a1048)`];
    const pool = GRADIENTS.slice(0, -1);
    const g = pool[hash(row && row.id) % pool.length];
    return [g.a, g.b];
  }
  const glyphName = (name) => (typeof ICONS !== "undefined" && ICONS[name] ? name : "work");

  function html(row) {
    const id = esc((row && row.id) || "");
    if (row && row.icon_url) {
      return `<span class="mico" data-mico="${id}" aria-hidden="true"><img src="${esc(row.icon_url)}" alt="" draggable="false" decoding="async"></span>`;
    }
    const [a, b] = colorsOf(row);
    return `<span class="mico mico-glyph" data-mico="${id}" aria-hidden="true" style="--mico-a:${esc(a)};--mico-b:${esc(b)}">${ic(glyphName(row && row.icon))}</span>`;
  }

  /* ------------------------------------------------ SVG the picker composes (glyph / emoji on a squircle) */
  function tileSvg(a, b, inner) {
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs>'
      + `<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient>`
      + '<radialGradient id="light" cx="0.22" cy="0.1" r="0.7"><stop offset="0" stop-color="#ffffff" stop-opacity=".38"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>'
      + '<linearGradient id="rim" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff" stop-opacity=".55"/><stop offset=".45" stop-color="#ffffff" stop-opacity="0"/><stop offset="1" stop-color="#000000" stop-opacity=".12"/></linearGradient>'
      + '<filter id="shadow" x="-25%" y="-25%" width="150%" height="160%"><feDropShadow dx="0" dy="10" stdDeviation="13" flood-color="#1b1030" flood-opacity=".3"/></filter></defs>'
      + '<rect x="28" y="28" width="456" height="456" rx="118" fill="url(#bg)"/><rect x="28" y="28" width="456" height="456" rx="118" fill="url(#light)"/>'
      + '<rect x="30" y="30" width="452" height="452" rx="116" fill="none" stroke="url(#rim)" stroke-width="4"/>'
      + `<g filter="url(#shadow)">${inner}</g></svg>`;
  }
  function glyphSvg(name, gradient) {
    const g = gradientById(gradient);
    const markup = ic(glyphName(name));
    const inner = ((markup.match(/<svg[^>]*>([\s\S]*)<\/svg>/) || [])[1] || "");
    return tileSvg(g.a, g.b, `<g transform="translate(256 256) scale(11.5) translate(-12 -12)" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">${inner}</g>`);
  }
  function emojiSvg(emoji, gradient) {
    const g = gradientById(gradient);
    return tileSvg(g.a, g.b, `<text x="256" y="270" font-size="268" text-anchor="middle" dominant-baseline="central" font-family="Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji, sans-serif">${esc(emoji)}</text>`);
  }
  const svgUrl = (svg) => "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);

  /* ------------------------------------------------ raster */
  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("image"));
      img.src = src;
    });
  }
  async function drawPng(src, size = 256) {
    try {
      const img = await loadImage(src);
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const g = canvas.getContext("2d");
      const w = img.naturalWidth || size, h = img.naturalHeight || size, k = Math.min(size / w, size / h);
      g.imageSmoothingQuality = "high";
      g.drawImage(img, (size - w * k) / 2, (size - h * k) / 2, w * k, h * k);
      return canvas.toDataURL("image/png");
    } catch (_) { return ""; }
  }
  function png(row, size = 256) {
    if (row && row.icon_url) return drawPng(row.icon_url, size);
    const [a, b] = colorsOf(row);
    const solid = (c) => (c.startsWith("#") ? c : a);  // a canvas gradient takes no color-mix()
    const markup = ic(glyphName(row && row.icon));
    const inner = ((markup.match(/<svg[^>]*>([\s\S]*)<\/svg>/) || [])[1] || "");
    const svg = tileSvg(solid(a), solid(b), `<g transform="translate(256 256) scale(11.5) translate(-12 -12)" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">${inner}</g>`);
    return drawPng(svgUrl(svg), size);
  }

  /* ------------------------------------------------ repaint every copy of one module's icon */
  function rowOf(id) { return ((M() && M().catalog) || []).find((row) => row.id === id) || null; }
  function repaint(id) {
    const row = rowOf(id);
    if (!row) return;
    const fresh = html(row);
    document.querySelectorAll(`[data-mico="${CSS.escape(id)}"]`).forEach((node) => { node.outerHTML = fresh; });
  }

  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  /* ------------------------------------------------ the picker */
  function picker(target) {
    const row = (target && rowOf(target.id)) || target;
    if (!row || typeof panel !== "function") return;
    const meta = row.icon_meta || {};
    const st = { tab: meta.emoji ? "emoji" : "set", gradient: meta.bg || "memu", glyph: meta.glyph || row.icon || "work",
      emoji: meta.emoji || "🎵", choice: null, file: null, busy: false };
    const title = en() ? (row.title_en || row.title) : (row.title || row.title_en);
    const swatches = () => GRADIENTS.map((g) => `<button type="button" class="mico-swatch${st.gradient === g.id ? " on" : ""}" data-mico-grad="${g.id}" style="--mico-a:${g.a};--mico-b:${g.b}" title="${esc(en() ? g.en : g.ru)}" aria-label="${esc(en() ? g.en : g.ru)}" aria-pressed="${st.gradient === g.id}"></button>`).join("");
    const isOn = (kind, value) => st.choice && st.choice.kind === kind && st.choice.value === value;
    function body() {
      if (st.tab === "set") {
        return `<p class="mico-cap">${esc(t("Набор MemuAI", "The MemuAI set"))}</p>
          <div class="mico-grid big">${BUILTINS.map((id) => `<button type="button" class="mico-opt${isOn("preset", id) ? " on" : ""}" data-mico-preset="${id}" aria-label="${esc(id)}"><img src="/api/pg/module-icons/builtin/${id}.svg" alt="" draggable="false"></button>`).join("")}</div>
          <p class="mico-cap">${esc(t("Значок на градиенте", "A glyph on a gradient"))}</p>
          <div class="mico-swatches">${swatches()}</div>
          <div class="mico-grid">${GLYPHS.filter((name) => typeof ICONS === "undefined" || ICONS[name]).map((name) => `<button type="button" class="mico-opt${isOn("glyph", name) ? " on" : ""}" data-mico-glyph="${name}" aria-label="${esc(name)}"><img src="${svgUrl(glyphSvg(name, st.gradient))}" alt="" draggable="false"></button>`).join("")}</div>`;
      }
      if (st.tab === "emoji") {
        return `<p class="mico-cap">${esc(t("Эмодзи — вставьте любой или выберите", "Emoji — paste any or pick one"))}</p>
          <div class="mico-emoji-row"><input id="mico-emoji" class="mico-emoji-input" maxlength="16" value="${esc(st.emoji)}" aria-label="${esc(t("Эмодзи", "Emoji"))}" autocomplete="off" spellcheck="false">
            <div class="mico-emoji-pick">${EMOJI.map((e) => `<button type="button" data-mico-emoji="${esc(e)}">${esc(e)}</button>`).join("")}</div></div>
          <p class="mico-cap">${esc(t("Фон", "Background"))}</p><div class="mico-swatches">${swatches()}</div>`;
      }
      return `<label class="mico-drop" id="mico-drop"><input type="file" id="mico-file" accept=".svg,.png,.ico,.webp,image/svg+xml,image/png,image/x-icon,image/vnd.microsoft.icon,image/webp" hidden>
          ${ic("attach")}<b>${esc(t("Выберите или перетащите файл", "Choose or drop a file"))}</b>
          <small>${esc(t("SVG, PNG, ICO или WebP до 768 КБ. Квадратная картинка от 256 px выглядит лучше всего; SVG очищается от скриптов и внешних ссылок.",
            "SVG, PNG, ICO or WebP up to 768 KB. A square image of 256 px or more looks best; an SVG is cleaned of scripts and external links."))}</small>
          ${st.file ? `<span class="mico-file-name">${esc(st.file.name)}</span>` : ""}</label>`;
    }
    function currentSrc() {
      if (st.choice) return st.choice.src;
      return "";
    }
    function preview() {
      const src = currentSrc();
      const cell = (size) => `<span class="mico-pv-cell"><span class="mico" style="--mico-size:${size}px">${src ? `<img src="${esc(src)}" alt="">` : ""}</span><small>${size}</small></span>`;
      if (!src) return `<span class="mico-pv-now">${[64, 32, 20, 16].map((size) => `<span class="mico-pv-cell"><span style="--mico-size:${size}px;display:inline-flex">${html(row)}</span><small>${size}</small></span>`).join("")}</span>`;
      return `<span class="mico-pv-now">${[64, 32, 20, 16].map(cell).join("")}</span>`;
    }
    const markup = `<div class="mico-dialog">
        <div class="mico-head"><div class="mico-head-text"><h2>${esc(t(`Иконка «${title}»`, `“${title}” icon`))}</h2>
          <p class="sub">${esc(t("Видна везде: боковая панель, вкладки, каталог модулей, Быстрый доступ, окно и ярлык приложения.",
            "Shown everywhere: the sidebar, tabs, the module list, Quick access, the app window and shortcut."))}</p></div>
          <div class="mico-pv" id="mico-pv">${preview()}</div></div>
        <div class="mico-tabs" role="tablist">${[["set", t("Набор", "Set")], ["emoji", t("Эмодзи", "Emoji")], ["file", t("Файл", "File")]].map(([id, label]) =>
          `<button type="button" role="tab" data-mico-tab="${id}" aria-selected="${st.tab === id}">${esc(label)}</button>`).join("")}</div>
        <div class="mico-body" id="mico-body">${body()}</div>
        <div class="mico-foot">
          ${row.icon_custom ? `<button type="button" class="pbtn ghost" id="mico-reset">${ic("undo")} ${esc(t("Вернуть иконку модуля", "Restore the module's icon"))}</button>` : "<span></span>"}
          <span class="mico-foot-end"><button type="button" class="pbtn ghost" id="mico-cancel">${esc(t("Отмена", "Cancel"))}</button>
          <button type="button" class="pbtn" id="mico-save" disabled>${ic("check")} ${esc(t("Сохранить", "Save"))}</button></span>
        </div></div>`;
    panel(markup, "mico-panel", {});
    document.getElementById("panel-back")?.classList.add("over-modules");
    const root = document.querySelector(".mico-dialog");
    if (!root) return;
    const $ = (sel) => root.querySelector(sel);
    const close = () => { if (typeof closePanel === "function") closePanel("cancel"); document.getElementById("panel-back")?.classList.remove("over-modules"); };
    function choose(choice) {
      st.choice = choice;
      $("#mico-pv").innerHTML = preview();
      $("#mico-save").disabled = !choice;
      root.querySelectorAll(".mico-opt.on").forEach((node) => node.classList.remove("on"));
      if (choice && choice.kind === "preset") root.querySelector(`[data-mico-preset="${choice.value}"]`)?.classList.add("on");
      if (choice && choice.kind === "glyph") root.querySelector(`[data-mico-glyph="${choice.value}"]`)?.classList.add("on");
    }
    function chooseGlyph(name) { st.glyph = name; const svg = glyphSvg(name, st.gradient); choose({ kind: "glyph", value: name, data: svg, src: svgUrl(svg) }); }
    function chooseEmoji(value) {
      const emoji = [...String(value || "").trim()].slice(0, 8).join("");
      if (!emoji) return choose(null);
      st.emoji = emoji;
      const svg = emojiSvg(emoji, st.gradient);
      choose({ kind: "emoji", value: emoji, data: svg, src: svgUrl(svg) });
    }
    function bindBody() {
      root.querySelectorAll("[data-mico-grad]").forEach((b) => { b.onclick = () => {
        st.gradient = b.dataset.micoGrad;
        root.querySelectorAll("[data-mico-grad]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
        if (st.tab === "set") {
          root.querySelectorAll("[data-mico-glyph]").forEach((x) => { x.firstElementChild.src = svgUrl(glyphSvg(x.dataset.micoGlyph, st.gradient)); });
          if (st.choice && st.choice.kind === "glyph") chooseGlyph(st.choice.value);
        } else if (st.tab === "emoji") chooseEmoji(st.emoji);
      }; });
      root.querySelectorAll("[data-mico-glyph]").forEach((b) => { b.onclick = () => chooseGlyph(b.dataset.micoGlyph); });
      root.querySelectorAll("[data-mico-preset]").forEach((b) => { b.onclick = async () => {
        try {
          const svg = await (await fetch(`/api/pg/module-icons/builtin/${b.dataset.micoPreset}.svg`)).text();
          choose({ kind: "preset", value: b.dataset.micoPreset, data: svg, src: svgUrl(svg) });
        } catch (_) { note(t("Не удалось загрузить иконку", "Could not load the icon"), "error"); }
      }; });
      root.querySelectorAll("[data-mico-emoji]").forEach((b) => { b.onclick = () => { $("#mico-emoji").value = b.dataset.micoEmoji; chooseEmoji(b.dataset.micoEmoji); }; });
      const input = $("#mico-emoji");
      if (input) input.oninput = () => chooseEmoji(input.value);
      const file = $("#mico-file"), drop = $("#mico-drop");
      const take = (f) => {
        if (!f) return;
        if (f.size > 768 * 1024) { note(t("Файл больше 768 КБ", "The file is larger than 768 KB"), "error"); return; }
        const reader = new FileReader();
        reader.onload = () => { st.file = { name: f.name }; choose({ kind: "file", value: f.name, data: String(reader.result || ""), src: String(reader.result || "") });
          const name = drop.querySelector(".mico-file-name"); if (name) name.textContent = f.name; else drop.insertAdjacentHTML("beforeend", `<span class="mico-file-name">${esc(f.name)}</span>`); };
        reader.readAsDataURL(f);
      };
      if (file) file.onchange = () => take(file.files && file.files[0]);
      if (drop) {
        drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
        drop.ondragleave = () => drop.classList.remove("over");
        drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); take(e.dataTransfer.files && e.dataTransfer.files[0]); };
      }
    }
    root.querySelectorAll("[data-mico-tab]").forEach((b) => { b.onclick = () => {
      st.tab = b.dataset.micoTab;
      root.querySelectorAll("[data-mico-tab]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      $("#mico-body").innerHTML = body();
      bindBody();
      if (st.tab === "emoji" && !st.choice) chooseEmoji(st.emoji);
    }; });
    bindBody();
    if (st.tab === "emoji") chooseEmoji(st.emoji);
    $("#mico-cancel").onclick = close;
    async function done(message) {
      if (M() && M().refresh) await M().refresh();
      repaint(row.id);
      close();
      note(message);
    }
    $("#mico-save").onclick = async () => {
      if (!st.choice || st.busy) return;
      st.busy = true; $("#mico-save").disabled = true;
      try {
        const raster = await drawPng(st.choice.src, 256);
        const meta = st.choice.kind === "glyph" ? { glyph: st.choice.value, bg: st.gradient }
          : st.choice.kind === "emoji" ? { emoji: st.choice.value, bg: st.gradient }
          : st.choice.kind === "preset" ? { preset: st.choice.value } : {};
        await api("/api/pg/module-icons/set", { id: row.id, data: st.choice.data, raster, kind: st.choice.kind, meta });
        await done(t("Иконка модуля обновлена", "The module icon is updated"));
      } catch (error) {
        note(String(error.message || error), "error");
        st.busy = false; $("#mico-save").disabled = false;
      }
    };
    const reset = $("#mico-reset");
    if (reset) reset.onclick = async () => {
      try {
        await api("/api/pg/module-icons/reset", { id: row.id });
        await done(t("Вернулась иконка модуля", "The module's own icon is back"));
      } catch (error) { note(String(error.message || error), "error"); }
    };
  }

  /* The card icon in Settings → Modules opens the picker (delegated: the cards are redrawn often). */
  document.addEventListener("click", (event) => {
    const node = event.target && event.target.closest && event.target.closest("[data-module-icon-edit]");
    if (!node) return;
    const row = rowOf(node.dataset.moduleIconEdit);
    if (row) picker(row);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const node = event.target && event.target.closest && event.target.closest("[data-module-icon-edit]");
    if (!node) return;
    event.preventDefault();
    const row = rowOf(node.dataset.moduleIconEdit);
    if (row) picker(row);
  });

  window.MemuModuleIcon = { html, png, picker, repaint, glyphSvg, emojiSvg, GRADIENTS };
})();
