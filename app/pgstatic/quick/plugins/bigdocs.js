

(() => {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BASE = "/api/pg/bigdocs";
  const INLINE_TOKENS = 15000;
  const S = { doc: null, mode: "ask", busy: false, run: null, el: null, pendingWhole: null };

  async function json(path, options = {}) {
    const response = await fetch(path, { method: options.method || "GET", headers: { "Content-Type": "application/json" },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const e = new Error(data.error || `HTTP ${response.status}`); e.status = response.status; e.payload = data; throw e; }
    return data;
  }
  async function ingest(file, onProgress) {
    const response = await fetch(`${BASE}/ingest?name=${encodeURIComponent(file.name || "file")}&lang=${Q.lang()}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
    if (!response.ok) { const d = await response.json().catch(() => ({})); throw new Error(d.error || `HTTP ${response.status}`); }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buf = "", last = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        try { last = JSON.parse(line); if (!last.done && !last.error) onProgress(last); } catch (e) {}
      }
    }
    if (buf.trim()) { try { last = JSON.parse(buf.trim()); } catch (e) {} }
    if (!last || last.error || !last.doc) throw new Error(last?.error || t("Документ не прочитан", "The document could not be read"));
    return last.doc;
  }
  const pagesText = (d) => `${d.approx_pages ? "≈" : ""}${Number(d.pages || 0).toLocaleString(Q.lang() === "en" ? "en-US" : "ru-RU")} ${t("стр.", "pages")}`;

  async function take(file) {
    if (file.size > 200 * 1024 * 1024) { Q.toast(t("Файл больше 200 МБ", "The file is over 200 MB"), "err", 3600); return true; }
    S.doc = { name: file.name, pages: 0, reading: t("Читаю…", "Reading…") };
    render();
    let doc;
    try {
      doc = await ingest(file, (ev) => {
        S.doc.reading = ev.stage === "read" && ev.pages > 1 ? t(`Читаю: стр. ${ev.page} из ${ev.pages}`, `Reading: page ${ev.page} of ${ev.pages}`)
          : ev.stage === "index" ? t("Строю поиск…", "Indexing…") : t("Читаю…", "Reading…");
        render();
      });
    } catch (e) {
      S.doc = null; render();

      if (file.size <= 15 * 1024 * 1024) return false;
      Q.toast(`${file.name}: ${e.message}`, "err", 5000);
      return true;
    }
    if (doc.tokens <= INLINE_TOKENS) { S.doc = null; render(); return false; }
    S.doc = doc; S.mode = "ask";
    render();
    Q.toast(t(`«${doc.name}»: ${pagesText(doc)} — документ целиком на компьютере, спрашивайте`, `“${doc.name}”: ${pagesText(doc)} — the whole document is on this computer, ask away`), "ok", 3600);
    return true;
  }

  function render() {
    if (!S.el) return;
    const d = S.doc;
    if (!d) { S.el.innerHTML = ""; Q.hidePanel("bigdocs"); return; }
    Q.showPanel("bigdocs");
    const run = S.run;
    let tail = "";
    if (d.reading) tail = `<span class="qbd-meta">${esc(d.reading)}</span>`;
    else if (run?.stage === "confirm") tail = `<span class="qbd-meta">${esc(run.text)}</span><button class="qbd-btn primary" data-q="go">${esc(t("Запустить", "Start"))}</button><button class="qbd-btn" data-q="ask">${esc(t("По фрагментам", "From fragments"))}</button>`;
    else if (run?.stage === "run") {
      const pct = run.total ? Math.round(100 * run.done / run.total) : 0;
      tail = `<span class="qbd-progress" title="${pct}%"><i style="width:${pct}%"></i></span><span class="qbd-meta">${esc(run.label || t("Готовлюсь…", "Preparing…"))}</span><button class="qbd-btn" data-q="stop">${esc(t("Стоп", "Stop"))}</button>`;
    } else {
      tail = `<span class="qbd-meta">${esc(pagesText(d))}</span><span class="qbd-modes" role="radiogroup">
        <button role="radio" aria-checked="${S.mode === "ask"}" class="${S.mode === "ask" ? "on" : ""}" data-q="mode-ask" title="${esc(t("Ответ по найденным фрагментам с номерами страниц", "An answer from the fragments found, with page numbers"))}">${esc(t("Спросить", "Ask"))}</button>
        <button role="radio" aria-checked="${S.mode === "whole"}" class="${S.mode === "whole" ? "on" : ""}" data-q="mode-whole" title="${esc(t("Пересказ по частям и свод — стоимость покажу заранее", "Part-by-part, then combined — the cost is shown first"))}">${esc(t("Весь документ", "Whole document"))}</button></span>`;
    }
    S.el.innerHTML = `<div class="qbd-strip"><span class="qbd-ico" aria-hidden="true">📄</span><b class="qbd-name" title="${esc(d.name)}">${esc(d.name)}</b>${tail}
      <button class="qbd-x" data-q="close" aria-label="${esc(t("Убрать документ", "Remove the document"))}" title="${esc(t("Убрать документ", "Remove the document"))}">×</button></div>`;
    S.el.querySelectorAll("[data-q]").forEach((b) => b.onclick = () => act(b.dataset.q));
    Q.resize();
  }
  function act(what) {
    if (what === "close") { if (S.run?.job) fetch(`${BASE}/jobs/${S.run.job}/cancel`, { method: "POST" }).catch(() => {}); S.doc = null; S.run = null; render(); return; }
    if (what === "mode-ask") { S.mode = "ask"; render(); return; }
    if (what === "mode-whole") { S.mode = "whole"; render(); return; }
    if (what === "stop" && S.run?.job) { fetch(`${BASE}/jobs/${S.run.job}/cancel`, { method: "POST" }).catch(() => {}); return; }
    if ((what === "go" || what === "ask") && S.pendingWhole) { const resolve = S.pendingWhole; S.pendingWhole = null; resolve(what); }
  }

  Q.addRequestContext("bigdoc", () => (S.doc?.id && !S.doc.reading
    ? { type: "bigdoc", id: S.doc.id, mode: S.sendWhole ? "whole" : "ask", prompt: S.sendWhole || "" } : null));

  Q.on("submit", (e) => {
    if (!S.doc?.id || S.mode !== "whole" || S.sendWhole || !String(e.text || "").trim()) return;
    e.preventDefault();
    runWhole(String(e.text).trim());
  });
  async function runWhole(question) {
    const model = Q.state().model;
    if (!model) { Q.toast(t("Выберите модель", "Choose a model"), "err"); return; }
    try {
      const est = await json(`${BASE}/estimate`, { method: "POST", body: { id: S.doc.id, model, prompt: question, lang: Q.lang() } });
      if (est.needs_confirm) {
        const cost = est.price_known ? `≈ ${est.cost_text}` : t("цена неизвестна", "price unknown");
        S.run = { stage: "confirm", text: t(`${est.parts} частей · ${est.requests} запросов · ${cost}`, `${est.parts} parts · ${est.requests} requests · ${cost}`) };
        render();
        const choice = await new Promise((resolve) => { S.pendingWhole = resolve; });
        if (choice === "ask") { S.run = null; S.mode = "ask"; render(); Q.send(question); return; }
      }
      S.run = { stage: "run", done: est.resumable_parts || 0, total: est.parts, label: "" };
      render();
      const started = await json(`${BASE}/whole`, { method: "POST", body: { id: S.doc.id, model, prompt: question, lang: Q.lang(), confirmed: true } });
      S.run.job = started.job.id;
      const final = started.job.status === "done" ? started.job : await follow(started.job.id);
      if (final.status !== "done") {
        S.run = null; render();
        Q.toast(final.status === "cancelled" ? t("Остановлено — готовые части сохранены", "Stopped — finished parts are kept") : (final.error || t("Ошибка", "Error")), final.status === "cancelled" ? "" : "err", 5000);
        return;
      }
      S.run = null; render();
      S.sendWhole = question;
      Q.send(question);
    } catch (error) {
      S.run = null; render();
      Q.toast(error.message || String(error), "err", 5000);
    }
  }
  function follow(jobId) {
    return new Promise((resolve) => {
      const source = new EventSource(`${BASE}/jobs/${jobId}/events`);
      source.onmessage = (event) => {
        let ev = {};
        try { ev = JSON.parse(event.data); } catch (e) { return; }
        if (ev.stage === "map" || ev.stage === "plan") { S.run.done = ev.done_parts ?? ev.done ?? S.run.done; S.run.total = ev.total || S.run.total; S.run.label = ev.label || `${ev.unit || ""} ${S.run.done} ${t("из", "of")} ${S.run.total}`; render(); }
        else if (ev.stage === "reduce") { S.run.label = ev.label; render(); }
        if (ev.stage === "end") { source.close(); resolve(ev.state || {}); }
      };
      source.onerror = () => { source.close(); json(`${BASE}/jobs/${jobId}`).then(resolve).catch(() => resolve({ status: "error", error: t("связь прервалась", "connection lost") })); };
    });
  }

  Q.registerPanel("bigdocs", (el) => { S.el = el; render(); return {}; }, { slot: "top", order: -5, hidden: true });
  Q.on("chat-new", () => { S.doc = null; S.run = null; S.sendWhole = ""; render(); });
  Q.on("answer-done", () => { S.sendWhole = ""; });

  const CITE = /\[(≈)?\s*(стр\.?|p\.|page)\s*(\d{1,5}(?:\s*[,–-]\s*\d{1,5})*)(?:[,;]\s*(?:§|п\.|пп\.|para\.?|section|раздел|разд\.)[^\]\[]{0,40})?\]/gi;
  const CITE_ONE = new RegExp(CITE.source, "i");
  const quotes = new Map();
  function decorate() {
    const d = S.doc;
    if (!d?.id || d.reading) return;
    document.querySelectorAll(".qa-md:not([data-streaming])").forEach((box) => {
      const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, { acceptNode: (n) =>
        (n.parentElement?.closest("code, pre, .qbd-cite") ? NodeFilter.FILTER_REJECT : (CITE_ONE.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP)) });
      const nodes = [];
      for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
      nodes.forEach((node) => {
        CITE.lastIndex = 0;
        const html = esc(node.nodeValue).replace(CITE, (whole, approx, word, body) => {
          const page = parseInt(body, 10);
          if (!page || page > (d.pages || Infinity)) return whole;
          return `<button type="button" class="qbd-cite" data-page="${page}" aria-label="${esc(t(`Цитата со страницы ${page}`, `Quote from page ${page}`))}">${whole.replace(/^\[|\]$/g, "")}</button>`;
        });
        const span = document.createElement("span");
        span.innerHTML = html;
        node.replaceWith(...span.childNodes);
      });
    });
  }
  let decorateTimer = 0;
  new MutationObserver(() => { clearTimeout(decorateTimer); decorateTimer = setTimeout(decorate, 120); })
    .observe(document.body, { childList: true, subtree: true });
  let tipEl = null;
  function hideQuote() { tipEl?.remove(); tipEl = null; }
  async function showQuote(button) {
    hideQuote();
    const page = +button.dataset.page;
    const claim = (button.closest("p, li") || button.parentElement)?.textContent?.slice(0, 400) || "";
    tipEl = document.createElement("div");
    tipEl.className = "qbd-tip"; tipEl.setAttribute("role", "tooltip");
    tipEl.innerHTML = `<b>${esc(t("Стр.", "P."))} ${page}</b> <span class="qbd-tip-name">${esc(S.doc?.name || "")}</span><div class="qbd-tip-q">${esc(t("Ищу цитату…", "Finding the quote…"))}</div>`;
    document.body.appendChild(tipEl);
    const r = button.getBoundingClientRect();
    const w = Math.min(360, window.innerWidth - 16);
    tipEl.style.width = w + "px";
    tipEl.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
    const place = () => { if (!tipEl) return; const below = r.bottom + 6 + tipEl.offsetHeight < window.innerHeight; tipEl.style.top = (below ? r.bottom + 6 : Math.max(8, r.top - 6 - tipEl.offsetHeight)) + "px"; };
    place();
    const key = `${S.doc?.id}:${page}:${claim.slice(-120)}`;
    try {
      if (!quotes.has(key)) quotes.set(key, json(`${BASE}/quote`, { method: "POST", body: { id: S.doc?.id, page, claim, lang: Q.lang() } }));
      const q = await quotes.get(key);
      if (!tipEl) return;
      const box = tipEl.querySelector(".qbd-tip-q");
      box.textContent = !q.exists ? t("В документе нет такой страницы.", "The document has no such page.")
        : q.quote ? (Q.lang() === "en" ? `“${q.quote}”` : `«${q.quote}»`) : t("На этой странице нет текста.", "This page has no text.");
      place();
    } catch (e) {
      quotes.delete(key);
      if (tipEl) tipEl.querySelector(".qbd-tip-q").textContent = e.status === 404 || e.status === 410 ? t("Документа больше нет в кэше.", "The document is no longer cached.") : String(e.message || e);
    }
  }
  document.addEventListener("mouseover", (e) => { const b = e.target.closest?.(".qbd-cite"); if (b) showQuote(b); else if (tipEl && !e.target.closest?.(".qbd-tip")) hideQuote(); });
  document.addEventListener("click", (e) => { const b = e.target.closest?.(".qbd-cite"); if (b) { e.preventDefault(); showQuote(b); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && tipEl) hideQuote(); }, true);
  window.addEventListener("scroll", hideQuote, true);

  window.MemuQuickBigDocs = { take, state: () => ({ ...S, el: undefined }) };
})();
