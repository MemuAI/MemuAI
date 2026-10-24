/* MemuAI documentation viewer: Settings → Documentation.
 * Pages are Markdown served by app/docs.py. This file renders them, builds the
 * navigation, the in-page table of contents and search, and keeps a small
 * back/forward history. It relies on app.js helpers (icon, esc, toast,
 * showContextMenu, copyText, highlightSyntax) that are loaded before it runs.
 */
"use strict";
const MemuDocs = (() => {
  let catalog = null;           // { lang, groups, home }
  let current = null;           // the page object from /api/pg/docs/page
  let pendingAnchor = "";
  let back = [];
  let forward = [];
  let query = "";
  let results = [];
  let cursor = -1;
  let searchTimer = 0;
  let spyFrame = 0;
  let goSeq = 0;
  const scrollMemory = new Map();
  const collapsed = new Set();

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const lang = () => (en() ? "en" : "ru");
  const L = (ru, english) => (en() ? english : ru);
  const $ = (selector, node) => (node || document).querySelector(selector);
  const $$ = (selector, node) => Array.from((node || document).querySelectorAll(selector));
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const glyph = (name) => (typeof icon === "function" ? icon(name) : "");
  const note = (text, type = "ok") => { try { toast(text, type); } catch (_) {} };

  async function getJson(path) {
    const response = await fetch(path);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  /* -------------------------------- markdown -------------------------------- */

  /** Same rule as app/docs.py anchor(): letters and digits joined by "-". */
  function anchorOf(text) {
    const slug = String(text || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "");
    return slug.slice(0, 80) || "section";
  }

  // "++Ctrl+Shift+T++": the keys themselves contain "+", so the content only
  // has to start and end with a key character (app/docs.py uses the same rule).
  const KEYS = /\+\+([^\s+](?:[^\n]*?[^\s+])??)\+\+/g;

  function plainText(text) {
    return String(text || "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(KEYS, "$1")
      .replace(/`([^`]*)`/g, "$1")
      .replace(/(\*\*|__|\*|~~)/g, "")
      .trim();
  }

  function inline(text) {
    const codes = [];
    let value = String(text || "").replace(/`([^`\n]+)`/g, (_, code) => `\u0000${codes.push(code) - 1}\u0000`);
    value = escape(value);
    value = value.replace(KEYS, (_, keys) => keys.split("+").map((key) => `<kbd>${key.trim()}</kbd>`).join("+"));
    value = value.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    value = value.replace(/(^|[\s(«"])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    value = value.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    value = value.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, href) => {
      const target = href.replace(/&amp;/g, "&");
      if (/^doc:/i.test(target)) {
        const [page, hash] = target.slice(4).split("#");
        return `<a href="#" class="docs-link" data-doc-page="${escape(page || (current && current.id) || "")}" data-doc-anchor="${escape(hash || "")}">${label}</a>`;
      }
      if (target.startsWith("#")) {
        return `<a href="#" class="docs-link" data-doc-page="${escape((current && current.id) || "")}" data-doc-anchor="${escape(target.slice(1))}">${label}</a>`;
      }
      if (/^https?:\/\//i.test(target)) return `<a href="${escape(target)}" target="_blank" rel="noreferrer" class="docs-external">${label}</a>`;
      return label;
    });
    return value.replace(/\u0000(\d+)\u0000/g, (_, index) => `<code>${escape(codes[+index])}</code>`);
  }

  function codeBlock(source, language) {
    const lang = (language || "").toLowerCase() || "text";
    // Round 29: ```memu-widget fences become live cards (modules-aipack.js, e.g. the modules AI pack).
    if (lang === "memu-widget" && window.MemuModuleTools) { const html = MemuModuleTools.widget(source); if (html) return html; }
    let highlighted;
    try { highlighted = typeof highlightSyntax === "function" ? highlightSyntax(source, lang) : escape(source); }
    catch (_) { highlighted = escape(source); }
    return `<div class="code-block-wrap docs-code" data-lang="${escape(lang)}"><div class="code-header"><span>${escape(lang.toUpperCase())}</span>`
      + `<button class="code-copy-btn" data-copy-code aria-label="${L("Копировать", "Copy")}">${glyph("copy")} <span>${L("Копировать", "Copy")}</span></button></div>`
      + `<pre><code class="language-${escape(lang)}">${highlighted}</code></pre></div>`;
  }

  const CALLOUTS = {
    NOTE: ["note", "info", "Примечание", "Note"],
    TIP: ["tip", "sparkle", "Совет", "Tip"],
    WARNING: ["warning", "bell", "Внимание", "Warning"],
    IMPORTANT: ["important", "target", "Важно", "Important"],
    DANGER: ["danger", "trash", "Осторожно", "Careful"],
  };

  function render(markdown) {
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    const seen = new Map();
    let index = 0;

    function heading(level, text) {
      const plain = plainText(text);
      let id = anchorOf(plain);
      const count = (seen.get(id) || 0) + 1;
      seen.set(id, count);
      if (count > 1) id = `${id}-${count}`;
      // DOM-id с приставкой: заголовок «Messages» давал id="messages" и
      // перехватывал $("#messages") у самого приложения (как и #context-menu).
      if (level === 1) return `<h1 id="doc-${id}">${inline(text)}</h1>`;
      return `<h${level} id="doc-${id}" class="docs-heading">${inline(text)}<button type="button" class="docs-anchor" data-copy-anchor="${id}" title="${L("Копировать ссылку на раздел", "Copy a link to this section")}" aria-label="${L("Ссылка на раздел", "Section link")}">#</button></h${level}>`;
    }

    function list(startIndex) {
      // Items keep their indentation; deeper items become a nested list.
      const baseIndent = lines[startIndex].match(/^\s*/)[0].length;
      const ordered = /^\s*\d+[.)]\s+/.test(lines[startIndex]);
      const start = ordered ? Number(lines[startIndex].match(/\d+/)[0]) : 1;
      const items = [];
      let i = startIndex;
      while (i < lines.length) {
        const line = lines[i];
        const indent = line.match(/^\s*/)[0].length;
        const marker = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.*)$/);
        if (marker && indent === baseIndent) {
          const task = marker[1].match(/^\[([ xX])\]\s+(.*)$/);
          items.push({ text: task ? task[2] : marker[1], task: task ? task[1].toLowerCase() === "x" : null, children: "" });
          i += 1;
        } else if (marker && indent > baseIndent && items.length) {
          const nested = list(i);
          items[items.length - 1].children += nested.html;
          i = nested.next;
        } else if (line.trim() && indent > baseIndent && items.length && !marker) {
          items[items.length - 1].text += " " + line.trim();
          i += 1;
        } else if (!line.trim() && i + 1 < lines.length && /^\s*(?:[-*+]|\d+[.)])\s+/.test(lines[i + 1])
          && lines[i + 1].match(/^\s*/)[0].length >= baseIndent) {
          i += 1;
        } else break;
      }
      const tag = ordered ? "ol" : "ul";
      const body = items.map((item) => item.task === null
        ? `<li>${inline(item.text)}${item.children}</li>`
        : `<li class="docs-task ${item.task ? "done" : ""}"><span class="docs-check" aria-hidden="true">${item.task ? "✓" : ""}</span>${inline(item.text)}${item.children}</li>`).join("");
      return { html: `<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>${body}</${tag}>`, next: i };
    }

    while (index < lines.length) {
      const line = lines[index];
      const fence = line.match(/^\s*```\s*([\w.+-]*)\s*$/);
      if (fence) {
        const code = [];
        index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) { code.push(lines[index]); index += 1; }
        index += 1;
        out.push(codeBlock(code.join("\n"), fence[1]));
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[index + 1] || "")) {
        const cells = (row) => row.trim().slice(1, -1).split(/(?<!\\)\|/).map((cell) => cell.replace(/\\\|/g, "|").trim());
        const aligns = cells(lines[index + 1]).map((cell) => (/^:-+:$/.test(cell) ? "center" : /-+:$/.test(cell) ? "right" : ""));
        const head = cells(line);
        index += 2;
        const rows = [];
        while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) { rows.push(cells(lines[index])); index += 1; }
        const cell = (tag, text, column) => `<${tag}${aligns[column] ? ` style="text-align:${aligns[column]}"` : ""}>${inline(text)}</${tag}>`;
        out.push(`<div class="docs-table"><table><thead><tr>${head.map((text, column) => cell("th", text, column)).join("")}</tr></thead>`
          + `<tbody>${rows.map((row) => `<tr>${row.map((text, column) => cell("td", text, column)).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      // Round 29: a screenshot on its own line — ![alt](docs-img/ru/x.webp "caption") (docs-ai.js: lazy, click to zoom).
      const picture = line.match(/^\s*!\[([^\]]*)\]\((docs-img\/(?:ru|en)\/[a-z0-9][a-z0-9._-]*\.(?:webp|png|jpe?g))(?:\s+"([^"]*)")?\)\s*$/i);
      if (picture) { out.push(window.MemuDocsAI ? MemuDocsAI.figure(picture[2], picture[1], picture[3] || "") : ""); index += 1; continue; }
      const title = line.match(/^(#{1,4})\s+(.+?)\s*#*$/);
      if (title) { out.push(heading(title[1].length, title[2])); index += 1; continue; }
      if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { out.push("<hr>"); index += 1; continue; }
      if (/^\s*(?:[-*+]|\d+[.)])\s+/.test(line)) {
        const block = list(index);
        out.push(block.html);
        index = block.next;
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quoted = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) { quoted.push(lines[index].replace(/^\s*>\s?/, "")); index += 1; }
        const kind = (quoted[0] || "").match(/^\[!(NOTE|TIP|WARNING|IMPORTANT|DANGER)\]\s*(.*)$/i);
        if (kind) {
          const [cls, ico, ru, english] = CALLOUTS[kind[1].toUpperCase()];
          const label = kind[2] || L(ru, english);
          out.push(`<aside class="docs-callout ${cls}"><div class="docs-callout-title">${glyph(ico)}<b>${inline(label)}</b></div>${render(quoted.slice(1).join("\n"))}</aside>`);
        } else out.push(`<blockquote>${render(quoted.join("\n"))}</blockquote>`);
        continue;
      }
      if (!line.trim()) { index += 1; continue; }
      const paragraph = [line.trim()];
      index += 1;
      while (index < lines.length && lines[index].trim()
        && !/^(#{1,4}\s|\s*(?:[-*+]|\d+[.)])\s|\s*>|\s*\||\s*```|\s*(---|\*\*\*)\s*$|\s*!\[)/.test(lines[index])) {
        paragraph.push(lines[index].trim());
        index += 1;
      }
      out.push(`<p>${inline(paragraph.join(" "))}</p>`);
    }
    return out.join("");
  }

  /* --------------------------------- layout --------------------------------- */

  function shell() {
    return `<div class="docs-shell">
      <aside class="docs-side" aria-label="${L("Разделы документации", "Documentation sections")}">
        <label class="docs-search">${glyph("search")}
          <input type="search" id="docs-query" autocomplete="off" spellcheck="false"
            placeholder="${L("Поиск статей", "Search docs")}" title="${L("Поиск по документации", "Search the documentation")}" aria-label="${L("Поиск по документации", "Search the documentation")}">
          <kbd title="${L("Нажмите / чтобы начать поиск", "Press / to search")}">/</kbd>
        </label>
        <nav class="docs-tree" id="docs-tree"></nav>
      </aside>
      <div class="docs-main" id="docs-main">
        <div class="docs-topbar">
          <button type="button" class="iconbtn docs-nav-btn" id="docs-back" title="${L("Назад (Alt+←)", "Back (Alt+←)")}" aria-label="${L("Назад", "Back")}">${glyph("back")}</button>
          <button type="button" class="iconbtn docs-nav-btn" id="docs-forward" title="${L("Вперёд (Alt+→)", "Forward (Alt+→)")}" aria-label="${L("Вперёд", "Forward")}">${glyph("forward")}</button>
          <div class="docs-crumbs" id="docs-crumbs"></div>
          <div class="docs-find" id="docs-find" hidden role="search">
            ${glyph("search")}
            <input type="text" id="docs-find-input" autocomplete="off" spellcheck="false"
              placeholder="${L("Найти на странице", "Find on this page")}" aria-label="${L("Найти на странице", "Find on this page")}">
            <span class="docs-find-count" id="docs-find-count" aria-live="polite"></span>
            <button type="button" class="iconbtn" id="docs-find-prev" title="${L("Предыдущее (Shift+Enter)", "Previous (Shift+Enter)")}" aria-label="${L("Предыдущее совпадение", "Previous match")}">${glyph("back")}</button>
            <button type="button" class="iconbtn" id="docs-find-next" title="${L("Следующее (Enter)", "Next (Enter)")}" aria-label="${L("Следующее совпадение", "Next match")}">${glyph("forward")}</button>
            <button type="button" class="iconbtn" id="docs-find-close" title="${L("Закрыть (Esc)", "Close (Esc)")}" aria-label="${L("Закрыть поиск", "Close find")}">${glyph("close")}</button>
          </div>
          <button type="button" class="iconbtn docs-nav-btn" id="docs-find-open" title="${L("Найти на странице (Ctrl+F)", "Find on this page (Ctrl+F)")}" aria-label="${L("Найти на странице", "Find on this page")}">${glyph("search")}</button>
          <button type="button" class="iconbtn docs-nav-btn" id="docs-more" title="${L("Действия со страницей", "Page actions")}" aria-label="${L("Действия со страницей", "Page actions")}">${glyph("more")}</button>
        </div>
        <div class="docs-scroll" id="docs-scroll">
          <article class="docs-article" id="docs-article" tabindex="-1"></article>
          <nav class="docs-pager" id="docs-pager"></nav>
        </div>
      </div>
      <aside class="docs-toc" id="docs-toc" aria-label="${L("На этой странице", "On this page")}"></aside>
    </div>`;
  }

  function drawTree() {
    const tree = $("#docs-tree");
    if (!tree || !catalog) return;
    if (query.trim().length >= 2) return drawResults();
    tree.innerHTML = catalog.groups.map((group) => `
      <section class="docs-group ${collapsed.has(group.id) ? "collapsed" : ""}" data-group="${escape(group.id)}">
        <button type="button" class="docs-group-head" data-toggle-group="${escape(group.id)}" aria-expanded="${!collapsed.has(group.id)}">
          ${glyph(group.icon || "info")}<span>${escape(group.title)}</span><i class="docs-chevron" aria-hidden="true"></i>
        </button>
        <div class="docs-group-pages">${group.pages.map((page) => `
          <a href="#" class="docs-page-link ${current && current.id === page.id ? "active" : ""}" data-doc-page="${escape(page.id)}"
             title="${escape(page.summary || page.title)}"${current && current.id === page.id ? ' aria-current="page"' : ""}>
            ${escape(page.title)}${page.fallback ? `<small>${en() ? "RU" : "EN"}</small>` : ""}</a>`).join("")}
        </div>
      </section>`).join("");
  }

  function highlight(text, needle) {
    // Сначала режем исходный текст, потом экранируем куски: иначе поиск
    // «amp» или «quot» подсвечивал внутренности &amp; и ломал сущности.
    const words = needle.split(/\s+/).filter((word) => word.length > 1)
      .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!words.length) return escape(text);
    return String(text || "").split(new RegExp(`(${words.join("|")})`, "gi"))
      .map((part, index) => (index % 2 ? `<mark>${escape(part)}</mark>` : escape(part))).join("");
  }

  function drawResults() {
    const tree = $("#docs-tree");
    if (!tree) return;
    const needle = query.trim();
    if (!results.length) {
      tree.innerHTML = `<div class="docs-empty">${L("Ничего не найдено. Попробуйте другое слово или короче.", "Nothing found. Try another or a shorter word.")}</div>`;
      return;
    }
    tree.innerHTML = `<div class="docs-results-head">${L("Результаты", "Results")} · ${results.length}</div>` + results.map((row, index) => `
      <a href="#" class="docs-result ${index === cursor ? "active" : ""}" data-doc-page="${escape(row.id)}" data-doc-anchor="${escape(row.anchor || "")}" data-result="${index}">
        <b>${highlight(row.title, needle)}</b>
        ${row.heading ? `<span class="docs-result-heading">${highlight(row.heading, needle)}</span>` : ""}
        <span class="docs-result-snippet">${highlight(row.snippet || "", needle)}</span>
      </a>`).join("");
  }

  function drawToc() {
    const toc = $("#docs-toc");
    if (!toc || !current) return;
    const rows = (current.headings || []).filter((row) => row.level === 2 || row.level === 3);
    toc.innerHTML = rows.length < 2 ? "" : `<div class="docs-toc-title">${L("На этой странице", "On this page")}</div>`
      + rows.map((row) => `<a href="#" class="docs-toc-link level-${row.level}" data-doc-page="${escape(current.id)}" data-doc-anchor="${escape(row.anchor)}">${escape(row.text)}</a>`).join("");
    spy();
  }

  function drawCrumbs() {
    const crumbs = $("#docs-crumbs");
    if (!crumbs || !current) return;
    crumbs.innerHTML = `<span>${escape(current.group_title || "")}</span><i aria-hidden="true">/</i><b>${escape(current.title)}</b>`;
    $("#docs-back").disabled = !back.length;
    $("#docs-forward").disabled = !forward.length;
  }

  function drawPager() {
    const pager = $("#docs-pager");
    if (!pager || !current) return;
    const card = (row, dir) => row ? `<a href="#" class="docs-pager-card ${dir}" data-doc-page="${escape(row.id)}">
      <small>${dir === "prev" ? L("Назад", "Previous") : L("Дальше", "Next")}</small><b>${escape(row.title)}</b></a>` : "<span></span>";
    pager.innerHTML = card(current.prev, "prev") + card(current.next, "next");
  }

  function drawArticle() {
    const article = $("#docs-article");
    if (!article || !current) return;
    const fallback = current.fallback ? `<aside class="docs-callout note"><div class="docs-callout-title">${glyph("info")}<b>${L("Страница пока только на английском", "This page is only in Russian so far")}</b></div></aside>` : "";
    article.innerHTML = fallback + render(current.body);
    article.lang = current.lang;
    if (window.MemuModuleTools) MemuModuleTools.hydrate(article);   // Round 29: memu-widget cards
    if (window.MemuDocsAI) MemuDocsAI.decorate(article, current);      // Round 29: «Кратко от ИИ», screenshots
  }

  /* ------------------------------- navigation ------------------------------- */

  function scroller() { return $("#docs-scroll"); }

  function scrollToAnchor(anchor, flash) {
    const box = scroller();
    const target = anchor ? document.getElementById("doc-" + anchor) : null;
    if (!box) return;
    if (!target || !box.contains(target)) { box.scrollTop = 0; return; }
    box.scrollTop = Math.max(0, target.offsetTop - 12);
    if (flash) {
      target.classList.remove("docs-flash");
      void target.offsetWidth;
      target.classList.add("docs-flash");
    }
  }

  async function go(pageId, anchor = "", options = {}) {
    if (!catalog) await loadCatalog();
    const id = pageId || (catalog && catalog.home) || "";
    if (!id) return;
    if (current && current.id === id && !options.reload) {
      if (!options.history && anchor) pushHistory(anchor);
      scrollToAnchor(anchor, true);
      spy();
      return;
    }
    let page;
    // Быстрые клики: показываем последнюю запрошенную страницу, а не ту,
    // что ответила позже.
    const seq = (goSeq += 1);
    try {
      page = await getJson(`/api/pg/docs/page?lang=${lang()}&id=${encodeURIComponent(id)}`);
    } catch (error) {
      if (seq !== goSeq) return;
      note(L("Страница документации не открылась: ", "The documentation page did not open: ") + error.message, "err");
      return;
    }
    if (seq !== goSeq) return;
    if (current && !options.history) {
      back.push({ id: current.id, anchor: currentAnchor(), scroll: scroller() ? scroller().scrollTop : 0 });
      back = back.slice(-50);
      forward = [];
    }
    if (current && scroller()) scrollMemory.set(current.id, scroller().scrollTop);
    current = page;
    findHits = [];
    findAt = -1;
    drawArticle();
    // The find bar stays open across pages and searches the new one.
    const finder = $("#docs-find-input");
    if (finder && !$("#docs-find").hidden && finder.value) findRun(finder.value);
    drawCrumbs();
    drawPager();
    drawToc();
    if (!query.trim()) drawTree();
    else $$(".docs-page-link").forEach((link) => link.classList.toggle("active", link.dataset.docPage === id));
    revealActiveLink();
    requestAnimationFrame(() => {
      if (options.scroll !== undefined && scroller()) scroller().scrollTop = options.scroll;
      else scrollToAnchor(anchor, !!anchor);
      spy();
    });
    try { localStorage.setItem("memu_docs_page", id); } catch (_) {}
  }

  function pushHistory(anchor) {
    if (!current) return;
    back.push({ id: current.id, anchor: currentAnchor(), scroll: scroller() ? scroller().scrollTop : 0 });
    back = back.slice(-50);
    forward = [];
    drawCrumbs();
  }

  function currentAnchor() {
    const active = $(".docs-toc-link.active");
    return active ? active.dataset.docAnchor : "";
  }

  function historyStep(direction) {
    const from = direction < 0 ? back : forward;
    const to = direction < 0 ? forward : back;
    const entry = from.pop();
    if (!entry || !current) return;
    to.push({ id: current.id, anchor: currentAnchor(), scroll: scroller() ? scroller().scrollTop : 0 });
    go(entry.id, entry.anchor, { history: true, scroll: entry.scroll });
  }

  function revealActiveLink() {
    const link = $(".docs-page-link.active");
    const tree = $("#docs-tree");
    if (!link || !tree) return;
    const group = link.closest(".docs-group");
    if (group && group.classList.contains("collapsed")) {
      collapsed.delete(group.dataset.group);
      group.classList.remove("collapsed");
    }
    const top = link.offsetTop - tree.offsetTop;
    if (top < tree.scrollTop || top > tree.scrollTop + tree.clientHeight - 40) tree.scrollTop = top - 80;
  }

  function spy() {
    cancelAnimationFrame(spyFrame);
    spyFrame = requestAnimationFrame(() => {
      const box = scroller();
      const links = $$(".docs-toc-link");
      if (!box || !links.length) return;
      let active = links[0];
      for (const link of links) {
        const target = document.getElementById("doc-" + link.dataset.docAnchor);
        if (target && target.offsetTop - box.scrollTop <= 40) active = link;
      }
      if (box.scrollTop + box.clientHeight >= box.scrollHeight - 4) active = links[links.length - 1];
      links.forEach((link) => link.classList.toggle("active", link === active));
    });
  }

  /* --------------------------------- search --------------------------------- */

  function runSearch() {
    clearTimeout(searchTimer);
    const needle = query.trim();
    if (needle.length < 2) { results = []; cursor = -1; drawTree(); return; }
    searchTimer = setTimeout(async () => {
      try {
        const data = await getJson(`/api/pg/docs/search?lang=${lang()}&q=${encodeURIComponent(needle)}`);
        if (query.trim() !== needle) return;
        results = data.results || [];
        cursor = results.length ? 0 : -1;
        drawResults();
      } catch (_) { /* поиск не критичен */ }
    }, 140);
  }

  function openResult(index) {
    const row = results[index];
    if (!row) return;
    go(row.id, row.anchor || "").then(() => markInArticle(query.trim()));
  }

  /* Highlight the searched words in the opened page for a few seconds. */
  function markInArticle(needle) {
    const article = $("#docs-article");
    // Syntax highlighting splits "Memu.save" into several nodes, so mark the parts.
    const words = needle.toLowerCase().split(/[^\p{L}\p{N}_]+/u).filter((word) => word.length > 1);
    if (!article || !words.length) return;
    const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
    const hits = [];
    while (walker.nextNode() && hits.length < 60) {
      const node = walker.currentNode;
      if (node.parentElement.closest(".code-header, mark, .docs-anchor")) continue;
      const text = node.nodeValue.toLowerCase();
      const word = words.find((item) => text.includes(item));
      if (word) hits.push([node, text.indexOf(word), word.length]);
    }
    hits.forEach(([node, at, length]) => {
      const range = document.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + length);
      const mark = document.createElement("mark");
      mark.className = "docs-hit";
      try { range.surroundContents(mark); } catch (_) {}
    });
    setTimeout(() => $$(".docs-hit", article).forEach((mark) => mark.replaceWith(...mark.childNodes)), 4000);
  }

  /* ----------------------------- find on the page ---------------------------- */

  // Ctrl+F. WebView2 has no find bar of its own inside the app window, so the
  // page gets one: every match is marked, Enter / Shift+Enter walk through them.
  let findHits = [];
  let findAt = -1;

  function findClear() {
    $$(".docs-find-hit", $("#docs-article") || document).forEach((mark) => {
      const parent = mark.parentNode;
      mark.replaceWith(...mark.childNodes);
      if (parent) parent.normalize();
    });
    findHits = [];
    findAt = -1;
  }

  function findRun(needle) {
    findClear();
    const article = $("#docs-article");
    const count = $("#docs-find-count");
    const text = String(needle || "").toLowerCase();
    if (!article || !text) { if (count) count.textContent = ""; return; }
    const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.parentElement.closest(".code-header, .docs-anchor")) continue;
      if (node.nodeValue.toLowerCase().includes(text)) nodes.push(node);
    }
    for (const node of nodes) {
      // Walk the matches of one text node from the end, so earlier offsets stay valid.
      const value = node.nodeValue.toLowerCase();
      const starts = [];
      for (let at = value.indexOf(text); at >= 0 && starts.length < 400; at = value.indexOf(text, at + text.length)) starts.push(at);
      const marks = [];
      for (const at of starts.reverse()) {
        const range = document.createRange();
        range.setStart(node, at);
        range.setEnd(node, at + text.length);
        const mark = document.createElement("mark");
        mark.className = "docs-find-hit";
        try { range.surroundContents(mark); marks.unshift(mark); } catch (_) {}
      }
      findHits.push(...marks);
      if (findHits.length > 2000) break;
    }
    if (findHits.length) findGo(0); else if (count) count.textContent = L("нет совпадений", "no matches");
    const input = $("#docs-find-input");
    if (input) input.classList.toggle("missing", !findHits.length);
  }

  function findGo(index) {
    if (!findHits.length) return;
    findAt = (index + findHits.length) % findHits.length;
    findHits.forEach((mark, at) => mark.classList.toggle("current", at === findAt));
    const mark = findHits[findAt];
    const box = scroller();
    if (box && mark) {
      const top = mark.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
      if (top < box.scrollTop + 40 || top > box.scrollTop + box.clientHeight - 80) box.scrollTop = Math.max(0, top - box.clientHeight / 3);
    }
    const count = $("#docs-find-count");
    if (count) count.textContent = `${findAt + 1} / ${findHits.length}`;
  }

  function findOpen() {
    const bar = $("#docs-find");
    const input = $("#docs-find-input");
    if (!bar || !input) return;
    bar.hidden = false;
    const selection = String(window.getSelection ? window.getSelection().toString() : "").trim();
    if (selection && selection.length <= 80 && !selection.includes("\n")) input.value = selection;
    input.focus();
    input.select();
    if (input.value) findRun(input.value);
  }

  function findClose() {
    const bar = $("#docs-find");
    if (!bar || bar.hidden) return false;
    bar.hidden = true;
    findClear();
    const count = $("#docs-find-count");
    if (count) count.textContent = "";
    const article = $("#docs-article");
    if (article) article.focus({ preventScroll: true });
    return true;
  }

  /* ------------------------------ page actions ------------------------------ */

  function sectionPath(anchor) {
    const heading = anchor ? (current.headings || []).find((row) => row.anchor === anchor) : null;
    return `${L("Документация MemuAI", "MemuAI documentation")} → ${current.title}${heading ? " → " + heading.text : ""}`;
  }

  function copyLink(anchor) {
    if (!current) return;
    const text = `${sectionPath(anchor)} (memuai://docs/${current.id}${anchor ? "#" + anchor : ""})`;
    if (typeof copyText === "function") copyText(text);
  }

  function askMemuAI(text) {
    const selection = String(text || "").trim();
    if (!selection) return;
    const prompt = L(
      `Объясни подробнее этот фрагмент из документации MemuAI (страница «${current ? current.title : ""}»):\n\n> ${selection.replace(/\n/g, "\n> ")}`,
      `Explain this part of the MemuAI documentation in more detail (page “${current ? current.title : ""}”):\n\n> ${selection.replace(/\n/g, "\n> ")}`);
    if (typeof closePanel === "function") closePanel();
    const input = document.getElementById("input");
    if (!input) return;
    input.value = prompt;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  function pageMenu(x, y) {
    if (!current || typeof showContextMenu !== "function") return;
    showContextMenu([
      { label: L("Копировать ссылку на страницу", "Copy a link to the page"), icon: "copy", run: () => copyLink("") },
      { label: L("Копировать страницу как Markdown", "Copy the page as Markdown"), icon: "text", run: () => copyText(current.body) },
      { label: L("Сохранить страницу в файл", "Save the page to a file"), icon: "download", run: () => {
        if (typeof downloadTextFile === "function") downloadTextFile(`memuai-docs-${current.id}.md`, current.body, "text/markdown");
      } },
      { sep: true },
      { label: L("Свернуть все разделы", "Collapse all sections"), icon: "list", run: () => {
        catalog.groups.forEach((group) => { if (!current || group.id !== current.group) collapsed.add(group.id); });
        drawTree();
      } },
      { label: L("Развернуть все разделы", "Expand all sections"), icon: "list", run: () => { collapsed.clear(); drawTree(); } },
    ], x, y);
  }

  function articleMenu(event) {
    if (typeof showContextMenu !== "function" || !current) return;
    event.preventDefault();
    event.stopPropagation();
    const selection = String(window.getSelection ? window.getSelection().toString() : "").trim();
    const heading = event.target.closest("h2[id], h3[id], h4[id]");
    const code = event.target.closest(".code-block-wrap");
    const link = event.target.closest("a.docs-link, a.docs-external");
    const items = [];
    if (selection) {
      items.push({ label: L("Копировать", "Copy"), icon: "copy", run: () => copyText(selection) });
      items.push({ label: L(`Найти «${selection.slice(0, 24)}${selection.length > 24 ? "…" : ""}» в документации`, `Search docs for “${selection.slice(0, 24)}${selection.length > 24 ? "…" : ""}”`),
        icon: "search", run: () => searchFor(selection.slice(0, 80)) });
      items.push({ label: L("Спросить MemuAI об этом", "Ask MemuAI about this"), icon: "sparkle", run: () => askMemuAI(selection) });
      items.push({ sep: true });
    }
    if (code) items.push({ label: L("Копировать код", "Copy code"), icon: "copy", run: () => {
      const button = code.querySelector("[data-copy-code]");
      if (button && typeof copyCode === "function") copyCode(button);
    } });
    if (link && link.classList.contains("docs-link")) {
      items.push({ label: L("Открыть ссылку", "Open the link"), icon: "forward", run: () => go(link.dataset.docPage, link.dataset.docAnchor) });
    }
    // DOM-id заголовка — «doc-<якорь>» (см. heading()), а copyLink ждёт сам
    // якорь: без среза ссылка получала «#doc-…» и не находила раздел.
    if (heading) items.push({ label: L("Копировать ссылку на раздел", "Copy a link to this section"), icon: "copy", run: () => copyLink(heading.id.replace(/^doc-/, "")) });
    items.push({ label: L("Выделить всю страницу", "Select the whole page"), icon: "focus", run: () => {
      const range = document.createRange();
      range.selectNodeContents($("#docs-article"));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } });
    items.push({ sep: true });
    if (back.length) items.push({ label: L("Назад", "Back"), icon: "back", run: () => historyStep(-1) });
    if (forward.length) items.push({ label: L("Вперёд", "Forward"), icon: "forward", run: () => historyStep(1) });
    if (current.prev) items.push({ label: L("Предыдущая страница: ", "Previous page: ") + current.prev.title, icon: "back", run: () => go(current.prev.id) });
    if (current.next) items.push({ label: L("Следующая страница: ", "Next page: ") + current.next.title, icon: "forward", run: () => go(current.next.id) });
    showContextMenu(items, event.clientX, event.clientY);
  }

  function searchFor(text) {
    const input = $("#docs-query");
    if (!input) return;
    input.value = text;
    query = text;
    input.focus();
    runSearch();
  }

  /* --------------------------------- binding -------------------------------- */

  function bind(pane) {
    pane.addEventListener("click", (event) => {
      const toggle = event.target.closest("[data-toggle-group]");
      if (toggle) {
        const id = toggle.dataset.toggleGroup;
        if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
        const section = toggle.closest(".docs-group");
        section.classList.toggle("collapsed", collapsed.has(id));
        toggle.setAttribute("aria-expanded", String(!collapsed.has(id)));
        return;
      }
      const copyAnchor = event.target.closest("[data-copy-anchor]");
      if (copyAnchor) { event.preventDefault(); copyLink(copyAnchor.dataset.copyAnchor); return; }
      const link = event.target.closest("[data-doc-page]");
      if (link) {
        event.preventDefault();
        if (link.dataset.result !== undefined) openResult(Number(link.dataset.result));
        else go(link.dataset.docPage, link.dataset.docAnchor || "");
      }
    });
    pane.addEventListener("contextmenu", (event) => {
      if (event.target.closest("#docs-article")) articleMenu(event);
      else if (event.target.closest("#docs-query")) return;
      else if (event.target.closest(".docs-page-link")) {
        event.preventDefault();
        event.stopPropagation();
        const link = event.target.closest(".docs-page-link");
        showContextMenu([
          { label: L("Открыть", "Open"), icon: "forward", run: () => go(link.dataset.docPage) },
          { label: L("Копировать ссылку", "Copy link"), icon: "copy", run: () => copyText(`memuai://docs/${link.dataset.docPage}`) },
        ], event.clientX, event.clientY);
      }
    });
    const input = $("#docs-query", pane);
    input.addEventListener("input", () => { query = input.value; runSearch(); });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!results.length) return;
        event.preventDefault();
        cursor = (cursor + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
        drawResults();
        const active = $(".docs-result.active");
        if (active) active.scrollIntoView({ block: "nearest" });
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (cursor >= 0) openResult(cursor);
      } else if (event.key === "Escape") {
        if (input.value) { event.preventDefault(); event.stopPropagation(); input.value = ""; query = ""; runSearch(); }
      }
    });
    $("#docs-back", pane).onclick = () => historyStep(-1);
    $("#docs-forward", pane).onclick = () => historyStep(1);
    $("#docs-more", pane).onclick = (event) => {
      event.stopPropagation();
      const rect = event.currentTarget.getBoundingClientRect();
      pageMenu(rect.right - 240, rect.bottom + 4);
    };
    $("#docs-scroll", pane).addEventListener("scroll", spy, { passive: true });
    const find = $("#docs-find-input", pane);
    let findTimer = 0;
    find.addEventListener("input", () => { clearTimeout(findTimer); findTimer = setTimeout(() => findRun(find.value), 90); });
    find.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); clearTimeout(findTimer); if (!findHits.length) findRun(find.value); else findGo(findAt + (event.shiftKey ? -1 : 1)); }
      else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); findClose(); }
    });
    $("#docs-find-open", pane).onclick = findOpen;
    $("#docs-find-close", pane).onclick = findClose;
    $("#docs-find-prev", pane).onclick = () => findGo(findAt - 1);
    $("#docs-find-next", pane).onclick = () => findGo(findAt + 1);
    pane.addEventListener("keydown", (event) => {
      const typing = event.target.closest("input, textarea, select");
      const key = String(event.key || "").toLowerCase();
      // Ctrl+F (и Ctrl+А на русской раскладке): поиск по открытой странице.
      if ((event.ctrlKey || event.metaKey) && !event.altKey && (key === "f" || key === "а" || event.code === "KeyF")) {
        event.preventDefault(); event.stopPropagation(); findOpen(); return;
      }
      if (event.key === "F3" && findHits.length) { event.preventDefault(); findGo(findAt + (event.shiftKey ? -1 : 1)); return; }
      if (event.key === "Escape" && !typing && findClose()) { event.preventDefault(); event.stopPropagation(); return; }
      if ((window.MemuKeys ? MemuKeys.latin(event) : event.key) === "/" && !typing) { event.preventDefault(); input.focus(); input.select(); }
      else if (event.altKey && event.key === "ArrowLeft") { event.preventDefault(); historyStep(-1); }
      else if (event.altKey && event.key === "ArrowRight") { event.preventDefault(); historyStep(1); }
    });
  }

  async function loadCatalog() {
    try {
      catalog = await getJson(`/api/pg/docs?lang=${lang()}`);
    } catch (error) {
      catalog = { lang: lang(), groups: [], home: "" };
      note(L("Документация не загрузилась: ", "The documentation did not load: ") + error.message, "err");
    }
    return catalog;
  }

  // Ctrl+F must work wherever the focus is while the documentation tab is open
  // (right after opening it the focus is still on the settings tab button).
  document.addEventListener("keydown", (event) => {
    const key = String(event.key || "").toLowerCase();
    if (!(event.ctrlKey || event.metaKey) || event.altKey || !(key === "f" || key === "а" || event.code === "KeyF")) return;
    const pane = document.getElementById("pane-docs");
    if (!pane || !pane.classList.contains("active") || !pane.offsetParent || !$("#docs-find", pane)) return;
    event.preventDefault();
    event.stopPropagation();
    findOpen();
  }, true);

  let pendingPage = "";
  let mounting = null;

  /** Render the documentation into its settings pane (idempotent). */
  async function mount(page, anchor) {
    const pane = document.getElementById("pane-docs");
    if (!pane) return;
    page = page || pendingPage;
    anchor = anchor || pendingAnchor;
    pendingPage = "";
    pendingAnchor = "";
    if (mounting) {
      // The shell is still being built: finish that first, then navigate.
      await mounting;
      if (page) await go(page, anchor || "");
      return;
    }
    if (catalog && catalog.lang !== lang()) { catalog = null; current = null; back = []; forward = []; }
    if (!pane.querySelector(".docs-shell")) {
      mounting = (async () => {
        pane.innerHTML = shell();
        bind(pane);
        if (query) $("#docs-query", pane).value = query;
        const previous = current;
        current = null;
        if (!catalog) await loadCatalog();
        drawTree();
        let start = page || (previous && previous.id) || "";
        if (!start) { try { start = localStorage.getItem("memu_docs_page") || ""; } catch (_) {} }
        await go(start || catalog.home, anchor || "", { history: true, reload: true });
        if (query.trim().length >= 2) runSearch();
      })();
      try { await mounting; } finally { mounting = null; }
      return;
    }
    if (page) await go(page, anchor || "");
  }

  /** Open Settings → Documentation, optionally at a page and a section. */
  async function open(page, anchor) {
    pendingPage = page || "";
    pendingAnchor = anchor || "";
    if (typeof currentSettingsTab !== "undefined") currentSettingsTab = "docs";
    const visibleSettings = document.getElementById("panel-back")?.classList.contains("visible")
      && document.querySelector("#panel .settings-body");
    if ((!document.getElementById("pane-docs") || !visibleSettings) && typeof showSettings === "function") {
      await showSettings(true);   // renderSettingsPanel mounts the pane with the pending page
      return;
    }
    $$(".settings-tab-btn").forEach((button) => button.classList.toggle("active", button.dataset.setTab === "docs"));
    $$(".settings-tab-pane").forEach((pane) => pane.classList.toggle("active", pane.id === "pane-docs"));
    const body = document.querySelector(".settings-body");
    if (body) { body.classList.add("docs-mode"); body.scrollTop = 0; }
    await mount();
  }

  return Object.freeze({ mount, open, render, inline, anchorOf, go,
    get current() { return current; } });
})();
window.MemuDocs = MemuDocs;
