/* MemuAI Proxy — Round 40 (r40-proxy): web versions of models («<model>:web»).

   A web model is an ordinary model id for any program (OpenAI / Anthropic API) that searches the web on the Proxy side
   (app/web_models.py). It is optional, so the UI stays out of the way: a folded «Веб-версия» section at the bottom
   of the model card (model_caps.js calls MemuWebModels.cardBlock(card)) and a list in «Каталог моделей → Дополнительно».
   API: GET /api/admin/web-models, POST …/save | …/delete | …/try (app/web_models_api.py).
   Uses app.js globals: LANG, esc, api, toast, openModal, closeModal, confirmDialog, copyText. */
"use strict";

window.MemuWebModels = (() => {
  const L = (ru, en) => (typeof LANG !== "undefined" && LANG === "en" ? en : ru);
  const S = { data: null, loading: null };
  const ICON_GLOBE = '<svg class="wm-ic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.3 2.4 3.5 5.2 3.5 8.5s-1.2 6.1-3.5 8.5c-2.3-2.4-3.5-5.2-3.5-8.5s1.2-6.1 3.5-8.5z"/></svg>';
  const LANGS = [["", "авто (как у пользователя)", "auto (as the user writes)"], ["ru", "русский", "Russian"], ["en", "английский", "English"],
    ["de", "немецкий", "German"], ["fr", "французский", "French"], ["es", "испанский", "Spanish"], ["zh", "китайский", "Chinese"]];
  const REGIONS = [["", "любой", "any"], ["ru", "Россия", "Russia"], ["us", "США", "USA"], ["eu", "Европа", "Europe"],
    ["uk", "Великобритания", "United Kingdom"], ["de", "Германия", "Germany"], ["cn", "Китай", "China"]];
  const TOOLS = { read_url: ["Чтение страниц по ссылке", "Read pages by link"], current_time: ["Текущие дата и время", "Current date and time"],
    calculator: ["Калькулятор", "Calculator"] };

  async function load(force = false) {
    if (S.data && !force) return S.data;
    if (!S.loading) {
      S.loading = api("/api/admin/web-models").then((data) => { S.data = data; return data; })
        .finally(() => { S.loading = null; });
    }
    return S.loading;
  }
  const findByBase = (base) => ((S.data && S.data.models) || []).find((m) => m.base === base);
  const findById = (id) => ((S.data && S.data.models) || []).find((m) => m.id === id);

  /* ------------------------------------------------------------ model card */
  function cardBlock(card) {
    if (!card || !card.id || String(card.id).endsWith(":web")) return "";
    setTimeout(() => fillCard(card.id), 0);
    return `<details class="mcap-sec wm-card" data-wm-base="${esc(card.id)}"><summary>${ICON_GLOBE}<span class="wm-card-t">${esc(L("Веб-версия", "Web version"))}</span>
      <span class="wm-card-sub">${esc(L("дополнительно · поиск в интернете для любых программ", "advanced · web search for any program"))}</span><span class="wm-card-state"></span></summary>
      <div class="wm-card-body"><div class="wm-muted">${esc(L("Загрузка…", "Loading…"))}</div></div></details>`;
  }

  async function fillCard(base) {
    const box = document.querySelector(`.wm-card[data-wm-base="${CSS.escape(base)}"]`);
    if (!box) return;
    try { await load(); } catch (e) { box.querySelector(".wm-card-body").innerHTML = `<div class="wm-muted">${esc(e.message || String(e))}</div>`; return; }
    const row = findByBase(base);
    box.querySelector(".wm-card-state").innerHTML = row ? `<span class="chip ok">${esc(L("создана", "created"))}</span>` : "";
    box.querySelector(".wm-card-body").innerHTML = row ? `
      <div class="wm-idrow"><code>${esc(row.id)}</code><button type="button" class="btn small" data-wm-act="copy" data-wm-id="${esc(row.id)}">${esc(L("Копировать", "Copy"))}</button></div>
      <p class="wm-muted">${esc(summary(row))}</p>
      <div class="wm-actions"><button type="button" class="btn small primary" data-wm-act="edit" data-wm-id="${esc(row.id)}">${esc(L("Настроить", "Settings"))}</button>
        <button type="button" class="btn small" data-wm-act="try" data-wm-id="${esc(row.id)}">${esc(L("Проверить", "Test"))}</button>
        <div class="grow"></div><button type="button" class="btn small danger" data-wm-act="delete" data-wm-id="${esc(row.id)}">${esc(L("Удалить", "Delete"))}</button></div>` : `
      <p class="wm-muted">${esc(L("Создаёт модель", "Creates the model"))} <code>${esc(base)}:web</code>. ${esc(L("Её видят все программы в /v1/models: Proxy сам ищет в интернете, читает страницы и отдаёт обычный ответ с источниками — в форматах OpenAI и Anthropic. Исходная модель не меняется; веб-версию можно удалить в любой момент.",
        "Every program sees it in /v1/models: the Proxy searches the web, reads pages and returns an ordinary answer with sources — in the OpenAI and Anthropic formats. The original model stays as it is; the web version can be deleted at any time."))}</p>
      <div class="wm-actions"><button type="button" class="btn small primary" data-wm-act="create" data-wm-base="${esc(base)}">${ICON_GLOBE}${esc(L("Создать веб-версию", "Create a web version"))}</button></div>`;
  }

  function summary(row) {
    const s = row.settings || {};
    const engines = (S.data && S.data.engines) || [];
    const engine = s.engine ? (engines.find((e) => e.id === s.engine) || {}) : null;
    const engineName = engine ? L(engine.label, engine.label_en) : L("поиск из настроек Proxy", "the Proxy search setting");
    const mode = s.mode === "presearch" || (s.mode === "auto" && !row.native_tools)
      ? L("сначала поиск, потом ответ", "search first, then answer") : L("модель сама решает, когда искать", "the model decides when to search");
    return [engineName, `${s.results} ${L("результатов", "results")}`, `${L("до", "up to")} ${s.max_steps} ${L("шагов", "steps")}`,
      s.read_pages ? L("читает страницы", "reads pages") : "", s.citations ? L("с источниками", "with sources") : "", mode].filter(Boolean).join(" · ");
  }

  /* ------------------------------------------------------------ settings dialog */
  function opt(list, value) {
    return list.map(([v, ru, en]) => `<option value="${esc(v)}" ${String(value || "") === v ? "selected" : ""}>${esc(L(ru, en))}</option>`).join("");
  }

  async function edit(base, id) {
    try { await load(); } catch (e) { toast(e.message, "err"); return; }
    const row = id ? findById(id) : null;
    base = row ? row.base : base;
    const s = { ...(S.data.defaults || {}), ...((row && row.settings) || {}) };
    const engines = S.data.engines || [];
    const proxyEngine = engines.find((e) => e.id === S.data.proxy_engine);
    const engineOpts = [`<option value="" title="${proxyEngine ? esc(L(proxyEngine.label, proxyEngine.label_en)) : ""}">${esc(L("Из настроек Proxy", "From the Proxy settings"))}</option>`]
      .concat(engines.map((e) => `<option value="${esc(e.id)}" ${s.engine === e.id ? "selected" : ""}>${esc(L(e.label, e.label_en))}</option>`)).join("");
    const tools = Object.keys(TOOLS).map((k) => `<label class="wm-check"><input type="checkbox" data-wm-tool="${k}" ${(s.tools || []).includes(k) ? "checked" : ""}>${esc(L(TOOLS[k][0], TOOLS[k][1]))}</label>`).join("");
    openModal(`<h3 class="wm-dlg-h">${ICON_GLOBE}${esc(row ? L("Веб-модель", "Web model") : L("Новая веб-версия", "New web version"))}</h3>
      <p class="wm-muted wm-dlg-id"><code>${esc(base)}:web</code></p>
      <form class="wm-form" id="wm-form">
        <div class="wm-grid">
          <label class="wm-field"><span>${esc(L("Поисковик", "Search engine"))}</span><select name="engine">${engineOpts}</select></label>
          <label class="wm-field"><span>${esc(L("Как искать", "How to search"))}</span><select name="mode">
            ${opt([["auto", "Авто", "Auto"], ["agent", "Модель решает сама (инструменты)", "The model decides (tools)"], ["presearch", "Сначала поиск, потом ответ", "Search first, then answer"]], s.mode)}</select></label>
          <label class="wm-field"><span>${esc(L("Результатов в поиске", "Results per search"))}</span><input type="number" name="results" min="1" max="10" value="${esc(s.results)}"></label>
          <label class="wm-field"><span>${esc(L("Шагов поиска (глубина)", "Search steps (depth)"))}</span><input type="number" name="max_steps" min="1" max="8" value="${esc(s.max_steps)}"></label>
          <label class="wm-field"><span>${esc(L("Язык", "Language"))}</span><select name="lang">${opt(LANGS, s.lang)}</select></label>
          <label class="wm-field"><span>${esc(L("Регион источников", "Region of sources"))}</span><select name="region">${opt(REGIONS, s.region)}</select></label>
          <label class="wm-field"><span>${esc(L("Ход поиска", "Search progress"))}</span><select name="progress">
            ${opt([["off", "Не показывать", "Hidden"], ["thinking", "В размышлениях", "In reasoning"], ["text", "В тексте ответа", "In the answer text"]], s.progress)}</select></label>
          <label class="wm-field"><span>${esc(L("Страниц читать за поиск", "Pages to read per search"))}</span><input type="number" name="pages" min="1" max="4" value="${esc(s.pages)}"></label>
        </div>
        <div class="wm-checks">
          <label class="wm-check"><input type="checkbox" name="read_pages" ${s.read_pages !== false ? "checked" : ""}>${esc(L("Читать лучшие страницы, а не только сниппеты", "Read the best pages, not only snippets"))}</label>
          <label class="wm-check"><input type="checkbox" name="citations" ${s.citations !== false ? "checked" : ""}>${esc(L("Ссылки [1] в тексте и список источников", "[1] links in the text and a list of sources"))}</label>
        </div>
        <div class="wm-sub-h">${esc(L("Другие инструменты", "Other tools"))}</div>
        <div class="wm-checks">${tools}</div>
        <p class="wm-muted">${esc(L("«Авто»: если модель умеет вызывать инструменты, она сама решает, что и сколько раз искать; иначе Proxy ищет по сообщению пользователя и передаёт ей результаты.", "“Auto”: a model with tool calls decides what and how often to search itself; otherwise the Proxy searches by the user's message and hands it the results."))}</p>
        <div class="row-end"><button type="button" class="btn" id="wm-cancel">${esc(L("Отмена", "Cancel"))}</button>
          <button type="submit" class="btn primary">${esc(row ? L("Сохранить", "Save") : L("Создать", "Create"))}</button></div>
      </form>`, true);
    document.getElementById("wm-cancel").onclick = closeModal;
    document.getElementById("wm-form").onsubmit = async (event) => {
      event.preventDefault();
      const f = event.target;
      const settings = {
        engine: f.engine.value, mode: f.mode.value, results: Number(f.results.value), max_steps: Number(f.max_steps.value),
        lang: f.lang.value, region: f.region.value, progress: f.progress.value, pages: Number(f.pages.value),
        read_pages: f.read_pages.checked, citations: f.citations.checked,
        tools: Array.from(f.querySelectorAll("[data-wm-tool]")).filter((x) => x.checked).map((x) => x.dataset.wmTool),
      };
      try {
        const res = await api("/api/admin/web-models/save", { method: "POST", body: { base, settings } });
        closeModal();
        toast(row ? L("Сохранено", "Saved") : `${L("Создана", "Created")}: ${res.model.id}`);
        await refresh();
      } catch (e) { toast(e.message, "err"); }
    };
  }

  async function remove(id) {
    confirmDialog(L("Удалить веб-модель?", "Delete the web model?"), `${id} — ${L("исходная модель останется.", "the original model stays.")}`, async () => {
      try { await api("/api/admin/web-models/delete", { method: "POST", body: { id } }); toast(L("Удалено", "Deleted")); await refresh(); }
      catch (e) { toast(e.message, "err"); }
    });
  }

  async function test(id) {
    const ask = L("Какая сейчас последняя стабильная версия Python? Ответь кратко.", "What is the latest stable Python version? Answer briefly.");
    openModal(`<h3 class="wm-dlg-h">${ICON_GLOBE}${esc(L("Проверка", "Test"))} <code class="wm-dlg-code">${esc(id)}</code></h3>
      <form class="wm-form" id="wm-try"><label class="wm-field"><span>${esc(L("Вопрос", "Question"))}</span><input type="text" name="text" value="${esc(ask)}"></label>
      <div class="wm-try-out wm-muted" id="wm-try-out">${esc(L("Настоящий запрос к модели: тратит её квоту.", "A real request to the model: it uses its quota."))}</div>
      <div class="row-end"><button type="button" class="btn" id="wm-try-close">${esc(L("Закрыть", "Close"))}</button><button type="submit" class="btn primary">${esc(L("Спросить", "Ask"))}</button></div></form>`, true);
    document.getElementById("wm-try-close").onclick = closeModal;
    document.getElementById("wm-try").onsubmit = async (event) => {
      event.preventDefault();
      const out = document.getElementById("wm-try-out");
      const button = event.target.querySelector("[type=submit]");
      button.disabled = true;
      out.textContent = L("Ищу и отвечаю…", "Searching and answering…");
      try {
        const res = await api("/api/admin/web-models/try", { method: "POST", body: { id, text: event.target.text.value } });
        out.classList.remove("wm-muted");
        out.innerHTML = `<div class="wm-try-meta">${esc((res.ms / 1000).toFixed(1))} ${esc(L("с", "s"))} · ${esc(String((res.citations || []).length))} ${esc(L("источн.", "sources"))}</div><pre class="wm-try-text"></pre>`;
        out.querySelector(".wm-try-text").textContent = res.text || "—";
      } catch (e) { out.textContent = e.message; }
      button.disabled = false;
    };
  }

  /* ------------------------------------------------------------ «Каталог моделей → Дополнительно» list */
  async function renderList() {
    const box = document.getElementById("wm-list");
    if (!box) return;
    try { await load(); } catch (e) { box.innerHTML = `<span class="wm-muted">${esc(e.message)}</span>`; return; }
    const rows = S.data.models || [];
    box.innerHTML = `<span class="models-more-label">${esc(L("Веб-модели", "Web models"))}</span>` + (rows.length
      ? `<div class="wm-list">${rows.map((m) => `<div class="wm-item${m.base_exists ? "" : " gone"}">${ICON_GLOBE}<code title="${esc(summary(m))}">${esc(m.id)}</code>
          ${m.base_exists ? "" : `<span class="chip warn">${esc(L("нет исходной модели", "base model gone"))}</span>`}
          <button type="button" class="btn small" data-wm-act="edit" data-wm-id="${esc(m.id)}" title="${esc(L("Настроить", "Settings"))}">${esc(L("Настроить", "Settings"))}</button>
          <button type="button" class="btn small danger" data-wm-act="delete" data-wm-id="${esc(m.id)}" aria-label="${esc(L("Удалить", "Delete"))} ${esc(m.id)}">✕</button></div>`).join("")}</div>`
      : `<span class="wm-muted">${esc(L("Пока нет. Откройте карточку модели (ⓘ) → «Веб-версия».", "None yet. Open a model card (ⓘ) → “Web version”."))}</span>`);
  }

  async function refresh() {
    await load(true).catch(() => null);
    renderList();
    document.querySelectorAll(".wm-card[data-wm-base]").forEach((box) => fillCard(box.dataset.wmBase));
  }

  document.addEventListener("click", (event) => {
    const el = event.target.closest && event.target.closest("[data-wm-act]");
    if (!el) return;
    const act = el.dataset.wmAct;
    if (act === "create") edit(el.dataset.wmBase, "");
    else if (act === "edit") edit("", el.dataset.wmId);
    else if (act === "delete") remove(el.dataset.wmId);
    else if (act === "try") test(el.dataset.wmId);
    else if (act === "copy" && typeof copyText === "function") copyText(el.dataset.wmId);
  });
  /* The list loads only when «Дополнительно» is opened — nothing runs on start. */
  const more = document.getElementById("models-more");
  if (more) more.addEventListener("toggle", () => { if (more.open) renderList(); });

  return { cardBlock, refresh };
})();
