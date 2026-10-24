/* Round 29 — documentation extras (window.MemuDocsAI), used by docs.js:
 *  • «Кратко от ИИ» / «AI summary»: a collapsible card under the page title.
 *    The summary is shipped with the docs (app/docs/summaries/*.json, reviewed);
 *    «Пересказать моей моделью» and «Спросить об этой статье» go through the
 *    user's current model via MemuAI Proxy (POST /api/pg/docs/ai).
 *  • Screenshots: ![alt](docs-img/<lang>/<name>.webp "caption") become lazy
 *    figures that open in the app's image viewer.
 */
"use strict";
window.MemuDocsAI = (() => {
  const OPEN_KEY = "memu_docs_ai_open";
  const answers = new Map();      // "lang:id" → [{q, a, error}]
  const retold = new Map();       // "lang:id" → {tldr, points, model}
  const busy = new Set();

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const L = (ru, english) => (en() ? english : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const glyph = (name) => (typeof icon === "function" ? icon(name) : "");
  const inline = (text) => (window.MemuDocs && MemuDocs.inline ? MemuDocs.inline(text) : esc(text));
  const block = (text) => (window.MemuDocs && MemuDocs.render ? MemuDocs.render(text) : `<p>${esc(text)}</p>`);
  const model = () => (typeof currentModel === "function" ? currentModel() : "") || "";
  const modelName = (id) => (typeof modelDisplayName === "function" ? modelDisplayName(id) : id);
  const isOpen = () => { try { return localStorage.getItem(OPEN_KEY) !== "0"; } catch (_) { return true; } };
  const keyOf = (page) => `${page.lang}:${page.id}`;
  const SPARK = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.8l1.9 5.3 5.3 1.9-5.3 1.9L12 17.2l-1.9-5.3L4.8 10l5.3-1.9z" fill="currentColor"/><path d="M18.6 14.6l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9z" fill="currentColor" opacity=".7"/></svg>`;

  /* ------------------------------- screenshots ------------------------------ */

  function figure(path, alt, caption) {
    const src = "/" + String(path).replace(/^\/+/, "");
    const text = caption || alt || "";
    const plainText = text.replace(/\+\+(.+?)\+\+/g, "$1").replace(/[`*]/g, "");   // attributes / viewer title: no markup
    return `<figure class="docs-figure"><button type="button" class="docs-figure-zoom" data-docs-zoom="${esc(src)}" data-docs-caption="${esc(plainText)}"
        title="${esc(L("Открыть крупно", "Open full size"))}" aria-label="${esc(L("Открыть крупно: ", "Open full size: ") + plainText)}">
        <img src="${esc(src)}" alt="${esc(alt || caption || "")}" loading="lazy" decoding="async"
          onload="this.closest('figure').classList.add('loaded')" onerror="this.closest('figure').classList.add('broken')">
        <span class="docs-figure-hint" aria-hidden="true">${glyph("fit") || glyph("search")}</span></button>
      ${text ? `<figcaption>${inline(text)}</figcaption>` : ""}</figure>`;
  }

  function zoom(button) {
    const src = button.dataset.docsZoom;
    const name = button.dataset.docsCaption || L("Снимок экрана", "Screenshot");
    if (typeof openImagePreview === "function") openImagePreview(src, name);
    else window.open(src, "_blank", "noopener");
  }

  /* --------------------------------- summary -------------------------------- */

  function summaryOf(page) {
    return retold.get(keyOf(page)) || page.ai_summary || null;
  }

  function cardHtml(page) {
    const data = summaryOf(page);
    const mine = retold.has(keyOf(page));
    const open = isOpen();
    const points = data && data.points ? data.points : [];
    const count = points.length ? L(`${points.length} ${plural(points.length, "главная мысль", "главные мысли", "главных мыслей")}`, `${points.length} key point${points.length === 1 ? "" : "s"}`) : "";
    const stale = data && data.stale && !mine;
    const loading = busy.has(keyOf(page) + ":summary");
    const body = loading ? `<div class="docs-ai-skeleton" aria-hidden="true"><i></i><i></i><i></i><i></i></div>`
      : data ? `<p class="docs-ai-tldr">${inline(data.tldr)}</p>${points.length ? `<ul class="docs-ai-points">${points.map((point) => `<li>${inline(point)}</li>`).join("")}</ul>` : ""}`
        : `<p class="docs-ai-empty">${L("Для этой страницы ещё нет краткого пересказа. Его может написать ваша модель.", "There is no summary for this page yet. Your model can write one.")}</p>`;
    const source = mine
      ? `${L("Пересказ модели", "Retold by")} <b>${esc(modelName(data.model))}</b>`
      : stale ? L("Статья изменилась после пересказа — сверяйтесь с текстом ниже", "The article changed after this summary — check the text below")
        : L("Подготовлено заранее и сверено со статьёй", "Prepared in advance and checked against the article");
    const ready = !!model();
    const askOpen = answers.has(keyOf(page)) || false;
    return `<section class="docs-ai ${open ? "open" : ""} ${stale ? "stale" : ""}" data-docs-ai aria-label="${esc(L("Кратко от ИИ", "AI summary"))}">
      <div class="docs-ai-head">
        <button type="button" class="docs-ai-toggle" data-ai="toggle" aria-expanded="${open}">
          <span class="docs-ai-badge">${SPARK}</span>
          <b>${L("Кратко от ИИ", "AI summary")}</b>
          ${count ? `<span class="docs-ai-count">${esc(count)}</span>` : ""}
          ${stale ? `<span class="docs-ai-flag">${L("может устареть", "may be outdated")}</span>` : ""}
          <i class="docs-ai-chevron" aria-hidden="true"></i>
        </button>
        ${data ? `<button type="button" class="iconbtn docs-ai-mini" data-ai="copy" title="${esc(L("Копировать пересказ", "Copy the summary"))}" aria-label="${esc(L("Копировать пересказ", "Copy the summary"))}">${glyph("copy")}</button>` : ""}
      </div>
      <div class="docs-ai-body" ${open ? "" : "hidden"}>
        <div class="docs-ai-content" aria-live="polite">${body}</div>
        <div class="docs-ai-foot">
          <span class="docs-ai-source">${source}</span>
          <span class="docs-ai-actions">
            ${mine ? `<button type="button" class="docs-ai-btn ghost" data-ai="restore">${glyph("undo")}<span>${L("Исходный", "Original")}</span></button>` : ""}
            <button type="button" class="docs-ai-btn" data-ai="retell" ${ready && !loading ? "" : "disabled"}
              title="${esc(ready ? L("Пересказать страницу моделью ", "Retell the page with ") + modelName(model()) : L("Выберите модель в окне чата", "Pick a model in the chat window"))}">
              ${glyph("retry")}<span>${L("Пересказать моей моделью", "Retell with my model")}</span></button>
            <button type="button" class="docs-ai-btn" data-ai="ask" aria-expanded="${askOpen}">${glyph("chat")}<span>${L("Спросить об этой статье", "Ask about this page")}</span></button>
          </span>
        </div>
        <div class="docs-ai-ask" ${askOpen ? "" : "hidden"}>
          <div class="docs-ai-thread">${threadHtml(page)}</div>
          <form class="docs-ai-form" data-ai-form>
            <input type="text" maxlength="2000" autocomplete="off" spellcheck="true" data-ai-input
              placeholder="${esc(L("Например: где это включается?", "For example: where do I turn this on?"))}"
              aria-label="${esc(L("Вопрос об этой статье", "A question about this page"))}" ${ready ? "" : "disabled"}>
            <button type="submit" class="docs-ai-send" ${ready ? "" : "disabled"} title="${esc(L("Спросить (Enter)", "Ask (Enter)"))}" aria-label="${esc(L("Спросить", "Ask"))}">${glyph("send")}</button>
          </form>
          <div class="docs-ai-note">${ready
            ? L(`Ответит ${esc(modelName(model()))} — по тексту этой страницы. Ответ нигде не сохраняется.`, `${esc(modelName(model()))} answers from this page only. Nothing is saved.`)
            : L("Выберите модель в окне чата — нужен запущенный MemuAI Proxy.", "Pick a model in the chat window — MemuAI Proxy has to be running.")}</div>
        </div>
      </div>
    </section>`;
  }

  function plural(n, one, few, many) {
    const a = n % 10, b = n % 100;
    if (a === 1 && b !== 11) return one;
    if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
    return many;
  }

  function threadHtml(page) {
    const rows = answers.get(keyOf(page)) || [];
    return rows.map((row, index) => `<div class="docs-ai-qa">
        <div class="docs-ai-q">${esc(row.q)}</div>
        <div class="docs-ai-a ${row.error ? "error" : ""}">${row.pending
          ? `<span class="docs-ai-typing" aria-label="${esc(L("Модель отвечает", "The model is answering"))}"><i></i><i></i><i></i></span>`
          : row.error ? esc(row.error) : block(row.a)}</div>
        ${!row.pending && !row.error ? `<div class="docs-ai-qa-actions">
          <button type="button" class="docs-ai-link" data-ai="copy-answer" data-index="${index}">${glyph("copy")}${L("Копировать", "Copy")}</button>
          <button type="button" class="docs-ai-link" data-ai="to-chat" data-index="${index}">${glyph("forward")}${L("Продолжить в чате", "Continue in chat")}</button></div>` : ""}
      </div>`).join("");
  }

  function redraw(card, page, focusInput) {
    const fresh = document.createElement("div");
    fresh.innerHTML = cardHtml(page);
    const next = fresh.firstElementChild;
    card.replaceWith(next);
    bindCard(next, page);
    if (focusInput) { const input = next.querySelector("[data-ai-input]"); if (input) input.focus(); }
    return next;
  }

  async function post(body) {
    const response = await fetch("/api/pg/docs/ai", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  const live = (page) => document.querySelector("#docs-article [data-docs-ai]")
    && window.MemuDocs && MemuDocs.current && MemuDocs.current.id === page.id && MemuDocs.current.lang === page.lang;

  async function retell(card, page) {
    const key = keyOf(page) + ":summary";
    if (busy.has(key)) return;
    busy.add(key);
    card = redraw(card, page);
    try {
      const data = await post({ mode: "summary", lang: page.lang, id: page.id, model: model() });
      retold.set(keyOf(page), { tldr: data.tldr, points: data.points || [], model: data.model });
    } catch (error) {
      if (typeof toast === "function") toast(error.message, "err", 6000);
    } finally {
      busy.delete(key);
      if (live(page)) redraw(document.querySelector("#docs-article [data-docs-ai]"), page);
    }
  }

  async function ask(card, page, question) {
    const rows = answers.get(keyOf(page)) || [];
    const history = rows.filter((row) => !row.error && !row.pending).slice(-3)
      .flatMap((row) => [{ role: "user", content: row.q }, { role: "assistant", content: row.a }]);
    const row = { q: question, a: "", pending: true };
    rows.push(row);
    answers.set(keyOf(page), rows);
    card = redraw(card, page);
    try {
      const data = await post({ mode: "ask", lang: page.lang, id: page.id, model: model(), question, history });
      row.a = data.answer || "";
    } catch (error) {
      row.error = error.message;
    } finally {
      row.pending = false;
      if (live(page)) {
        const next = redraw(document.querySelector("#docs-article [data-docs-ai]"), page, true);
        const thread = next.querySelector(".docs-ai-thread");
        if (thread && thread.lastElementChild) thread.lastElementChild.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function toChat(page, row) {
    const prompt = L(`Вопрос по странице документации MemuAI «${page.title}»: ${row.q}\n\nКраткий ответ, который я уже получил:\n${row.a}`,
      `A question about the MemuAI documentation page “${page.title}”: ${row.q}\n\nThe short answer I already got:\n${row.a}`);
    if (typeof closePanel === "function") closePanel();
    const input = document.getElementById("input");
    if (!input) return;
    input.value = prompt;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  function summaryText(page) {
    const data = summaryOf(page);
    if (!data) return "";
    const plain = (text) => String(text || "").replace(/\+\+([^\s+](?:[^\n]*?[^\s+])??)\+\+/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");
    return `${page.title}\n\n${plain(data.tldr)}\n${(data.points || []).map((point) => "• " + plain(point)).join("\n")}`;
  }

  function bindCard(card, page) {
    card.addEventListener("click", (event) => {
      const target = event.target.closest("[data-ai]");
      if (!target) return;
      const action = target.dataset.ai;
      if (action === "toggle") {
        const open = !card.classList.contains("open");
        try { localStorage.setItem(OPEN_KEY, open ? "1" : "0"); } catch (_) {}
        card.classList.toggle("open", open);
        card.querySelector(".docs-ai-body").hidden = !open;
        target.setAttribute("aria-expanded", String(open));
      } else if (action === "copy") {
        if (typeof copyText === "function") copyText(summaryText(page));
      } else if (action === "retell") {
        retell(card, page);
      } else if (action === "restore") {
        retold.delete(keyOf(page));
        redraw(card, page);
      } else if (action === "ask") {
        const box = card.querySelector(".docs-ai-ask");
        box.hidden = !box.hidden;
        target.setAttribute("aria-expanded", String(!box.hidden));
        if (!box.hidden) { const input = box.querySelector("[data-ai-input]"); if (input && !input.disabled) input.focus(); }
      } else if (action === "copy-answer" || action === "to-chat") {
        const row = (answers.get(keyOf(page)) || [])[+target.dataset.index];
        if (!row) return;
        if (action === "to-chat") toChat(page, row);
        else if (typeof copyText === "function") copyText(row.a);
      }
    });
    const form = card.querySelector("[data-ai-form]");
    if (form) form.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = form.querySelector("[data-ai-input]");
      const question = String(input.value || "").trim();
      if (!question || input.disabled) return;
      input.value = "";
      ask(card, page, question);
    });
    const input = card.querySelector("[data-ai-input]");
    if (input) input.addEventListener("keydown", (event) => {
      // Esc in the field closes the question box, not the whole settings window.
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); card.querySelector("[data-ai='ask']").click(); }
    });
  }

  /** Called by docs.js after every page render. */
  function decorate(article, page) {
    if (!article || !page) return;
    article.querySelectorAll("[data-docs-zoom]").forEach((button) => { button.onclick = () => zoom(button); });
    const holder = document.createElement("div");
    holder.innerHTML = cardHtml(page);
    const card = holder.firstElementChild;
    const title = article.querySelector("h1");
    if (title) title.after(card); else article.prepend(card);
    bindCard(card, page);
  }

  return Object.freeze({ decorate, figure, summaryText });
})();
