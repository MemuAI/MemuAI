

(function () {
  "use strict";
  const MERMAID_URL = "/vendor/mermaid/mermaid.min.js?v=11.17.2";
  const ENGINE = "/api/pg/canvas-engine/slides/";
  const darkUi = () => {
    const m = getComputedStyle(document.body).backgroundColor.match(/\d+(\.\d+)?/g);
    if (!m) return false;
    const [r, g, b] = m.map(Number);
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 110;
  };
  const escRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  let mermaidPromise = null;
  function loadMermaid() {
    if (window.MemuDiagrams?.loadMermaid) return window.MemuDiagrams.loadMermaid();
    if (window.mermaid?.render) return Promise.resolve(window.mermaid);
    if (mermaidPromise) return mermaidPromise;
    mermaidPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = MERMAID_URL; script.async = true;
      script.onload = () => (window.mermaid ? resolve(window.mermaid) : reject(new Error("mermaid")));
      script.onerror = () => { mermaidPromise = null; reject(new Error("mermaid")); };
      document.head.appendChild(script);
    });
    return mermaidPromise;
  }
  let queue = Promise.resolve();
  let seq = 0;

  function renderMermaid(code, dark) {
    const job = queue.then(async () => {
      const mermaid = await loadMermaid();
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default",
        fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif", flowchart: { htmlLabels: false, curve: "basis" },
        themeVariables: dark ? { background: "transparent" } : {} });
      await mermaid.parse(code);
      const out = await mermaid.render("cvmm" + (++seq), code);
      return out.svg;
    });
    queue = job.catch(() => {});
    return job;
  }
  const isFlow = (code) => /^\s*(?:%%[^\n]*\n\s*)*(flowchart|graph)\b/i.test(code);
  const nodeIdOf = (g) => ((g.id || "").match(/(?:^|-)flowchart-(.+)-\d+$/) || [])[1] || "";
  const CLOSE = { "(((": [")))"], "((": ["))"], "[[": ["]]"], "[(": [")]"], "([": ["])"], "[/": ["/]", "\\]"], "[\\": ["\\]", "/]"],
    "{{": ["}}"], "[": ["]"], "(": [")"], "{": ["}"], ">": ["]"] };

  function renameNode(code, id, label) {
    const safe = `"${String(label).replace(/"/g, "#quot;")}"`;
    const re = new RegExp(`(^|[^\\w-])${escRe(id)}(\\s*)(\\(\\(\\(|\\(\\(|\\[\\[|\\[\\(|\\(\\[|\\[\\/|\\[\\\\|\\{\\{|\\[|\\(|\\{|>)`, "m");
    const m = re.exec(code);
    if (m) {
      const start = m.index + m[0].length;
      let end = -1;
      for (const close of CLOSE[m[3]] || ["]"]) { const at = code.indexOf(close, start); if (at >= 0 && (end < 0 || at < end)) end = at; }
      if (end >= 0) return code.slice(0, start) + safe + code.slice(end);
    }
    return code.replace(/\s*$/, "") + `\n    ${id}[${safe}]\n`;
  }
  const addLine = (code, line) => code.replace(/\s*$/, "") + `\n    ${line}\n`;
  function freeId(code) {
    let n = 1;
    while (new RegExp(`(^|[^\\w-])N${n}([^\\w-]|$)`, "m").test(code)) n += 1;
    return "N" + n;
  }

  async function diagramView(host, ctx) {
    const { L, esc, ico } = ctx;
    const code = String(ctx.text || "").trim();
    if (!code) { host.innerHTML = `<div class="cv-empty">${esc(L("Схема пуста", "The diagram is empty"))}</div>`; return; }
    let svg;
    try { svg = await renderMermaid(code, darkUi()); }
    catch (error) {
      const msg = String(error?.message || error || "Mermaid error");
      const line = +((msg.match(/line (\d+)/i) || [])[1] || 0);
      ctx.onError(msg.split("\n").filter(Boolean).slice(0, 3).join(" — "), line);
      host.innerHTML = `<div class="cv-kind-fail">${ico("warn")}<span>${esc(L("Mermaid не смог нарисовать схему — исправьте код или нажмите «Исправить с ИИ»", "Mermaid could not draw the diagram — fix the code or press «Fix with AI»"))}</span></div>`;
      return;
    }
    if (!host.isConnected) return;
    const flow = isFlow(code) && ctx.editable;
    host.innerHTML = `${flow ? `<div class="cv-dg-bar"><span class="cv-dg-hint">${esc(L("Перетащите блок на другой — связь · двойной щелчок — переименовать", "Drag a block onto another — a link · double-click — rename"))}</span>
        <span class="cv-grow"></span><button type="button" class="cv-chip cv-chip-s" data-dg="add">${ico("plus")}${esc(L("Блок", "Block"))}</button></div>` : ""}
      <div class="cv-dg-stage">${svg}</div>`;
    const svgEl = host.querySelector(".cv-dg-stage svg");
    if (!svgEl) return;
    svgEl.removeAttribute("height");
    if (!flow) return;
    let selected = "";
    const nodes = [...svgEl.querySelectorAll("g.node")].filter((g) => nodeIdOf(g));
    nodes.forEach((g) => { g.classList.add("cv-dg-node"); g.setAttribute("tabindex", "0"); });
    const select = (id) => { selected = id; nodes.forEach((g) => g.classList.toggle("is-sel", nodeIdOf(g) === id)); };
    let drag = null;
    svgEl.addEventListener("pointerdown", (event) => {
      const g = event.target.closest("g.cv-dg-node");
      if (!g || event.button !== 0) return;
      select(nodeIdOf(g));
      const ctm = svgEl.getScreenCTM();
      drag = { g, x: event.clientX, y: event.clientY, base: g.getAttribute("transform") || "", k: ctm ? ctm.a : 1, moved: false, target: null };
      svgEl.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    svgEl.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const dx = (event.clientX - drag.x) / drag.k, dy = (event.clientY - drag.y) / drag.k;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true;
      drag.g.classList.add("is-drag");
      drag.g.setAttribute("transform", `${drag.base} translate(${dx.toFixed(1)} ${dy.toFixed(1)})`);
      drag.g.style.pointerEvents = "none";
      const under = document.elementFromPoint(event.clientX, event.clientY)?.closest?.("g.cv-dg-node");
      const target = under && under !== drag.g ? under : null;
      if (target !== drag.target) { drag.target?.classList.remove("is-target"); target?.classList.add("is-target"); drag.target = target; }
    });
    const finish = async () => {
      if (!drag) return;
      const { g, target, moved, base } = drag;
      drag = null;
      g.style.pointerEvents = "";
      g.classList.remove("is-drag");
      target?.classList.remove("is-target");
      if (!moved) return;
      if (target) {
        const a = nodeIdOf(g), b = nodeIdOf(target);
        if (await ctx.save(addLine(code, `${a} --> ${b}`), L(`Связь ${a} → ${b}`, `Link ${a} → ${b}`))) return;
      }
      g.setAttribute("transform", base);
    };
    svgEl.addEventListener("pointerup", finish);
    svgEl.addEventListener("pointercancel", finish);
    svgEl.addEventListener("dblclick", (event) => {

      const g = event.target.closest?.("g.cv-dg-node") || nodes.find((n) => nodeIdOf(n) === selected);
      if (!g) return;
      const id = nodeIdOf(g);
      const label = window.prompt(L("Текст блока:", "Block text:"), (g.textContent || "").trim());
      if (label == null || !label.trim()) return;
      ctx.save(renameNode(code, id, label.trim()), L("Блок переименован", "Block renamed"));
    });
    svgEl.addEventListener("keydown", (event) => {
      const g = event.target.closest?.("g.cv-dg-node");
      if (g && (event.key === "Enter" || event.key === "F2")) { event.preventDefault(); g.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); }
    });
    host.querySelector('[data-dg="add"]')?.addEventListener("click", () => {
      const label = window.prompt(L("Текст нового блока:", "New block text:"), L("Новый блок", "New block"));
      if (label == null || !label.trim()) return;
      const id = freeId(code);
      let next = addLine(code, `${id}["${label.trim().replace(/"/g, "#quot;")}"]`);
      if (selected) next = addLine(next, `${selected} --> ${id}`);
      ctx.save(next, L("Блок добавлен", "Block added"));
    });
  }

  async function svgToPng(svgText) {
    const img = new Image();
    const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgText);
    await new Promise((ok, fail) => { img.onload = ok; img.onerror = () => fail(new Error("svg")); img.src = url; });
    const k = 2;
    const w = img.naturalWidth || 1200, h = img.naturalHeight || 800;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(w * k); canvas.height = Math.round(h * k);
    const g = canvas.getContext("2d");
    g.fillStyle = "#ffffff"; g.fillRect(0, 0, canvas.width, canvas.height);
    g.drawImage(img, 0, 0, canvas.width, canvas.height);
    return await new Promise((ok) => canvas.toBlob(ok, "image/png"));
  }
  async function diagramExport(text, fmt) {
    let svg = await renderMermaid(String(text || "").trim(), false);

    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    const root = doc.documentElement;
    const vb = (root.getAttribute("viewBox") || "").split(/\s+/).map(Number);
    if (vb.length === 4) { root.setAttribute("width", String(Math.ceil(vb[2]))); root.setAttribute("height", String(Math.ceil(vb[3]))); }
    root.style.removeProperty("max-width");
    svg = new XMLSerializer().serializeToString(root);
    if (fmt === "svg") return new Blob([svg], { type: "image/svg+xml" });
    return svgToPng(svg);
  }

  let enginePromise = null;
  function engine() {
    if (!enginePromise) {
      enginePromise = Promise.all([import(ENGINE + "pptx.js"), import(ENGINE + "render.js"), import(ENGINE + "templates.js"),
        fetch(ENGINE + "render.css").then((r) => (r.ok ? r.text() : ""))])
        .then(([P, R, T, css]) => ({ P, R, T, css }))
        .catch((error) => { enginePromise = null; throw error; });
    }
    return enginePromise;
  }

  function parseSlides(text) {
    let src = String(text || "").replace(/\r/g, "");
    let theme = "";
    const tm = src.match(/^\s*theme:\s*([\w-]+)[ \t]*(\n|$)/i);
    if (tm) { theme = tm[1].toLowerCase(); src = src.slice(tm[0].length); }
    const chunks = src.split(/^[ \t]*-{3,}[ \t]*$/m).map((c) => c.replace(/^\n+|\s+$/g, "")).filter((c) => c.trim());
    const slides = chunks.map((chunk, i) => {
      const spec = { bullets: [] };
      const notes = [], table = [];
      let inNotes = false;
      for (const raw of chunk.split("\n")) {
        const line = raw.replace(/\s+$/, "");
        let m;
        if (inNotes) { if (line.trim()) notes.push(line.trim()); continue; }
        if ((m = line.match(/^#\s+(.*)$/))) { spec.title = m[1].trim(); spec.layout = i === 0 ? "title" : "section"; }
        else if ((m = line.match(/^#{2,6}\s+(.*)$/))) { spec.title = m[1].trim(); }
        else if ((m = line.match(/^(?:notes?|заметки)\s*:\s*(.*)$/i))) { inNotes = true; if (m[1].trim()) notes.push(m[1].trim()); }
        else if (/^\s*\|.*\|\s*$/.test(line)) { if (!/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line)) table.push(line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim())); }
        else if ((m = line.match(/^(\s*)(?:[-*•]|\d+[.)])\s+(.*)$/))) { spec.bullets.push("  ".repeat(Math.min(4, Math.floor(m[1].replace(/\t/g, "  ").length / 2))) + m[2].trim()); }
        else if (line.trim()) {
          if ((spec.layout === "title" || spec.layout === "section") && !spec.subtitle) spec.subtitle = line.trim().replace(/^\*+|\*+$/g, "");
          else spec.bullets.push(line.trim());
        }
      }
      if (table.length) { spec.table = table; spec.layout = "table"; }
      if (!spec.layout) spec.layout = spec.bullets.length > 1 || !spec.title ? "content" : "statement";
      spec.notes = notes.join("\n");
      spec.bullets = spec.bullets.map((b) => b.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1"));
      return spec;
    });
    return { theme, slides };
  }

  function linesToParas(lines) {
    const out = [];
    for (const raw of lines) {
      const m = /^(\s*)(.*)$/.exec(String(raw ?? ""));
      const text = m[2].trim();
      if (!text) continue;
      const lvl = Math.min(4, Math.floor(m[1].length / 2));
      out.push(lvl ? { lvl, runs: [{ t: text }] } : { runs: [{ t: text }] });
    }
    return out.length ? out : [{ runs: [{ t: "" }] }];
  }
  function layoutFor(deck, kind) {
    const by = (type) => deck.layouts.find((l) => l.type === type);
    const want = { title: ["title"], section: ["secHead", "title"], content: ["obj", "tx"], statement: ["tx", "titleOnly", "title"], table: ["titleOnly", "obj"] }[kind] || ["obj"];
    for (const w of want) { const l = by(w); if (l) return l; }
    return deck.layouts.find((l) => l.placeholders.some((p) => p.type === "title")) || deck.layouts[0];
  }
  function slideFromSpec(E, deck, spec) {
    const kind = spec.layout;
    const s = E.P.makeSlide(deck, layoutFor(deck, kind).path);
    const ph = (types) => s.elements.find((e) => e.ph && types.includes(e.ph.type || "body"));
    const bodies = s.elements.filter((e) => e.ph && ["body", "obj", ""].includes(e.ph.type || ""));
    const drop = (list) => { s.elements = s.elements.filter((e) => !list.includes(e)); };
    const title = ph(["title", "ctrTitle"]);
    if (title) title.text.paras = linesToParas([spec.title || ""]);
    const sub = ph(["subTitle"]);
    if (kind === "title" || kind === "section") {
      const target = sub || bodies[0];
      if (target) target.text.paras = linesToParas([spec.subtitle || spec.bullets.join(" · ")]);
      drop(bodies.filter((b) => b !== target));
    } else if (kind === "table") {
      const rows = spec.table.slice(0, 12).map((r) => r.slice(0, 8));
      const cols = Math.max(...rows.map((r) => r.length));
      const w = deck.w * 0.84, rh = 370840;
      s.elements.push({ id: E.P.uid(), kind: "table", name: "Table", rot: 0, x: Math.round(deck.w * 0.08), y: Math.round(deck.h * 0.28), w: Math.round(w), h: rh * rows.length,
        table: { cols: Array.from({ length: cols }, () => Math.round(w / cols)), firstRow: true, bandRow: true, style: "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}",
          rows: rows.map((r) => ({ h: rh, cells: Array.from({ length: cols }, (_, j) => ({ text: { paras: [{ runs: [{ t: r[j] ?? "" }] }] } })) })) } });
      drop(bodies);
    } else {
      if (bodies[0]) {
        if (spec.bullets.length) bodies[0].text.paras = linesToParas(spec.bullets);
        else drop([bodies[0]]);
      }
      drop(bodies.slice(1));
    }
    s.notes = spec.notes || "";
    return s;
  }
  async function buildDeck(text, lang, title) {
    const E = await engine();
    const parsed = parseSlides(text);
    const theme = E.T.THEMES.find((t) => t.id === parsed.theme) || E.T.themeById("clean");
    const deck = await E.P.newDeck({ theme, size: "16:9", lang: lang === "en" ? "en" : "ru", title: title || "" });
    deck.slides = parsed.slides.map((spec) => slideFromSpec(E, deck, spec));
    return { E, deck, parsed, theme };
  }

  async function slidesView(host, ctx) {
    const { L, esc } = ctx;
    if (!String(ctx.text || "").trim()) { host.innerHTML = `<div class="cv-empty">${esc(L("Слайдов пока нет", "No slides yet"))}</div>`; return; }
    let built;
    try { built = await buildDeck(ctx.text, ctx.lang, ctx.art?.title); }
    catch (error) {
      ctx.onError(L("Движок слайдов MemuNote недоступен: ", "The MemuNote slides engine is unavailable: ") + (error?.message || error), 0);
      host.innerHTML = `<div class="cv-empty">${esc(L("Не удалось загрузить движок слайдов MemuNote", "Could not load the MemuNote slides engine"))}</div>`;
      return;
    }
    if (!host.isConnected) return;
    const { E, deck, parsed, theme } = built;
    if (!parsed.slides.length) { host.innerHTML = `<div class="cv-empty">${esc(L("Слайдов пока нет", "No slides yet"))}</div>`; return; }
    host.innerHTML = ctx.editable ? `<div class="cv-sl-bar"><span class="cv-dg-hint">${esc(L(`${parsed.slides.length} слайдов · 16:9 · движок MemuNote`, `${parsed.slides.length} slides · 16:9 · MemuNote engine`))}</span>
      <span class="cv-grow"></span><label class="cv-sl-theme"><span>${esc(L("Тема", "Theme"))}</span><select data-sl="theme">${E.T.THEMES.map((t) => `<option value="${esc(t.id)}"${t.id === theme.id ? " selected" : ""}>${esc(ctx.lang === "en" ? t.en : t.ru)}</option>`).join("")}</select></label></div>` : "";
    const stage = document.createElement("div");
    stage.className = "cv-sl-stage";
    host.appendChild(stage);
    const root = stage.attachShadow({ mode: "open" });
    const draw = () => {
      const width = Math.max(200, Math.floor(stage.clientWidth - 2));
      const scale = width / deck.w;
      root.innerHTML = `<style>${E.css}
        :host{display:block}.list{display:flex;flex-direction:column;gap:14px}
        figure{margin:0;position:relative;border-radius:8px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.12),0 0 0 1px rgba(127,127,127,.18)}
        figcaption{position:absolute;right:8px;bottom:6px;font:600 11px/1 system-ui,sans-serif;color:rgba(127,127,127,.9);background:rgba(127,127,127,.12);padding:3px 6px;border-radius:6px}</style><div class="list"></div>`;
      const list = root.querySelector(".list");
      deck.slides.forEach((slide, i) => {
        const fig = document.createElement("figure");
        const node = E.R.renderSlide(deck, slide, { scale, slideNo: i + 1 });
        fig.appendChild(node);
        const cap = document.createElement("figcaption");
        cap.textContent = String(i + 1);
        fig.appendChild(cap);
        list.appendChild(fig);
        try { E.R.fitTexts(node); } catch (e) {  }
      });
      stage.dataset.w = String(width);
    };
    draw();

    let frame = 0;
    const ro = new ResizeObserver(() => {
      if (!stage.isConnected) { ro.disconnect(); return; }
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => { if (Math.abs(stage.clientWidth - 2 - (+stage.dataset.w || 0)) > 8) draw(); });
    });
    ro.observe(stage);
    host.querySelector('[data-sl="theme"]')?.addEventListener("change", (event) => {
      const id = event.target.value;
      const body = String(ctx.text || "").replace(/^\s*theme:\s*[\w-]+[ \t]*(\n|$)/i, "");
      ctx.save(`theme: ${id}\n${body.replace(/^\n+/, "")}`, L("Тема изменена", "Theme changed"));
    });
  }
  async function slidesExport(text, fmt, opts = {}) {
    if (fmt !== "pptx") throw new Error(fmt);
    const { E, deck } = await buildDeck(text, opts.lang, opts.title);
    const bytes = await E.P.savePptx(deck);
    return new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" });
  }

  window.MemuCanvasKinds = {
    diagram: { view: diagramView, export: diagramExport },
    slides: { view: slidesView, export: slidesExport },
    _parseSlides: parseSlides, _renameNode: renameNode,
  };
})();
