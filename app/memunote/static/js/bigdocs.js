

import { api, esc, I18N, toast } from "./core.js";

const tr = (ru, en) => (I18N.lang === "en" ? en : ru);
const DOC_RE = /\.(pdf|docx|epub|fb2)$/i;
const docs = new Map();

function tabOf(panel) { return panel.ctx.editor?.()?.tab || null; }
function limitOf(panel) { return Math.min(+panel.ctx.settings?.().ai_context_chars || 60000, 60000); }

export function applies(panel, snap) {
  const tab = tabOf(panel);
  if (!tab?.path || !DOC_RE.test(tab.path) || !snap) return false;
  return tab.kind === "pdf" || !String(snap.text || "").trim() || (snap.total || 0) > limitOf(panel);
}

async function call(op, body) {
  const res = await api("bigdocs", { op, ...body });
  if (!res.ok) { const e = new Error(res.error || "error"); e.data = res; throw e; }
  return res;
}
async function ensureDoc(path) {
  if (docs.has(path)) return docs.get(path);
  const res = await call("ingest", { path });
  docs.set(path, res.doc);
  return res.doc;
}

export function bar(panel, box, snap) {
  if (!box || !applies(panel, snap)) return;
  const tab = tabOf(panel), info = docs.get(tab.path);
  const mode = panel.bigdocMode === "whole" ? "whole" : "ask";
  const pages = info ? `${info.approx_pages ? "≈" : ""}${info.pages} ${tr("стр.", "pages")}` : tr("большой документ", "big document");
  box.insertAdjacentHTML("beforeend", `<span class="mnbd-bar" role="radiogroup" title="${esc(tr("Документ больше окна контекста: вопросы идут через поиск по всему файлу", "The document is bigger than the context window: questions go through a search of the whole file"))}">
    <span class="mnbd-pages">📄 ${esc(pages)}</span>
    <button role="radio" aria-checked="${mode === "ask"}" class="${mode === "ask" ? "on" : ""}" data-mnbd="ask">${esc(tr("Спросить", "Ask"))}</button>
    <button role="radio" aria-checked="${mode === "whole"}" class="${mode === "whole" ? "on" : ""}" data-mnbd="whole">${esc(tr("Весь документ", "Whole document"))}</button></span>`);
  box.querySelectorAll("[data-mnbd]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); panel.bigdocMode = b.dataset.mnbd; panel.renderScope(); });
  if (!info) ensureDoc(tab.path).then(() => panel.renderScope()).catch(() => {});
}

function fakeSnap(snap, text, name) {
  return { ...snap, text, total: text.length, selFrom: text.length, selTo: text.length, cursor: text.length, kind: "text", doc: null, offset: 0, name };
}

export async function answer(panel, question, snap, model) {
  const tab = tabOf(panel);
  const bubble = panel.assistantBubble();
  const body = bubble.querySelector(".msg-body");
  const say = (html) => { body.innerHTML = html; panel.scrollDown?.(); };
  try {
    say(`<span class="muted small">${esc(tr("Читаю документ целиком…", "Reading the whole document…"))}</span>`);
    const info = await ensureDoc(tab.path);
    let block = "";
    if (panel.bigdocMode === "whole") {
      block = await whole(panel, info, question, model, say);
      if (block === null) { bubble.remove(); return; }
      panel.bigdocRefs = { id: info.id, name: info.name, pages: info.pages, retrieved: null };
    } else {
      const found = await call("search", { id: info.id, query: question, lang: I18N.lang, model });
      block = found.block + "\n\n" + found.instruction;
      panel.bigdocRefs = { id: info.id, name: info.name, pages: info.pages, retrieved: found.pages };
    }
    bubble.remove();
    panel.runAnswer({ action: "chat" }, fakeSnap(snap, block, info.name), "document");
  } catch (error) {
    say(`<span class="mnbd-err">${esc(error.message || String(error))}</span>`);
  }
}

async function whole(panel, info, question, model, say) {
  const est = (await call("estimate", { id: info.id, model, prompt: question, lang: I18N.lang })).estimate;
  if (est.needs_confirm) {
    const cost = est.price_known ? `≈ ${est.cost_text}` : tr("цена неизвестна", "price unknown");
    say(`<div class="mnbd-card"><b>${esc(tr("Весь документ", "Whole document"))}</b> · ${esc(info.name)}
      <div class="muted small">${esc(tr(`${est.parts} частей · ${est.requests} запросов · ≈${Math.round((est.input_tokens + est.output_tokens) / 1000)} тыс. токенов · ${cost}`, `${est.parts} parts · ${est.requests} requests · ≈${Math.round((est.input_tokens + est.output_tokens) / 1000)}K tokens · ${cost}`))}</div>
      <div class="mnbd-actions"><button class="btn sm primary" data-go>${esc(tr("Запустить", "Start"))}</button><button class="btn sm ghost" data-no>${esc(tr("Отмена", "Cancel"))}</button></div></div>`);
    const go = await new Promise((resolve) => {
      panel.log.querySelector("[data-go]").onclick = () => resolve(true);
      panel.log.querySelector("[data-no]").onclick = () => resolve(false);
    });
    if (!go) return null;
  }
  const started = await call("whole", { id: info.id, model, prompt: question, lang: I18N.lang, confirmed: true });
  let state = started.job, since = 0, stopped = false;
  const card = (s) => {
    const pct = s.total ? Math.round(100 * s.done / s.total) : 0;
    const label = s.stage === "reduce" || (s.total && s.done >= s.total) ? tr("Свожу всё вместе…", "Putting it all together…")
      : `${s.unit || tr("часть", "part")} ${Math.min(s.total, s.done + 1)} ${tr("из", "of")} ${s.total}`;
    say(`<div class="mnbd-card"><b>${esc(tr("Весь документ", "Whole document"))}</b> · ${esc(info.name)}
      <div class="mnbd-progress"><i style="width:${pct}%"></i></div><div class="small"><b>${esc(label)}</b> <span class="muted">${s.done}/${s.total}</span></div>
      <div class="mnbd-actions"><button class="btn sm ghost" data-stop>${esc(tr("Остановить", "Stop"))}</button></div></div>`);
    const stop = panel.log.querySelector("[data-stop]");
    if (stop) stop.onclick = () => { stopped = true; call("cancel", { job: state.id }).catch(() => {}); };
  };
  card(state);
  while (state.status === "running") {
    await new Promise((r) => setTimeout(r, 1200));
    const res = await call("job", { job: state.id, since });
    since = res.next; state = res.state;
    card(state);
  }
  if (state.status === "done") return `<document name="${esc(info.name)}" pages="${info.pages}" mode="whole-document-summary">\n${state.result}\n</document>\n`
    + tr("Это чтение ВСЕХ страниц по частям, сведённое под вопрос. Отвечай по нему и сохраняй ссылки [стр. N].", "This is a part-by-part reading of ALL pages combined for the question. Answer from it and keep the [p. N] references.");
  if (state.status === "cancelled" || stopped) {
    toast(tr("Остановлено — готовые части сохранены, повторный запуск продолжит", "Stopped — finished parts are kept, a new run resumes"), { kind: "info" });
    return null;
  }
  throw new Error(state.error || tr("Ошибка", "Error"));
}

const CITE_RE = /\[(≈)?\s*(стр\.?|с\.|p\.|page)\s*(\d+(?:\s*[,–-]\s*\d+)*)(?:[,;]\s*(?:§|п\.|пп\.|para\.?|section|раздел|разд\.)[^\]\[]{0,40})?\]/gi;
const CITE_TEST = new RegExp(CITE_RE.source, "i");
export function decorate(body, refs) {
  if (!refs || !body) return;
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (CITE_TEST.test(walker.currentNode.nodeValue) && !walker.currentNode.parentElement.closest("code, pre, .mnbd-cite")) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const html = esc(node.nodeValue).replace(CITE_RE, (whole, _a, _w, list) => {
      const first = parseInt(list, 10);
      const outside = first > (refs.pages || Infinity);
      const unverified = !outside && refs.retrieved && !refs.retrieved.includes(first);
      return `<button type="button" class="mnbd-cite${outside ? " bad" : unverified ? " unverified" : ""}" data-page="${first}">${whole.slice(1, -1)}</button>`;
    });
    const span = document.createElement("span");
    span.innerHTML = html;
    node.replaceWith(span);
  }
  body.querySelectorAll(".mnbd-cite").forEach((b) => {
    b.onmouseenter = async () => {
      if (b.title) return;
      b.title = tr("Ищу цитату…", "Finding the quote…");
      const claim = (b.closest("p, li") || body).textContent.slice(0, 400);
      try {
        const q = await call("quote", { id: refs.id, page: +b.dataset.page, claim });
        b.title = !q.exists ? tr("В документе нет такой страницы", "The document has no such page") : q.quote ? `«${q.quote}»` : tr("На странице нет текста", "No text on the page");
      } catch (e) { b.title = e.message; }
    };
    b.onclick = async () => {
      try {
        const p = await call("page", { id: refs.id, page: +b.dataset.page });
        const text = p.text.length > 1500 ? p.text.slice(0, 1500) + "…" : p.text;
        toast(`${tr("Стр.", "P.")} ${p.n}: ${text}`, { kind: "info", ms: 12000 });
      } catch (e) { toast(e.message, { kind: "error" }); }
    };
  });
}
