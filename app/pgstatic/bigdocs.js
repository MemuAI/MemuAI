

(() => {
  "use strict";
  const EN = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const tr = (ru, en) => (EN() ? en : ru);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BASE = "/api/pg/bigdocs";
  const MAX_BYTES = 200 * 1024 * 1024;
  const PACKED = /\.(pdf|docx|epub|fb2)$/i;
  const TEXTY = /\.(txt|md|markdown|html?|rst|log)$/i;
  const toastSafe = (text, kind, ms) => { try { toast(text, kind, ms); } catch (e) { console.warn(text); } };
  const num = (n) => Number(n || 0).toLocaleString(EN() ? "en-US" : "ru-RU");

  const ICON_DOC = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 12h6M10 15.5h6M10 19h4"/></svg>`;
  const ICON_BOOK = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/></svg>`;
  const ICON_ASK = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></svg>`;
  const ICON_LAYERS = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/></svg>`;
  const ICON_CHECK = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>`;
  const ICON_STOP = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>`;
  const ICON_X = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>`;
  const svg = (s, cls = "") => `<span class="bd-ico ${cls}">${s}</span>`;

  function takes(file) {
    if (!file || /^image\//.test(file.type || "") || file.size > MAX_BYTES) return false;
    const name = String(file.name || "");
    if (PACKED.test(name) || /pdf|wordprocessingml|epub/.test(file.type || "")) return true;
    return TEXTY.test(name) && file.size > 300 * 1024;
  }
  const fmtBytes = (n) => (n >= 1024 * 1024 ? `${(n / 1048576).toFixed(1)} ${tr("МБ", "MB")}` : `${Math.max(1, Math.round(n / 1024))} ${tr("КБ", "KB")}`);
  const fmtTokens = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} ${tr("млн", "M")}` : n >= 1000 ? `${Math.round(n / 1000)} ${tr("тыс.", "K")}` : String(n || 0));
  function pagesText(doc) {
    const n = doc.pages || 0;
    return `${doc.approx_pages ? "≈" : ""}${num(n)} ${tr(plural(n, "страница", "страницы", "страниц"), n === 1 ? "page" : "pages")}`;
  }
  function plural(n, one, few, many) {
    const a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  }
  function metaText(a) {
    if (a.pending) return a.progress || tr("Читаю документ…", "Reading the document…");
    const doc = a.bigdoc || {};
    return [pagesText(doc), a.size ? fmtBytes(a.size) : "", doc.tokens ? `≈${fmtTokens(doc.tokens)} ${tr("ток.", "tok.")}` : ""].filter(Boolean).join(" · ");
  }
  function kindLabel(a) {
    return (String(a.name || "").match(/\.(\w{1,6})$/)?.[1] || "DOC").toUpperCase();
  }

  async function readNdjson(response, onLine) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "", last = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        try { last = JSON.parse(line); onLine(last); } catch (e) {  }
      }
    }
    if (buf.trim()) { try { last = JSON.parse(buf.trim()); onLine(last); } catch (e) {} }
    return last;
  }
  function progressText(ev, name) {
    if (ev.stage === "read" && ev.pages > 1) return tr(`Читаю: стр. ${num(ev.page)} из ${num(ev.pages)}`, `Reading: page ${num(ev.page)} of ${num(ev.pages)}`);
    if (ev.stage === "index") return tr("Строю поиск по документу…", "Building the document search…");
    if (ev.stage === "cached") return tr("Уже прочитан — беру из кэша", "Already read — from the cache");
    return tr("Читаю документ…", "Reading the document…");
  }
  async function ingest(file, onProgress) {
    const response = await fetch(`${BASE}/ingest?name=${encodeURIComponent(file.name || "file")}&lang=${EN() ? "en" : "ru"}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    const last = await readNdjson(response, (ev) => { if (!ev.done && !ev.error) onProgress(ev); });
    if (!last || last.error || !last.doc) throw new Error(last?.error || tr("Документ не прочитан", "The document could not be read"));
    return last.doc;
  }
  function attach(file, list, render) {
    const item = { name: file.name || "document", size: file.size, type: file.type || "", data: "", bigdocChip: true, pending: true,
      progress: tr("Читаю документ…", "Reading the document…"), bigdocMode: "ask" };
    list.push(item);
    render();
    let lastPaint = 0;
    ingest(file, (ev) => {
      item.progress = progressText(ev, item.name);
      const now = performance.now();
      if (now - lastPaint > 180) { lastPaint = now; patchChip(item) || render(); }
    }).then((doc) => {
      item.pending = false; item.bigdoc = doc; item.progress = "";
      if (!list.includes(item)) return;
      render();
      toastSafe(tr(`«${item.name}»: ${pagesText(doc)} — можно спрашивать`, `“${item.name}”: ${pagesText(doc)} — ready for questions`), "ok", 3200);
    }).catch((error) => {
      const index = list.indexOf(item);
      if (index >= 0) list.splice(index, 1);
      render();
      toastSafe(`${item.name}: ${error.message || error}`, "warn", 6000);
    });
  }
  function patchChip(item) {
    const node = document.querySelector(`#attach-row .bd-chip[data-bd-name="${CSS.escape(item.name)}"] .bd-chip-meta small`);
    if (!node || !item.pending) return false;
    node.textContent = metaText(item);
    return true;
  }
  function chipHtml(a, i) {
    const name = a.name || tr("Документ", "Document");
    const mode = a.bigdocMode === "whole" ? "whole" : "ask";
    const modes = a.pending ? "" : `<div class="bd-modes" role="radiogroup" aria-label="${esc(tr("Режим документа", "Document mode"))}">
        <button type="button" role="radio" aria-checked="${mode === "ask"}" class="${mode === "ask" ? "on" : ""}" data-bd-mode="ask:${i}" title="${esc(tr("Ответ на вопрос с цитатами и номерами страниц. Документ, который помещается в контекст модели, уходит целиком; большой — найденными фрагментами.", "An answer with quotes and page numbers. A document that fits the model's context goes whole; a big one — as the fragments found."))}">${svg(ICON_ASK)}${esc(tr("Спросить", "Ask"))}</button>
        <button type="button" role="radio" aria-checked="${mode === "whole"}" class="${mode === "whole" ? "on" : ""}" data-bd-mode="whole:${i}" title="${esc(tr("Пересказ по частям и свод: модель прочитает весь документ. Стоимость покажу до начала.", "Part-by-part summaries, then one result: the model reads the whole document. The cost is shown before it starts."))}">${svg(ICON_LAYERS)}${esc(tr("Весь документ", "Whole document"))}</button>
      </div>`;
    return `<div class="attach-chip attach-card bd-chip ${a.pending ? "pending" : ""}" data-bd-name="${esc(name)}">
      <button type="button" class="attach-open" data-attach-preview="${i}" ${a.pending ? "disabled" : ""} title="${esc(name)}" aria-label="${esc(`${tr("Документ", "Document")}: ${name}`)}">
        <span class="attach-card-thumb bd-thumb">${a.pending ? `<span class="attach-spinner" aria-hidden="true"></span>` : `${svg(ICON_DOC)}<em>${esc(kindLabel(a))}</em>`}</span>
        <span class="attach-card-meta bd-chip-meta"><b>${esc(name)}</b><small>${esc(metaText(a))}</small></span>
      </button>${modes}<button type="button" class="attach-remove" data-attach-remove="${i}" aria-label="${esc(`${tr("Убрать", "Remove")}: ${name}`)}">${typeof icon === "function" ? icon("close") : "×"}</button></div>`;
  }
  function wireChips(row, list, render) {
    row.querySelectorAll("[data-bd-mode]").forEach((button) => button.onclick = (event) => {
      event.stopPropagation();
      const [mode, index] = String(button.dataset.bdMode).split(":");
      const item = list[+index];
      if (!item) return;
      item.bigdocMode = mode === "whole" ? "whole" : "ask";
      render();
    });
  }

  function contextLimit(model) {
    try { if (typeof modelContextLimit === "function") return Math.max(4000, +modelContextLimit(model) || 32000); } catch (e) {}
    return 32000;
  }
  async function getJson(path, options = {}) {
    const response = await fetch(path, { method: options.method || "GET", headers: { "Content-Type": "application/json" },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined, signal: options.signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.error || `HTTP ${response.status}`); error.status = response.status; error.payload = data; throw error; }
    return data;
  }
  const attrSafe = (s) => String(s || "").replace(/[<>&"]/g, " ");

  async function contextBlocks(messages, assistant, opts = {}) {
    const { model, signal, render } = opts;
    const users = messages.filter((m) => m.role === "user");
    const latest = [...users].reverse().find((m) => !m.internal) || users[users.length - 1];
    const question = String(latest?.content || "").trim();

    const docs = new Map();
    for (const m of users) for (const a of (m.attachments || [])) if (a.bigdoc?.id) docs.set(a.bigdoc.id, { a, latest: m === latest });
    if (!docs.size) return [];
    const lang = EN() ? "en" : "ru";
    const limit = contextLimit(model);

    let budget = Math.min(60000, Math.floor(limit * 0.45));
    const blocks = [];
    const refs = { docs: [] };
    assistant.bigdocRefs = refs;
    for (const [id, { a, latest: isLatest }] of [...docs.entries()].reverse()) {
      const doc = a.bigdoc;
      let info = null;
      try { info = await getJson(`${BASE}/doc/${id}?lang=${lang}`, { signal }); }
      catch (error) {
        if (error.name === "AbortError") throw error;
        toastSafe(tr(`«${a.name}» больше нет в кэше документов — прикрепите файл заново`, `“${a.name}” is no longer in the document cache — attach the file again`), "warn", 6000);
        blocks.push(`<file name="${attrSafe(a.name)}" unreadable="true">The document is no longer available locally; ask the user to attach it again.</file>`);
        continue;
      }
      const ref = { id, name: a.name || info.name, approx: !!info.approx_pages, pages: info.pages, retrieved: null, mode: "" };
      refs.docs.push(ref);
      if (isLatest && a.bigdocMode === "whole") {
        const summary = await runWhole(info, a, assistant, { model, signal, render, question, lang });
        if (summary !== null) {
          ref.mode = "whole";
          blocks.push(`<document name="${attrSafe(ref.name)}" pages="${info.pages}" mode="whole-document-summary">\n` +
            (lang === "en" ? "A map-reduce reading of ALL pages (every part summarized, then combined for the user's request). Page references [p. N] come from the parts.\n"
              : "Чтение ВСЕХ страниц по схеме map-reduce (каждая часть пересказана, затем сведена под запрос пользователя). Ссылки [стр. N] взяты из частей.\n")
            + summary + "\n</document>");
          continue;
        }

      }
      if (info.tokens <= budget) {
        budget -= info.tokens;
        const full = await getJson(`${BASE}/full`, { method: "POST", body: { id, lang }, signal });
        ref.mode = "full";
        blocks.push(`<document name="${attrSafe(ref.name)}" pages="${ref.approx ? "≈" : ""}${info.pages}" mode="full-text">\n${full.text}\n</document>`);
      } else {
        const found = await getJson(`${BASE}/search`, { method: "POST", body: { id, query: searchQuery(users, latest, question), lang, model }, signal });
        ref.mode = "retrieval";
        ref.retrieved = found.pages || [];
        blocks.push(found.block);
      }
    }
    return blocks;
  }

  function searchQuery(users, latest, question) {
    const real = users.filter((m) => !m.internal);
    const index = real.indexOf(latest);
    if (question.split(/\s+/).length <= 5 && index > 0) return `${real[index - 1].content || ""} ${question}`.slice(0, 2000);
    return question.slice(0, 2000);
  }
  function systemNote(assistant) {
    const refs = assistant?.bigdocRefs;
    if (!refs?.docs?.length) return "";
    const en = EN();
    const approx = refs.docs.some((d) => d.approx);
    const parts = [en
      ? "<document_citations>The user attached long document(s) (see <document> blocks). Cite the page of every fact you take from a document exactly as [p. N] right after the claim (several pages: [p. 3], [p. 7]); quote the decisive words in quotation marks. Cite only pages that appear in the blocks. If the blocks do not contain the answer, say so plainly"
      : "<document_citations>Пользователь приложил длинные документы (блоки <document>). После каждого факта из документа ставь ссылку на страницу строго в виде [стр. N] (несколько страниц — [стр. 3], [стр. 7]) и приводи решающие слова в кавычках «…». Ссылайся только на страницы, которые есть в блоках. Если в блоках ответа нет — прямо скажи об этом"];
    if (refs.docs.some((d) => d.mode === "retrieval")) parts.push(en
      ? "; a block with mode=\"retrieval\" holds only the fragments a local search found — not the whole document — so suggest the «Whole document» mode when the question needs all of it"
      : "; блок mode=\"retrieval\" содержит только найденные локальным поиском фрагменты, а не весь документ — если вопрос требует всего документа, предложи режим «Весь документ»");
    if (approx) parts.push(en ? ". Page numbers of flowing files are approximate" : ". Номера страниц у файлов без фиксированной вёрстки приблизительные");
    return parts.join("") + ".</document_citations>";
  }

  const waiters = new Map();
  function cardKey(m) { return m._bdKey || (m._bdKey = "bd" + Math.random().toString(36).slice(2, 10)); }

  async function runWhole(info, a, assistant, { model, signal, render, question, lang }) {
    const st = assistant.bigdoc = { id: info.id, name: a.name || info.name, pages: info.pages, stage: "estimate", model };
    render?.();
    const est = await getJson(`${BASE}/estimate`, { method: "POST", body: { id: info.id, model, prompt: question, lang }, signal });
    st.estimate = { parts: est.parts, unit: est.unit, requests: est.requests, input: est.input_tokens, output: est.output_tokens,
      cost: est.cost_usd, cost_text: est.cost_text, known: est.price_known, billing: est.billing, cached: est.cached, resumable: est.resumable_parts };
    st.total = est.parts; st.unit = est.unit;
    if (est.needs_confirm) {
      st.stage = "confirm";
      render?.();
      const choice = await new Promise((resolve, reject) => {
        const key = cardKey(assistant);
        waiters.set(key, resolve);
        signal?.addEventListener("abort", () => { waiters.delete(key); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
      });
      if (choice === "ask") { st.stage = "skipped"; render?.(); return null; }
    }
    st.stage = "run"; st.done = est.resumable || 0; st.label = ""; st.startedAt = Date.now();
    render?.();
    const started = await getJson(`${BASE}/whole`, { method: "POST", body: { id: info.id, model, prompt: question, lang, confirmed: true }, signal });
    const job = started.job;
    st.job = job.id;
    if (job.status === "done") return finishWhole(st, job, render, job.cached);
    return await new Promise((resolve, reject) => {
      let since = 0, closed = false;
      const source = new EventSource(`${BASE}/jobs/${job.id}/events`);
      const stop = () => { closed = true; try { source.close(); } catch (e) {} };
      const onAbort = () => {
        stop();
        fetch(`${BASE}/jobs/${job.id}/cancel`, { method: "POST" }).catch(() => {});
        st.stage = "cancelled"; render?.();
        reject(new DOMException("Aborted", "AbortError"));
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      source.onmessage = (event) => {
        let ev = {};
        try { ev = JSON.parse(event.data); } catch (e) { return; }
        if (typeof ev.i === "number") since = ev.i + 1;
        if (ev.stage === "plan") { st.total = ev.total; st.unit = ev.unit; st.done = ev.resumed || 0; st.plan = ev.plan; }
        else if (ev.stage === "map-start") { (st.inflight ||= {})[ev.index] = ev.title || ""; pickCurrent(st); }
        else if (ev.stage === "map") { st.done = ev.done; st.total = ev.total; st.label = ev.label; if (st.inflight) delete st.inflight[ev.index]; pickCurrent(st); }
        else if (ev.stage === "reduce") { st.reducing = true; st.label = ev.label; }
        else if (ev.stage === "retry") { st.note = ev.attempts ? tr(`Связь с моделью оборвалась — попытка ${ev.attempt} из ${ev.attempts}…`, `The model connection dropped — attempt ${ev.attempt} of ${ev.attempts}…`) : tr("Повторяю запрос…", "Retrying a request…"); }
        if (ev.stage === "map" || ev.stage === "reduce") st.note = "";
        if (ev.stage === "end" || ["done", "error", "cancelled"].includes(ev.stage)) {
          const state = ev.state || {};
          if (ev.stage === "done" || state.status === "done") {
            stop(); signal?.removeEventListener("abort", onAbort);
            resolve(finishWhole(st, { ...state, result: ev.result || state.result, status: "done" }, render, ev.cached));
            return;
          }
          if (ev.stage === "error" || state.status === "error") {
            stop(); signal?.removeEventListener("abort", onAbort);
            st.stage = "error"; st.error = ev.error || state.error || ""; render?.();
            reject(new Error(tr("Весь документ: ", "Whole document: ") + st.error));
            return;
          }
          if (ev.stage === "cancelled" || state.status === "cancelled") {
            stop(); signal?.removeEventListener("abort", onAbort);
            st.stage = "cancelled"; render?.();
            reject(new DOMException("Aborted", "AbortError"));
            return;
          }
        }
        patchCard(assistant);
      };
      source.onerror = () => {
        if (closed) return;

        getJson(`${BASE}/jobs/${job.id}`).then((state) => {
          if (state.status === "done") { stop(); resolve(finishWhole(st, state, render)); }
        }).catch(() => {
          stop(); st.stage = "error"; st.error = tr("связь с MemuAI прервалась — запустите ещё раз, готовые части сохранены", "the connection to MemuAI dropped — run it again, finished parts are kept");
          render?.(); reject(new Error(st.error));
        });
      };
    });
  }
  function finishWhole(st, state, render, cached) {
    st.stage = "done"; st.done = st.total; st.cached = !!(cached || st.estimate?.cached);
    st.elapsed = Math.round(((Date.now() - (st.startedAt || Date.now())) / 1000));
    st.chars = String(state.result || "").length;
    render?.();
    return String(state.result || "");
  }

  function costLine(e) {
    if (!e) return "";
    if (e.cached) return tr("Результат уже в кэше — бесплатно", "The result is cached — free");
    const tokens = `≈${fmtTokens(e.input + e.output)} ${tr("токенов", "tokens")}`;
    if (e.billing === "local") return `${tokens} · ${tr("локальная модель — бесплатно", "local model — free")}`;
    if (e.billing === "subscription") return `${tokens} · ${tr("по подписке, без оплаты за токены", "subscription, no per-token cost")}`;
    if (!e.known) return `${tokens} · ${tr("цена модели неизвестна", "the model's price is unknown")}`;
    return `${tokens} · ≈ ${e.cost_text} ${tr("по ценам каталога", "at catalogue prices")}`;
  }
  function html(m, i) {
    const st = m.bigdoc;
    if (!st) return "";
    const key = cardKey(m);
    const e = st.estimate;
    const unitPlural = (n) => (st.unit === "глава" ? plural(n, "глава", "главы", "глав") : st.unit === "часть" ? plural(n, "часть", "части", "частей")
      : st.unit === "chapter" ? (n === 1 ? "chapter" : "chapters") : (n === 1 ? "part" : "parts"));
    const head = `<div class="bd-card-head">${svg(ICON_BOOK)}<b>${esc(tr("Весь документ", "Whole document"))}</b><span class="bd-card-name" title="${esc(st.name)}">${esc(st.name)}</span></div>`;
    const plan = e ? `${num(e.parts)} ${esc(unitPlural(e.parts))} · ${num(e.requests)} ${esc(tr(plural(e.requests, "запрос", "запроса", "запросов"), e.requests === 1 ? "request" : "requests"))} · ${esc(pagesText({ pages: st.pages }))}` : "";
    let body = "";
    if (st.stage === "estimate") body = `<div class="bd-line muted"><span class="attach-spinner"></span>${esc(tr("Считаю объём и стоимость…", "Estimating size and cost…"))}</div>`;
    else if (st.stage === "confirm") body = `<div class="bd-line">${plan}</div><div class="bd-cost">${esc(costLine(e))}</div>
      ${e?.resumable ? `<div class="bd-line muted">${esc(tr(`Готовые части с прошлого раза: ${e.resumable} — их не придётся оплачивать снова`, `Parts finished last time: ${e.resumable} — they will not be paid for again`))}</div>` : ""}
      <div class="bd-actions"><button class="primary" data-bd-choice="${key}:go">${esc(tr("Запустить", "Start"))}</button>
      <button data-bd-choice="${key}:ask">${svg(ICON_ASK)}${esc(tr("Ответить по фрагментам", "Answer from fragments"))}</button>
      <button class="ghost" data-bd-stop>${esc(tr("Отмена", "Cancel"))}</button></div>`;
    else if (st.stage === "run") {
      const pct = st.total ? Math.round((100 * (st.done || 0)) / st.total) : 0;
      const unitCap = String(st.unit || "").replace(/^./, (c) => c.toUpperCase());
      const label = st.reducing ? st.label : (st.total ? `${unitCap} ${st.currentIndex || Math.min(st.total, (st.done || 0) + 1)} ${tr("из", "of")} ${st.total}` : "");

      const current = String(st.current || "").replace(/^\s*(?:глава|часть|раздел|chapter|part|section)\s+[\divxlc]+\s*[.:—–-]?\s*/iu, "");
      body = `<div class="bd-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${st.reducing ? 100 : pct}%"></i></div>
        <div class="bd-line"><b class="bd-label">${esc(label)}</b>${current && !st.reducing ? `<span class="bd-current" title="${esc(st.current)}">${esc(current)}</span>` : ""}<span class="grow"></span><span class="muted bd-count">${num(st.done || 0)}/${num(st.total || 0)}</span></div>
        <div class="bd-cost">${esc(costLine(e))}</div>${st.note ? `<div class="bd-line muted bd-note">${esc(st.note)}</div>` : ""}
        <div class="bd-actions"><button class="ghost" data-bd-stop>${svg(ICON_STOP)}${esc(tr("Остановить", "Stop"))}</button></div>`;
    } else if (st.stage === "done") body = `<div class="bd-line ok">${svg(ICON_CHECK)}${esc(tr(`Прочитан весь документ: ${num(st.total)} ${unitPlural(st.total)}`, `Read the whole document: ${num(st.total)} ${unitPlural(st.total)}`))}${st.cached ? ` · ${esc(tr("из кэша", "from the cache"))}` : st.elapsed ? ` · ${esc(fmtTime(st.elapsed))}` : ""}</div>`;
    else if (st.stage === "cancelled") body = `<div class="bd-line">${esc(st.done
        ? tr(`Остановлено: готово ${num(st.done)} из ${num(st.total || 0)}. Готовые части сохранены — «Продолжить» начнёт с места остановки.`, `Stopped: ${num(st.done)} of ${num(st.total || 0)} done. Finished parts are kept — “Resume” continues where it stopped.`)
        : tr("Остановлено до того, как была готова первая часть.", "Stopped before the first part was finished."))}</div>
      ${m.streaming ? "" : `<div class="bd-actions"><button class="primary" onclick="retryAnswerAt(${i})">${esc(st.done ? tr("Продолжить", "Resume") : tr("Начать заново", "Start again"))}</button></div>`}`;
    else if (st.stage === "error") body = `<div class="bd-line err">${esc(st.error || tr("Ошибка", "Error"))}</div>${st.done ? `<div class="bd-line muted">${esc(tr(`Готово ${num(st.done)} из ${num(st.total || 0)} — готовые части сохранены, повтор продолжит с места ошибки.`, `${num(st.done)} of ${num(st.total || 0)} done — finished parts are kept, a retry resumes from there.`))}</div>` : ""}
      ${m.streaming ? "" : `<div class="bd-actions"><button class="primary" onclick="retryAnswerAt(${i})">${esc(st.done ? tr("Продолжить", "Resume") : tr("Повторить", "Retry"))}</button></div>`}`;
    else if (st.stage === "skipped") body = `<div class="bd-line muted">${esc(tr("Ответ по найденным фрагментам вместо чтения всего документа", "Answered from the fragments found instead of reading the whole document"))}</div>`;
    return `<div class="bd-card bd-${esc(st.stage)}" data-bd-card="${key}">${head}${body}</div>`;
  }

  function pickCurrent(st) {
    const open = Object.keys(st.inflight || {}).map(Number).sort((x, y) => x - y);
    if (open.length) { st.currentIndex = open[0]; st.current = st.inflight[open[0]]; }
    else { st.currentIndex = 0; st.current = ""; }
  }
  function fmtTime(s) { return s >= 60 ? `${Math.floor(s / 60)} ${tr("мин", "min")} ${s % 60} ${tr("с", "s")}` : `${s} ${tr("с", "s")}`; }
  function patchCard(m) {
    const node = document.querySelector(`[data-bd-card="${m._bdKey}"]`);
    if (!node) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = html(m, +node.closest("[data-msg]")?.dataset.msg || 0);
    const fresh = wrap.firstElementChild;
    if (fresh) node.replaceWith(fresh);
  }
  document.addEventListener("click", (event) => {
    const choice = event.target.closest?.("[data-bd-choice]");
    if (choice) {
      const [key, value] = String(choice.dataset.bdChoice).split(":");
      const resolve = waiters.get(key);
      if (resolve) { waiters.delete(key); resolve(value); }
      return;
    }
    if (event.target.closest?.("[data-bd-stop]")) { document.querySelector("#btn-send")?.click(); return; }
    const cite = event.target.closest?.(".bd-cite");
    if (cite) { event.preventDefault(); openPage(cite.dataset.bdDoc, +cite.dataset.bdPage, cite.dataset.bdName || "", claimOf(cite)); }
  });

  const CITE_RE = /\[(≈)?\s*(стр\.?|страницы?|с\.|p\.|pp\.|page|pages)\s*(\d[\d,\s–\-—и]*?)(?:[,;]\s*(?:§|п\.|пп\.|para\.?|section|раздел|разд\.)[^\]\[]{0,40})?\]/gi;
  function pagesOf(body) {
    const out = [];
    for (const part of String(body).split(/[,;]|\sи\s/)) {
      const range = part.trim().match(/^(\d+)\s*[–\-—]\s*(\d+)$/);
      if (range) { const a = +range[1], b = +range[2]; if (a > 0 && b >= a && b - a <= 20) for (let p = a; p <= b; p += 1) out.push(p); }
      else if (/^\d+$/.test(part.trim())) out.push(+part.trim());
    }
    return out;
  }

  function linkCitations(htmlText, m) {
    const docs = m?.bigdocRefs?.docs || [];
    if (!docs.length || !/\[/.test(htmlText)) return htmlText;
    const parts = htmlText.split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>|<[^>]+>)/);
    return parts.map((part) => {
      if (!part || part.startsWith("<")) return part;
      return part.replace(CITE_RE, (whole, approx, word, body) => {
        const pages = pagesOf(body);
        if (!pages.length) return whole;
        const label = whole.match(/^\[(.*)\]$/)?.[1] || whole;
        const buttons = pages.map((page, index) => {
          const doc = docs.find((d) => !d.pages || page <= d.pages) || docs[0];
          const outside = page > (doc.pages || Infinity);
          const unverified = !outside && doc.retrieved && !doc.retrieved.includes(page);
          const text = pages.length === 1 ? label : index === 0 ? `${String(word)} ${page}` : String(page);
          const title = outside ? tr("В документе нет такой страницы", "The document has no such page")
            : unverified ? tr("Этой страницы не было среди найденных фрагментов — проверьте", "This page was not among the fragments found — double-check")
            : tr("Наведите — цитата, щёлкните — страница", "Hover for the quote, click for the page");
          return `<button type="button" class="bd-cite${outside ? " bad" : unverified ? " unverified" : ""}" data-bd-doc="${esc(doc.id)}" data-bd-page="${page}" data-bd-name="${esc(doc.name || "")}" data-bd-warn="${outside || unverified ? esc(title) : ""}" aria-label="${esc(`${text}: ${title}`)}">${esc(text)}</button>`;
        });
        return `<span class="bd-cites">${buttons.join("")}</span>`;
      });
    }).join("");
  }
  function claimOf(button) {

    const block = button.closest("p, li, td, blockquote, h1, h2, h3, h4") || button.parentElement;
    if (!block) return "";
    const range = document.createRange();
    range.setStart(block, 0);
    range.setEndBefore(button.closest(".bd-cites") || button);
    const text = range.toString();
    const cut = Math.max(text.lastIndexOf(". "), text.lastIndexOf("] "));
    return text.slice(Math.max(0, cut > text.length - 30 ? text.lastIndexOf(". ", cut - 1) : cut) + 1).slice(-400);
  }
  const quoteCache = new Map();
  async function quoteFor(id, page, claim) {
    const key = `${id}:${page}:${claim.slice(-160)}`;
    if (quoteCache.has(key)) return quoteCache.get(key);
    const promise = getJson(`${BASE}/quote`, { method: "POST", body: { id, page, claim, lang: EN() ? "en" : "ru" } });
    quoteCache.set(key, promise);
    promise.catch(() => quoteCache.delete(key));
    return promise;
  }
  let tip = null, tipFor = null, tipTimer = 0;

  function hideTip() { clearTimeout(tipTimer); if (tip) { tip.remove(); window.removeEventListener("scroll", hideTip, true); } tip = null; tipFor = null; }
  function placeTip(button) {
    if (!tip) return;
    const r = button.getBoundingClientRect();
    const w = Math.min(380, window.innerWidth - 16);
    tip.style.maxWidth = w + "px";
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
    tip.style.left = left + "px";
    const below = r.bottom + 8 + tip.offsetHeight < window.innerHeight;
    tip.style.top = (below ? r.bottom + 8 : Math.max(8, r.top - 8 - tip.offsetHeight)) + "px";
  }
  function showTip(button) {
    if (tipFor === button) return;
    hideTip();
    tipFor = button;
    tip = document.createElement("div");
    tip.className = "bd-tip";
    tip.setAttribute("role", "tooltip");
    const page = +button.dataset.bdPage;
    tip.innerHTML = `<div class="bd-tip-head">${esc(tr("Стр.", "P."))} ${page}<span>${esc(button.dataset.bdName || "")}</span></div><div class="bd-tip-quote muted">${esc(tr("Ищу цитату…", "Finding the quote…"))}</div>${button.dataset.bdWarn ? `<div class="bd-tip-warn">${esc(button.dataset.bdWarn)}</div>` : ""}`;
    document.body.appendChild(tip);
    window.addEventListener("scroll", hideTip, { capture: true, passive: true });
    placeTip(button);
    quoteFor(button.dataset.bdDoc, page, claimOf(button)).then((q) => {
      if (tipFor !== button || !tip) return;
      const quote = tip.querySelector(".bd-tip-quote");
      if (!q.exists) { quote.textContent = tr("В документе нет такой страницы.", "The document has no such page."); quote.classList.add("err"); }
      else if (!q.quote) quote.textContent = tr("На этой странице нет текста.", "This page has no text.");
      else {
        quote.classList.remove("muted");
        quote.innerHTML = (EN() ? `“${esc(q.quote)}”` : `«${esc(q.quote)}»`) + (q.score < 0.18 ? `<small>${esc(tr("Совпадение слабое — откройте страницу и проверьте", "A weak match — open the page and check"))}</small>` : "");
      }
      tip.insertAdjacentHTML("beforeend", `<div class="bd-tip-foot">${esc(tr("Щёлкните, чтобы открыть страницу", "Click to open the page"))}${q.label ? ` · ${esc(tr("в книге", "printed"))}: ${esc(q.label)}` : ""}${q.approx ? ` · ${esc(tr("номер приблизительный", "approximate number"))}` : ""}</div>`);
      placeTip(button);
    }).catch((error) => {
      if (tipFor !== button || !tip) return;
      tip.querySelector(".bd-tip-quote").textContent = error.status === 410 || error.status === 404
        ? tr("Документа больше нет в кэше — прикрепите его снова.", "The document is no longer cached — attach it again.") : String(error.message || error);
    });
  }
  document.addEventListener("mouseover", (event) => {
    const button = event.target.closest?.(".bd-cite");
    if (button) { clearTimeout(tipTimer); tipTimer = setTimeout(() => showTip(button), 120); }
    else if (tip && !event.target.closest?.(".bd-tip")) { clearTimeout(tipTimer); tipTimer = setTimeout(hideTip, 160); }
  });
  document.addEventListener("focusin", (event) => { const b = event.target.closest?.(".bd-cite"); if (b) showTip(b); });
  document.addEventListener("focusout", (event) => { if (event.target.closest?.(".bd-cite")) hideTip(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && tip) hideTip(); });

  function highlight(text, quote) {
    const safe = esc(text);
    if (!quote) return safe;
    const q = esc(quote).trim();
    const at = safe.indexOf(q);
    return at < 0 ? safe : safe.slice(0, at) + `<mark>${q}</mark>` + safe.slice(at + q.length);
  }
  async function openPage(id, page, name, claim = "") {
    hideTip();
    if (typeof panel !== "function") return;
    panel(`<div class="bd-viewer"><h2>${esc(name || tr("Документ", "Document"))}</h2><p class="sub bd-viewer-sub">${esc(tr("Загружаю страницу…", "Loading the page…"))}</p><div class="bd-viewer-body"></div></div>`);
    await showPageIn(id, page, claim);
  }
  async function showPageIn(id, page, claim = "") {
    const sub = document.querySelector(".bd-viewer-sub"), body = document.querySelector(".bd-viewer-body");
    if (!body) return;
    try {
      const [p, q] = await Promise.all([getJson(`${BASE}/doc/${id}/page/${page}`), claim ? quoteFor(id, page, claim).catch(() => null) : null]);
      sub.innerHTML = `${esc(tr("Страница", "Page"))} ${p.n} ${esc(tr("из", "of"))} ${num(p.pages)}${p.label ? ` · ${esc(tr("в книге", "printed"))} ${esc(p.label)}` : ""}${p.approx ? ` · ${esc(tr("приблизительно", "approximate"))}` : ""}`;
      body.innerHTML = `<div class="bd-pager"><button class="pbtn ghost" data-bd-goto="${p.n - 1}" ${p.n <= 1 ? "disabled" : ""}>‹ ${esc(tr("Назад", "Previous"))}</button>
        <label>${esc(tr("Стр.", "P."))} <input type="number" min="1" max="${p.pages}" value="${p.n}" data-bd-goto-input></label>
        <button class="pbtn ghost" data-bd-goto="${p.n + 1}" ${p.n >= p.pages ? "disabled" : ""}>${esc(tr("Вперёд", "Next"))} ›</button></div>
        <pre class="bd-page-text">${highlight(p.text || tr("(на странице нет текста)", "(no text on this page)"), q?.quote || "")}</pre>`;
      body.querySelectorAll("[data-bd-goto]").forEach((b) => b.onclick = () => showPageIn(id, +b.dataset.bdGoto));
      const input = body.querySelector("[data-bd-goto-input]");
      input.onchange = () => { const n = Math.max(1, Math.min(p.pages, +input.value || 1)); showPageIn(id, n); };
      body.querySelector("mark")?.scrollIntoView({ block: "center" });
    } catch (error) {
      sub.textContent = String(error.message || error);
    }
  }
  async function preview(item) {
    const doc = item.bigdoc;
    if (!doc || typeof panel !== "function") return;
    const toc = (doc.toc || []).slice(0, 60);
    panel(`<div class="bd-viewer"><h2>${esc(item.name)}</h2><p class="sub">${esc(metaText(item))}${doc.chapters ? ` · ${num(doc.chapters)} ${esc(tr(plural(doc.chapters, "раздел", "раздела", "разделов"), doc.chapters === 1 ? "section" : "sections"))}` : ""}</p>
      ${toc.length ? `<div class="bd-toc">${toc.map((row) => `<button data-bd-toc="${row.page}"><span>${esc(row.title)}</span><em>${esc(tr("стр.", "p."))} ${row.page}</em></button>`).join("")}</div>` : ""}
      <div class="bd-viewer-sub sub"></div><div class="bd-viewer-body"></div>
      <div class="row-end"><button class="pbtn ghost" onclick="closePanel()">${esc(tr("Закрыть", "Close"))}</button></div></div>`);
    document.querySelectorAll("[data-bd-toc]").forEach((b) => b.onclick = () => showPageIn(doc.id, +b.dataset.bdToc));
    showPageIn(doc.id, 1);
  }

  window.MemuBigDocs = { takes, attach, chipHtml, wireChips, contextBlocks, systemNote, linkCitations, html, preview, openPage, _pagesOf: pagesOf };
})();
