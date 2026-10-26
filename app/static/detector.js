

"use strict";

window.MemuDetector = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const S = { models: [], families: [], run: null, timer: null, history: [], picked: "", viewing: "", prov: null };

  const VERDICT = {
    genuine: ["ok", "✓", () => L("Похоже на заявленную модель", "Looks like the claimed model"),
      () => L("Наиболее вероятная версия принадлежит заявленному семейству, и она заметно опережает «недостаточно данных».", "The leading candidate belongs to the claimed family, and clearly outweighs “not enough evidence”.")],
    mixed: ["warn", "≈", () => L("Признаки противоречат друг другу", "Mixed signals"),
      () => L("Заявленное семейство заметно, но не преобладает уверенно. Возможен системный промпт, прокси-обёртка, другая версия — или просто мало сигналов.", "The claimed family is present but does not clearly dominate. A system prompt, a wrapper, another version — or simply thin evidence — are all possible.")],
    substituted: ["invalid", "!", () => L("Вероятна подмена", "Likely substituted"),
      () => L("Ответы уверенно указывают на другое семейство, чем заявлено в названии.", "The answers point clearly to a different family than the name claims.")],
    identified: ["info", "i", () => L("Семейство определено", "Family identified"),
      () => L("В названии модели нет семейства, поэтому сравнивать не с чем — показано наиболее вероятное.", "The model name names no family, so there is nothing to compare — the most likely one is shown.")],
    unknown: ["unknown", "?", () => L("Не удалось определить", "Could not tell"),
      () => L("Модель не выдала узнаваемых признаков. Попробуйте полную проверку или ИИ-анализ.", "The model gave no recognisable signals. Try the full check or AI analysis.")],
    family_match: ["ok", "✓", () => L("Семейство совпадает, версия не опровергнута", "Right family, version not contradicted"),
      () => L("Ответы уверенно указывают на заявленное семейство. Конкретную версию проверить точно нельзя — ниже видно, какие версии подходят к фактам.",
        "The answers clearly point to the claimed family. The exact version cannot be verified precisely — see below which versions fit the facts.")],
    version_doubt: ["warn", "≈", () => L("Семейство совпадает, но версия под вопросом", "Right family, but the version is doubtful"),
      () => L("Это заявленное семейство, но дата знаний и остальные признаки лучше подходят к другой версии — см. блок «Версия».",
        "This is the claimed family, but the knowledge window and other signals fit another version better — see “Version”.")],
    version_mismatch: ["warn", "≠", () => L("Семейство совпадает, версия — вероятно, другая", "Right family, probably a different version"),
      () => L("Ответы похожи на заявленное семейство, но факты о версии (дата знаний, способности, самоотчёт) указывают на другую модель этого семейства.",
        "The answers fit the claimed family, but the version evidence (knowledge window, ability, self-report) points to another model of that family.")],
  };

  const VERSION = {
    confirmed: ["ok", () => L("Версия подтверждается", "Version confirmed")],
    consistent: ["info", () => L("Версия не опровергнута", "Version not contradicted")],
    unrecognized: ["unknown", () => L("Версия не распознана", "Version not recognised")],
    other_family: ["warn", () => L("Заявлена модель другого семейства", "A model of another family is claimed")],
    older_suspected: ["invalid", () => L("Вероятно, более старая версия", "Probably an older version")],
    newer_than_claimed: ["warn", () => L("Отвечает более новая модель", "A newer model is answering")],
    different_suspected: ["warn", () => L("Модель называет другую версию", "The model names another version")],
    likely_other: ["warn", () => L("Вероятнее другая версия", "Another version is more likely")],
    weaker_suspected: ["invalid", () => L("Похоже на более слабую модель", "Looks like a weaker model")],
  };
  const REASON_ICON = { ok: "✓", info: "i", warn: "!", bad: "✕" };
  const CONF = { high: () => L("высокая", "high"), medium: () => L("средняя", "medium"), low: () => L("низкая", "low") };
  const CATEGORY = {
    identity: () => L("Самоидентификация", "Self-identification"),
    behavioral: () => L("Токенизатор и служебные токены", "Tokenizer & special tokens"),
    cutoff: () => L("Дата знаний", "Knowledge cutoff"),
    consistency: () => L("Согласованность", "Consistency"),
    specs: () => L("Характеристики", "Specs"),
    reasoning: () => L("Рассуждение", "Reasoning"),
  };
  const PROBE = {
    identity_en: () => L("Кто ты? (англ.)", "Who are you? (EN)"), identity_ru: () => L("Кто ты? (рус.)", "Who are you? (RU)"),
    identity_base: () => L("Базовая модель", "Base model"), identity_complete: () => L("Дополни фразу", "Complete the sentence"),
    identity_version: () => L("Точная версия", "Exact version"), identity_repeat: () => L("Повтор вопроса «Кто ты?»", "Repeat of “Who are you?”"),
    tokens: () => L("Служебные токены", "Special tokens"), cutoff_self: () => L("Дата знаний (сама о себе)", "Cutoff (self-report)"),
    quiz: () => L("Викторина по датам", "Dated quiz"), specs: () => L("Контекст и вывод (сама о себе)", "Context & output (self-report)"),
    judge: () => L("ИИ-анализ", "AI analysis"), capability: () => L("Задачи на рассуждение", "Reasoning tasks"),
    tokenizer_base: () => L("Токенизатор: пустой запрос", "Tokenizer: baseline"), tokenizer_prose: () => L("Токенизатор: текст и код", "Tokenizer: prose and code"),
    tokenizer_symbols: () => L("Токенизатор: цифры и символы", "Tokenizer: digits and symbols"), tokenizer_scripts: () => L("Токенизатор: другие письменности", "Tokenizer: other scripts"),
    tokenizer_base2: () => L("Токенизатор: пустой запрос ещё раз", "Tokenizer: baseline again"),
    wrapper: () => L("Скрытые инструкции", "Hidden instructions"),

    needle: () => L("Поиск фразы в длинном тексте", "Needle in a long passage"), style: () => L("Приветствие", "Greeting"),
  };
  const probeLabel = (row) => (PROBE[row.id] ? PROBE[row.id]() : row.kind === "ability" ? `${L("Способность", "Ability")}: ${row.id.replace(/^ability_?/, "")}` : row.id);
  const TOKENIZER = {
    match: ["ok", () => L("совпал", "matches")], mismatch: ["err", () => L("чужой", "foreign")],
    identified: ["ok", () => L("определён", "identified")], unknown: ["", () => L("нет эталона", "no reference")],
    unlike_claimed: ["err", () => L("не похож на заявленный", "unlike the claimed one")],
    implausible: ["err", () => L("счёт токенов придуман", "token counts are made up")],
    no_usage: ["", () => L("нет счёта токенов", "no token counts")], failed: ["", () => L("не измерен", "not measured")],
    recount: ["warn", () => L("пересчитан шлюзом", "recounted by the gateway")],
  };
  function tokenizerStat(tok) {
    if (!tok || !tok.verdict || tok.verdict === "not_run") return "";
    const [cls, word] = TOKENIZER[tok.verdict] || TOKENIZER.unknown;
    const name = tok.verdict === "recount" ? "" : (tok.matches || [])[0] ? tok.matches[0].label : "";
    const counts = (tok.counts || []).join(" / ");
    const hidden = tok.hidden_tokens >= 150 ? ` · ${L("скрыто", "hidden")} ≈${tok.hidden_tokens.toLocaleString()} ${L("ток.", "tok.")}` : "";
    return `<div class="stat"><div class="stat-val ${cls}" style="font-size:15px">${esc(name || word())}</div>
      <div class="stat-lbl">${esc(L("Токенизатор", "Tokenizer"))}: ${esc(word())}${counts ? ` · ${esc(counts)}` : ""}${esc(hidden)}</div></div>`;
  }
  const errorOf = (error) => (error && error.message ? error.message : String(error || ""));
  const pct = (value) => (value === null || value === undefined ? "—" : `${value}%`);
  const icon = (name) => (window.MemuIcons ? MemuIcons.svg(name) : "");

  const copyBtn = (attrs, label, title) => `<button type="button" class="btn small det-copy" ${attrs} title="${esc(title || label)}" aria-label="${esc(title || label)}">${icon("copy")}<span>${esc(label)}</span></button>`;
  const copyIconBtn = (attrs, title) => `<button type="button" class="icon-btn det-copy-ico" ${attrs} title="${esc(title)}" aria-label="${esc(title)}">${icon("copy")}</button>`;
  async function copyWithFeedback(button, text, message) {
    const ok = await copyText(text, message);
    if (!ok || !button || !button.isConnected) return ok;
    const svg = button.querySelector("svg");
    button.classList.add("is-copied");
    if (svg && window.MemuIcons) svg.outerHTML = MemuIcons.svg("check");
    clearTimeout(button._copiedTimer);
    button._copiedTimer = setTimeout(() => {
      button.classList.remove("is-copied");
      const done = button.querySelector("svg");
      if (done && window.MemuIcons) done.outerHTML = MemuIcons.svg("copy");
    }, 1600);
    return ok;
  }

  function active() { return $("#tab-detector")?.classList.contains("active"); }

  async function load() {
    const root = $("#detector-root");
    if (!root) return;
    try {
      const [models, families, hist] = await Promise.all([api("/api/admin/models"), S.families.length ? { families: S.families } : api("/api/admin/detector/families"), api("/api/admin/detector/history")]);
      S.models = (models.models || []).filter((m) => m.enabled !== false && (m.kind || m.caps?.kind || "chat") === "chat").map((m) => m.id).sort();
      S.families = families.families || [];
      S.history = hist.runs || [];
    } catch (error) {
      root.innerHTML = `<div class="card"><div class="warn-strip">${esc(errorOf(error))}</div></div>`;

      root.removeEventListener("click", onClick);
      delete root.dataset.ready;
      return;
    }
    if (!root.dataset.ready) shell(root);
    visualLoad();
    fillSelects();
    renderHistory();
    if (S.run) renderRun();
  }

  function shell(root) {
    root.dataset.ready = "1";
    root.innerHTML = `
      <div class="det-layout">
        <div class="det-main">
          <form class="card det-form" id="det-form">
            <div class="grid-2">
              <div class="field"><label for="det-model">${esc(L("Модель для проверки", "Model to check"))}</label>
                <input id="det-model" list="det-models" autocomplete="off" spellcheck="false" class="mono" placeholder="genspark-claude-opus-5-5">
                <datalist id="det-models"></datalist></div>
              <div class="field"><label for="det-claimed">${esc(L("Заявленное семейство", "Claimed family"))}</label>
                <select id="det-claimed"></select>
                <div class="field-hint">${esc(L("Обычно определяется по названию модели.", "Usually taken from the model's name."))}</div></div>
            </div>
            <div class="field"><label>${esc(L("Глубина", "Depth"))}</label>
              <div class="segmented det-depth" role="radiogroup">
                <button type="button" class="seg active" data-depth="quick" role="radio" aria-checked="true">${esc(L("Быстрая · 14 запросов", "Quick · 14 requests"))}</button>
                <button type="button" class="seg" data-depth="full" role="radio" aria-checked="false">${esc(L("Полная · 17 запросов", "Full · 17 requests"))}</button>
              </div></div>
            <div class="field"><label for="det-judge">${esc(L("ИИ-анализ ответов (необязательно)", "AI analysis of the answers (optional)"))}</label>
              <select id="det-judge"></select>
              <div class="field-hint">${esc(L("Другая модель прочитает все ответы и выскажет своё мнение. Её оценка смешивается с эвристикой 50/50. Лучше брать сильную модель другого семейства.",
                "Another model reads every answer and gives its opinion, blended 50/50 with the heuristics. Prefer a strong model from a different family."))}</div></div>
            <div class="row"><button class="btn primary" id="det-go" type="submit">${esc(L("Проверить", "Run the check"))}</button>
              <span class="field-hint grow">${esc(L("Запросы идут через обычный конвейер Proxy и видны в логах. Температура — 0.", "Requests go through the normal Proxy pipeline and show in the logs. Temperature is 0."))}</span></div>
          </form>
          <div id="det-run"></div>
          <div class="card det-visual" id="det-visual">
            <div class="row"><h3 class="grow">${esc(L("Визуальный тест", "Visual test"))}</h3>
              <span class="chip dim">${esc(L("без оценки ИИ", "no AI grading"))}</span></div>
            <p class="field-hint">${esc(L("Модель из поля выше пишет страницу по трудному заданию, а оцениваете её вы — глазами. Сильную модель от слабой так видно сразу. Нажмите задание; когда страница будет готова, та же кнопка откроет результат.",
              "The model from the field above writes a page from a demanding prompt, and you judge it with your own eyes. That shows a strong model from a weak one at a glance. Press a task; when the page is ready the same button opens the result."))}</p>
            <div class="det-visual-main" id="det-visual-main"></div>
            <div class="row det-visual-more">
              <select id="det-visual-pick" class="grow" aria-label="${esc(L("Другие задания", "More prompts"))}"></select>
              <button type="button" class="btn small" id="det-visual-run">${esc(L("Запустить", "Run"))}</button>
              <button type="button" class="btn small" id="det-visual-text">${esc(L("Текст задания", "Prompt text"))}</button>
              ${copyBtn('id="det-visual-copy"', L("Скопировать промпт", "Copy prompt"))}
            </div>
            <div id="det-visual-runs"></div>
          </div>
          <details class="card adv det-how"><summary>${esc(L("Как это работает", "How it works"))}</summary>
            <ul>
              <li><b>${esc(L("Самоидентификация.", "Self-identification."))}</b> ${esc(L("Модель несколько раз по-разному спрашивают, кто она и какая у неё точная версия. Ответ легко подделать системным промптом, поэтому вес сигнала невысок сам по себе.", "The model is asked who it is, and its exact version, in several ways. A system prompt can fake this, so the signal carries modest weight on its own."))}</li>
              <li><b>${esc(L("Согласованность.", "Consistency."))}</b> ${esc(L("Сверяется, совпадают ли ответы на по-разному сформулированные вопросы, и не меняется ли ответ на дословно повторённый вопрос (полная проверка). Разнобой снижает уверенность.", "Checks whether differently-phrased questions agree with each other, and whether a literally repeated question gets the same answer (full check). Disagreement lowers confidence."))}</li>
              <li><b>${esc(L("Токенизатор.", "Tokenizer."))}</b> ${esc(L("Самая надёжная проверка. Модели отправляют три постоянных текста (обычный текст с кодом, цифры и символы, другие письменности) и берут счёт входных токенов, который сообщает сам провайдер. У каждого семейства свой токенизатор, и считает он по-своему; сравнение идёт с эталонами, вычисленными по опубликованным токенизаторам или измеренными у самих разработчиков. Системным промптом этот счёт не подделать. Не работает, если провайдер не сообщает число токенов.", "The most reliable check. The model receives three fixed texts (prose with code, digits and symbols, other scripts) and the detector takes the input token counts reported by the provider itself. Every family has its own tokenizer and counts differently; the counts are compared with references computed from published tokenizers or measured at the vendors. A system prompt cannot fake them. It does not work when the provider reports no token counts."))}</li>
              <li><b>${esc(L("Служебные токены.", "Special tokens."))}</b> ${esc(L("Модель просят повторить строки с маркерами вроде <|im_end|> или <|eot_id|>; собственный маркер конца хода обрывает ответ.", "The model repeats lines containing markers such as <|im_end|> or <|eot_id|>; its own end-of-turn marker cuts the answer."))}</li>
              <li><b>${esc(L("Скрытые инструкции.", "Hidden instructions."))}</b> ${esc(L("По счёту токенов пустого запроса видно, сколько токенов провайдер добавляет от себя к каждому сообщению; в полной проверке модель ещё и прямо спрашивают, получала ли она инструкции.", "The token count of an empty request shows how many tokens the provider adds to every message on its own; the full check also asks the model directly whether it was given instructions."))}</li>
              <li><b>${esc(L("Дата знаний.", "Knowledge cutoff."))}</b> ${esc(L("Вопросы о событиях 2022–2026 годов. По тому, где ответы перестают быть верными, оценивается срез обучения.", "Questions about dated events from 2022–2026. Where answers stop being right estimates the training cutoff."))}</li>
              <li><b>${esc(L("Версия.", "Version."))}</b> ${esc(L("Семейство и версия проверяются отдельно. Версии, чей известный срез знаний раньше событий, которые знает модель, исключаются; более новое поколение не может знать меньше старого; пакет задач на рассуждение даёт грубый уровень способностей. Догадка ИИ-аналитика сверяется с этими фактами.",
                "Family and version are checked separately. Versions whose known cutoff predates events the model knows are ruled out; a newer generation cannot know less than an older one; a batch of reasoning tasks gives a coarse ability tier. The AI analyst's guess is checked against these facts."))}</li>
              <li><b>${esc(L("Качество ответов.", "Answer quality."))}</b> ${esc(L("Оценивается только видимый ответ: размышления (<think>…) вырезаются, отказы и пустые ответы не учитываются, ответы-догадки в викторине не засчитываются. Если reasoning-модель потратила лимит на размышления, проба повторяется один раз с большим лимитом; временные ошибки шлюза — тоже один повтор. Пробы, не ответившие вовремя, помечаются, а вердикт строится по остальным.",
                "Only the visible answer is scored: reasoning (<think>…) is cut out, refusals and empty answers are ignored, hedged quiz answers do not count. If a reasoning model spent its budget on thinking, the probe is repeated once with a larger budget; transient gateway errors get one retry too. Probes that do not answer in time are marked and the verdict uses the rest."))}</li>
              <li><b>${esc(L("Характеристики.", "Specs."))}</b> ${esc(L("Модель называет заявленный контекст/вывод в токенах — сверяется с каталогом.", "The model states its claimed context/output in tokens, checked against a catalogue."))}</li>
            </ul>
            <p class="field-hint">${esc(L("Итоговый процент — калиброванная оценка (softmax с температурой + доля «недостаточно данных»), а не сырая доля сигналов: при слабых или противоречивых доказательствах она намеренно не приближается к 100%.",
              "The final percentage is a calibrated estimate (softmax with temperature plus a “not enough evidence” share), not a raw signal share: on weak or contradictory evidence it deliberately stays well short of 100%."))}</p>
          </details>
          <details class="card adv det-prov" id="det-prov">
            <summary>${esc(L("Происхождение файла (локально, необязательно)", "File provenance (local, optional)"))}</summary>
            <p class="field-hint">${esc(L("Проверяем метаданные изображения и прямо показываем, есть ли признаки ИИ. Отсутствие признаков не означает, что изображение создано человеком. Анализ выполняется локально; файл не покидает компьютер.",
              "Scans image metadata and shows whether AI-related traces were found. No traces does not mean a human made the image. The scan runs locally; the file stays on this computer."))}</p>
            <div class="row"><button type="button" class="btn small" id="det-prov-pick">${esc(L("Выбрать файл", "Choose a file"))}</button>
              <input type="file" id="det-prov-file" accept="image/png,image/jpeg,image/webp" style="display:none">
              <span class="muted" id="det-prov-name"></span></div>
            <div id="det-prov-result" role="status" aria-live="polite"></div>
          </details>
        </div>
        <aside class="card det-history"><div class="row"><h3 class="grow">${esc(L("История", "History"))}</h3></div><div id="det-history"></div></aside>
      </div>`;
    $("#det-form").onsubmit = (event) => { event.preventDefault(); start(); };
    visualBind();
    $$(".det-depth .seg").forEach((b) => {
      b.onclick = () => $$(".det-depth .seg").forEach((x) => { const on = x === b; x.classList.toggle("active", on); x.setAttribute("aria-checked", String(on)); });
    });
    root.addEventListener("click", onClick);
    $("#det-prov-pick").onclick = () => $("#det-prov-file").click();
    $("#det-prov-file").onchange = (event) => {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      if (file) runProvenance(file);
    };
  }

  function fillSelects() {
    $("#det-models").innerHTML = S.models.map((id) => `<option value="${esc(id)}">`).join("");
    const judge = $("#det-judge").value;
    $("#det-judge").innerHTML = `<option value="">${esc(L("Без ИИ-анализа", "No AI analysis"))}</option>` + S.models.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("");
    $("#det-judge").value = S.models.includes(judge) ? judge : "";
    const claimed = $("#det-claimed").value;
    $("#det-claimed").innerHTML = `<option value="">${esc(L("Авто (по названию)", "Auto (from the name)"))}</option>` +
      S.families.map((f) => `<option value="${esc(f.id)}">${esc(f.label)} · ${esc(f.vendor)}</option>`).join("");
    $("#det-claimed").value = claimed;
    if (S.picked) { $("#det-model").value = S.picked; S.picked = ""; }
    else if (!$("#det-model").value && S.models.length) $("#det-model").value = S.models[0];
  }

  function depth() { return $(".det-depth .seg.active")?.dataset.depth || "quick"; }

  async function start() {
    const model = $("#det-model").value.trim();
    if (!model) { toast(L("Выберите модель", "Pick a model"), "err"); return; }
    $("#det-go").disabled = true;
    try {
      S.run = await api("/api/admin/detector/run", { method: "POST", body: {
        model, depth: depth(), judge: $("#det-judge").value, claimed: $("#det-claimed").value, lang: LANG } });
      S.viewing = S.run.id;
      renderRun();
      poll();
    } catch (error) {
      toast(errorOf(error), "err");
    } finally {
      $("#det-go").disabled = false;
    }
  }

  async function poll() {
    clearTimeout(S.timer);
    if (!S.run || S.run.state !== "running") return;
    S.timer = setTimeout(async () => {
      try {
        const run = await api(`/api/admin/detector/runs/${encodeURIComponent(S.run.id)}`);
        if (S.viewing !== run.id) return;
        S.run = run;
        renderRun();
        if (run.state !== "running") {
          const h = await api("/api/admin/detector/history");
          S.history = h.runs || [];
          renderHistory();
        }
      } catch (error) {  }
      poll();
    }, 900);
  }

  function candidateBar(row, claimedFamily) {
    const uncertain = row.family === "__uncertain__";
    const label = uncertain ? esc(L("Недостаточно данных", "Not enough evidence")) : esc(row.label);
    const sub = uncertain ? "" : ` <span class="muted">${esc(row.vendor)}</span>`;
    return `<div class="det-dist-row ${row.family === claimedFamily ? "claimed" : ""} ${uncertain ? "uncertain" : ""}">
      <span class="det-fam">${label}${sub}</span>
      <div class="bar"><i style="width:${row.percent}%"></i></div><b>${row.percent}%</b></div>`;
  }

  function consistencyBlock(c) {
    if (!c) return "";
    const rows = [];
    if (c.identity_agreement !== null && c.identity_agreement !== undefined) {
      rows.push([L("Согласие формулировок", "Phrasing agreement"), `${Math.round(c.identity_agreement * 100)}% (${c.phrasings_compared})`]);
    }
    if (c.repeat_stability !== null && c.repeat_stability !== undefined) {
      rows.push([L("Стабильность повтора", "Repeat stability"), `${Math.round(c.repeat_stability * 100)}%`]);
    }
    if (!rows.length) return "";
    return `<div class="stat"><div class="stat-val">${rows.map((r) => esc(r[1])).join(" · ")}</div>
      <div class="stat-lbl">${esc(L("Согласованность", "Consistency"))}</div></div>`;
  }

  function renderRun() {
    const box = $("#det-run");
    if (!box) return;
    const run = S.run;
    if (!run) { box.innerHTML = ""; return; }
    if (run.state === "running") {
      const p = run.total ? Math.round((run.done / run.total) * 100) : 0;
      box.innerHTML = `<div class="card det-progress">
        <div class="row"><b class="grow">${esc(L("Проверяю", "Checking"))} <span class="mono">${esc(run.model)}</span></b>
          <span class="muted">${run.done} / ${run.total}</span><button class="btn small" data-det="cancel">${esc(L("Остановить", "Stop"))}</button></div>
        <div class="bar"><i style="width:${p}%"></i></div>
        <ul class="det-steps">${(run.probes || []).map((row) => `<li class="${row.error ? "err" : row.refused ? "warn" : row.text !== undefined ? "ok" : ""}">
          <span class="det-dot"></span>${esc(probeLabel(row))}${row.ms ? ` <span class="muted">${(row.ms / 1000).toFixed(1)} ${esc(L("с", "s"))}</span>` : ""}${probeBadges(row)}</li>`).join("")}</ul></div>`;
      return;
    }
    if (run.state === "error" || run.state === "cancelled" || !run.report) {
      box.innerHTML = `<div class="card"><div class="warn-strip">${esc(run.state === "cancelled" ? L("Проверка остановлена.", "The check was stopped.") : run.error || L("Проверка не удалась", "The check failed"))}</div>
        ${run.state === "error" ? `<div class="row"><button class="btn small" data-det="ask">✦ ${esc(t("comp.ask"))}</button></div>` : ""}</div>`;
      return;
    }
    const r = run.report;
    const [cls, icon, title, text] = VERDICT[r.overall || r.verdict] || VERDICT.unknown;
    const familyVerdict = r.overall && r.overall !== r.verdict ? (VERDICT[r.verdict] || VERDICT.unknown)[2]() : "";
    const candidates = r.candidates || [];
    const judge = r.judge || null;
    const cutoff = r.cutoff || {};
    const evidenceByCat = {};
    (r.evidence || []).forEach((row) => { (evidenceByCat[row.category] = evidenceByCat[row.category] || []).push(row); });
    const identitySignal = ((r.evidence || []).find((e) => e.probe === "identity") || {}).signal || [];
    const tokensSignal = ((r.evidence || []).find((e) => e.probe === "tokens") || {}).signal || {};
    const specific = r.specific || {};
    const context = r.context || {};

    box.innerHTML = `<div class="card det-result">
      ${r.spoof_suspected ? `<div class="warn-strip det-spoof">${esc(L(
        `Самоидентификация («${(r.spoof_detail && (VERDICT[r.spoof_detail.identity_family] ? "" : "")) || ""}${familyLabel(r.spoof_detail && r.spoof_detail.identity_family)}») расходится с поведенческими признаками («${familyLabel(r.spoof_detail && r.spoof_detail.behavior_family)}»). Возможен системный промпт, прокси или подмена модели.`,
        `Self-identification ("${familyLabel(r.spoof_detail && r.spoof_detail.identity_family)}") disagrees with the behavioural evidence ("${familyLabel(r.spoof_detail && r.spoof_detail.behavior_family)}"). A system prompt, proxy or model substitution is possible.`
      ))}</div>` : ""}
      <div class="det-verdict ${cls}"><span class="det-icon" aria-hidden="true">${icon}</span>
        <div class="grow"><b>${esc(title())}</b><p>${esc(text())}</p>
          <div class="det-meta"><span class="mono">${esc(r.model)}</span>
            ${r.claimed_label ? `<span>${esc(L("заявлено", "claimed"))}: <b>${esc(r.claimed_label)}</b>${r.claimed_percent !== null && r.claimed_percent !== undefined ? ` · ${r.claimed_percent}%` : ""}</span>` : ""}
            <span>${esc(L("уверенность в семействе", "family confidence"))}: ${esc((CONF[r.confidence] || CONF.low)())} (${r.confidence_percent}%)</span>
            ${familyVerdict ? `<span>${esc(L("семейство", "family"))}: ${esc(familyVerdict)}</span>` : ""}
            <span>${esc(L("неопределённость", "uncertainty"))}: ${Math.round((r.uncertainty || 0) * 100)}%</span></div></div>
        ${candidates[0] ? `<div class="det-top"><small>${esc(L("семейство", "family"))}</small><span>${candidates[0].family === "__uncertain__" ? esc(L("нет лидера", "no leader")) : esc(candidates[0].label)}</span><b>${candidates[0].percent}%</b>
          ${r.version && r.version.likely ? `<small>${esc(L("версия", "version"))}</small><span>${esc(r.version.likely.label)}</span><b class="${r.version.claimed && r.version.likely.id !== r.version.claimed.id ? "det-other" : ""}">${r.version.likely.percent}%</b>` : ""}</div>` : ""}
      </div>
      ${qualityStrip(r)}
      ${versionBlock(r)}
      ${reasonsBlock(r)}
      ${confidenceBlock(r)}
      <h4 class="mini-title">${esc(L("Семейство: калиброванная уверенность", "Family: calibrated confidence"))}</h4>
      <div class="det-dist">${candidates.length ? candidates.map((row) => candidateBar(row, r.claimed)).join("") : `<div class="empty">${esc(L("Нет данных", "No data"))}</div>`}</div>
      ${specific.top && !r.version ? `<h4 class="mini-title">${esc(L("Наиболее вероятная версия", "Most likely specific version"))}</h4>
        <div class="det-dist">${specific.distribution.map((row) => `<div class="det-dist-row"><span class="det-fam">${esc(row.label)}</span>
          <div class="bar"><i style="width:${row.percent}%"></i></div><b>${row.percent}%</b></div>`).join("")}</div>
        ${specific.note === "unconfirmed_fingerprint" ? `<p class="det-note">${esc(L("Отпечаток этой конкретной версии пока не подтверждён — каталог отмечает её как «неточно известную», а не проверенную.", "This specific version's fingerprint is not yet confirmed — the catalogue marks it “uncertain” rather than verified."))}</p>` : ""}` : ""}
      ${judge ? `<div class="det-judge">${judge.error ? `<div class="warn-strip">${esc(L("ИИ-анализ не удался", "AI analysis failed"))}: ${esc(judge.error)}</div>`
        : `<h4 class="mini-title">${esc(L("Мнение ИИ-аналитика", "The AI analyst's view"))} · <span class="mono">${esc(run.judge)}</span></h4>
          ${judge.best_guess ? `<p><b>${esc(L("Лучшая догадка", "Best guess"))}:</b> ${esc(judge.best_guess)}${judgeConflict(r) ? ` <span class="badge invalid">${esc(L("противоречит фактам", "contradicts the facts"))}</span>` : ""}</p>` : ""}
          ${judge.claimed_version ? `<p class="muted">${esc(L("О заявленной версии", "On the claimed version"))}: ${esc({ consistent: L("согласуется", "consistent"), contradicted: L("опровергнута", "contradicted"), unclear: L("неясно", "unclear") }[judge.claimed_version] || judge.claimed_version)}</p>` : ""}<p class="selectable">${esc(judge.reasoning || "")}</p>`}</div>` : ""}
      <div class="det-facts">
        <div class="stat"><div class="stat-val">${esc(cutoff.estimate || "—")}</div><div class="stat-lbl">${esc(L("Оценка даты знаний", "Estimated cutoff"))}${cutoff.expected ? ` · ${esc(L("ожидалось", "expected"))} ${esc(cutoff.expected)}` : ""}</div></div>
        ${tokensSignal.informative ? `<div class="stat"><div class="stat-val">${esc(tokensSignal.stopped_at ? `<|${tokensSignal.stopped_at}|>` : (tokensSignal.missing || []).map((key) => `<|${key}|>`).join(" ") || "—")}</div><div class="stat-lbl">${esc(tokensSignal.stopped_at ? L("Токен, оборвавший ответ", "Token that cut the answer") : L("Проглоченные служебные токены", "Swallowed special tokens"))}</div></div>` : ""}
        ${tokenizerStat(r.tokenizer)}
        ${r.ability && r.ability.total ? `<div class="stat"><div class="stat-val">${r.ability.passed}/${r.ability.total}</div><div class="stat-lbl">${esc(L("Задачи на точность", "Accuracy tasks"))}</div></div>` : ""}
        ${r.capability ? `<div class="stat"><div class="stat-val">${r.capability.answered ? `${r.capability.correct}/${r.capability.answered}` : "—"}</div><div class="stat-lbl">${esc(L("Задачи на рассуждение", "Reasoning tasks"))}${r.capability.tier ? ` · ${esc({ frontier: L("передовой уровень", "frontier"), strong: L("сильный уровень", "strong"), basic: L("базовый уровень", "basic") }[r.capability.tier])}` : ""}</div></div>` : ""}
        ${consistencyBlock(r.consistency)}
        <div class="stat"><div class="stat-val">${context.claimed_tokens ? context.claimed_tokens.toLocaleString() : "—"}${context.match !== null && context.match !== undefined ? (context.match ? " ✓" : " ✗") : ""}</div>
          <div class="stat-lbl">${esc(L("Заявленный контекст", "Claimed context"))}${context.expected_tokens ? ` · ${esc(L("ожидалось", "expected"))} ${context.expected_tokens.toLocaleString()}` : ""}</div></div>
        ${r.needle ? `<div class="stat"><div class="stat-val ${r.needle.passed ? "ok" : "err"}">${r.needle.passed ? "✓" : "✗"}</div><div class="stat-lbl">${esc(L("Длинный контекст (needle)", "Long-context needle"))}</div></div>` : ""}
        <div class="stat"><div class="stat-val">${r.speed && r.speed.ttft_ms ? `${r.speed.ttft_ms} ${esc(L("мс", "ms"))}` : "—"}</div><div class="stat-lbl">${esc(L("До первого токена", "Time to first token"))}${r.speed && r.speed.tokens_per_s ? ` · ${r.speed.tokens_per_s} ${esc(L("ток/с", "tok/s"))}` : r.speed && r.speed.chars_per_s ? ` · ${r.speed.chars_per_s} ${esc(L("симв/с", "chars/s"))}` : ""}</div></div>
      </div>
      ${identitySignal.length ? `<p class="det-note"><b>${esc(L("Назвала себя", "Called itself"))}:</b> ${identitySignal.map(esc).join(" · ")}</p>` : ""}
      ${r.reasoning_stream ? `<p class="det-note">${esc(L("Модель присылает отдельный поток размышлений (reasoning).", "The model streams a separate reasoning trace."))}</p>` : ""}
      ${cutoff.self_report ? `<p class="det-note"><b>${esc(L("О своей дате знаний", "About its cutoff"))}:</b> <span class="selectable">${esc(cutoff.self_report)}</span></p>` : ""}
      <details class="det-sub"><summary>${esc(L("Категории проверки", "Probe categories"))}</summary>
        <table class="tbl"><tbody>${Object.keys(CATEGORY).filter((key) => evidenceByCat[key]).map((key) => `<tr>
          <td>${esc(CATEGORY[key]())}</td>
          <td>${(evidenceByCat[key] || []).map((row) => `<span class="mono" title="${esc(JSON.stringify(row.signal || {}))}">${esc(row.probe)}: ${esc(row.weight)}</span>`).join(" · ")}</td>
        </tr>`).join("")}</tbody></table></details>
      ${(cutoff.quiz || []).length ? `<details class="det-sub"><summary>${esc(L("Викторина по датам", "Dated quiz"))} · ${(cutoff.quiz || []).filter((q) => q.correct).length}/${cutoff.quiz.length}</summary>
        <table class="tbl"><tbody>${cutoff.quiz.map((q) => `<tr><td class="mono">${esc(q.date)}</td><td>${esc(q.question)}</td><td class="selectable">${esc(q.answer || "—")}${q.hedged ? ` <span class="chip warn" title="${esc(L("Ответ-догадка («вероятно», «X или Y») не засчитан как знание", "A hedged guess (“probably”, “X or Y”) is not counted as knowledge"))}">${esc(L("догадка", "guess"))}</span>` : ""}</td><td>${q.correct ? "✓" : "✗"}</td></tr>`).join("")}</tbody></table></details>` : ""}
      ${(run.probes || []).length ? `<details class="det-sub"><summary>${esc(L("Все запросы и ответы", "Every prompt and answer"))}</summary>${run.probes.map((p, index) => `<div class="det-probe">
        <div class="row"><b class="grow">${esc(probeLabel(p))}${probeBadges(p)}</b>${p.ms ? `<span class="muted">${(p.ms / 1000).toFixed(1)} ${esc(L("с", "s"))}</span>` : ""}</div>
        <div class="det-pre-wrap"><pre class="selectable">${esc(p.prompt || "")}</pre>${copyIconBtn(`data-det="copy-prompt" data-index="${index}"`, L("Скопировать запрос", "Copy the prompt"))}</div>
        <div class="det-pre-wrap"><pre class="selectable det-answer ${p.error ? "err" : ""}">${esc(p.error || p.text || "—")}</pre>${p.text ? copyIconBtn(`data-det="copy-answer" data-index="${index}"`, L("Скопировать ответ", "Copy the answer")) : ""}</div>
        ${p.usage && p.usage.prompt_tokens ? `<div class="muted">${esc(L("Входных токенов", "Input tokens"))}: ${p.usage.prompt_tokens}${p.usage.estimated === false ? ` (${esc(L("счёт провайдера", "provider's count"))})` : ` (${esc(L("оценка Proxy по длине", "Proxy's estimate by length"))})`}</div>` : ""}</div>`).join("")}</details>` : ""}
      <div class="row det-actions"><button class="btn small" data-det="again">${esc(L("Повторить", "Run again"))}</button>
        <button class="btn small" data-det="copy">${esc(L("Скопировать сводку", "Copy the summary"))}</button>
        <button class="btn small" data-det="export">${esc(L("Сохранить JSON", "Save JSON"))}</button>
        <button class="btn small" data-det="ask">✦ ${esc(L("Обсудить с помощником", "Discuss with the companion"))}</button></div>
    </div>`;
  }

  function probeBadges(p) {
    const out = [];
    if (p.timed_out) out.push(`<span class="chip warn">${esc(L("таймаут", "timeout"))}</span>`);
    else if (p.refused) out.push(`<span class="chip warn" title="${esc(L("Модель отказалась отвечать — проба не учтена", "The model declined to answer — the probe is not counted"))}">${esc(L("отказ", "refused"))}</span>`);
    if (p.retried === "reasoning_budget") out.push(`<span class="chip" title="${esc(L("Модель потратила лимит ответа на размышления; проба повторена с большим лимитом", "The model spent the reply budget on thinking; the probe was repeated with a larger budget"))}">${esc(L("повтор: размышления", "retry: thinking"))}</span>`);
    else if (p.retried) out.push(`<span class="chip" title="${esc(L("Временная ошибка шлюза — проба повторена один раз", "A transient gateway error — the probe was repeated once"))}">${esc(L("повтор", "retried"))}</span>`);
    if (p.thinking_chars) out.push(`<span class="chip dim" title="${esc(L("Блок размышлений (<think>…) убран из ответа перед оценкой", "A reasoning block (<think>…) was removed from the answer before scoring"))}">think −${Number(p.thinking_chars).toLocaleString(locale())}</span>`);
    if (p.truncated_thinking) out.push(`<span class="chip warn">${esc(L("оборвано на размышлениях", "cut off while thinking"))}</span>`);
    return out.length ? ` <span class="det-badges">${out.join("")}</span>` : "";
  }

  function qualityStrip(r) {
    const q = r.quality;
    if (!q || !q.partial) return "";
    return `<div class="warn-strip">${esc(L(`Часть проб (${q.timed_out.length}) не ответила вовремя — вердикт построен по неполным данным, уверенность ограничена.`,
      `Some probes (${q.timed_out.length}) did not answer in time — the verdict rests on partial data and its confidence is capped.`))}</div>`;
  }

  function confidenceBlock(r) {
    const rows = r.confidence_factors || [];
    if (!rows.length) return "";
    return `<details class="det-sub det-conf"><summary>${esc(L("Почему уверенность", "Why this confidence"))} — ${esc((CONF[r.confidence] || CONF.low)())}</summary>
      <ul class="det-reasons">${rows.map((row) => `<li class="${esc(row.level)}"><span class="det-reason-ico" aria-hidden="true">${REASON_ICON[row.level] || "i"}</span>
        <span>${esc(LANG === "en" ? row.en : row.ru)}</span></li>`).join("")}</ul></details>`;
  }

  function judgeConflict(r) { return !!(r.version && r.version.judge && r.version.judge.conflict); }

  function versionBlock(r) {
    const v = r.version;
    if (!v) return "";
    const [cls, title] = VERSION[v.verdict] || VERSION.unrecognized;
    const claimed = v.claimed ? v.claimed.label : (r.model || "");
    const window = v.knowledge && v.knowledge.estimate
      ? (v.knowledge.bounded ? L(`знания до ${v.knowledge.estimate}`, `knowledge up to ${v.knowledge.estimate}`) : L(`знания не раньше ${v.knowledge.estimate}`, `knowledge from ${v.knowledge.estimate} or later`))
      : L("дату знаний оценить не удалось", "cutoff not estimated");
    const rows = (v.distribution || []).filter((row) => row.percent > 0).slice(0, 6);
    return `<div class="det-version ${cls}">
      <div class="row"><span class="det-icon small ${cls}" aria-hidden="true">${cls === "ok" ? "✓" : cls === "info" ? "i" : cls === "unknown" ? "?" : "!"}</span>
        <div class="grow"><b>${esc(L("Версия", "Version"))}: ${esc(title())}</b>
          <div class="muted">${esc(L("заявлено", "claimed"))}: <b>${esc(claimed)}</b>${v.claimed ? ` (${v.claimed_percent || 0}%)` : ""}${v.likely && (!v.claimed || v.likely.id !== v.claimed.id) ? ` · ${esc(L("вероятнее", "more likely"))}: <b>${esc(v.likely.label)}</b> (${v.likely.percent}%)` : ""} · ${esc(window)}</div></div></div>
      ${rows.length ? `<div class="det-dist">${rows.map((row) => `<div class="det-dist-row ${v.claimed && row.id === v.claimed.id ? "claimed" : ""} ${row.id === "__uncertain__" ? "uncertain" : ""}">
        <span class="det-fam">${esc(row.id === "__uncertain__" ? L("другая версия", "another version") : row.label)}</span>
        <div class="bar"><i style="width:${row.percent}%"></i></div><b>${row.percent}%</b></div>`).join("")}</div>` : ""}
      ${(v.ruled_out || []).length ? `<div class="det-ruled"><span class="muted">${esc(L("Исключены по дате знаний", "Ruled out by the knowledge window"))}:</span>
        ${v.ruled_out.map((row) => `<span class="chip dim" title="${esc(L("срез знаний", "cutoff"))} ${esc(row.cutoff)}">${esc(row.label)}</span>`).join("")}</div>` : ""}
    </div>`;
  }

  function reasonsBlock(r) {
    const rows = r.reasons || [];
    if (!rows.length) return "";
    return `<h4 class="mini-title">${esc(L("Почему так", "Why"))}</h4>
      <ul class="det-reasons">${rows.map((row) => `<li class="${esc(row.level)}"><span class="det-reason-ico" aria-hidden="true">${REASON_ICON[row.level] || "i"}</span>
        <span>${esc(LANG === "en" ? row.en : row.ru)}</span></li>`).join("")}</ul>`;
  }

  function familyLabel(id) {
    const row = (S.families || []).find((f) => f.id === id);
    return row ? row.label : (id || "?");
  }

  function summary(run) {
    const r = run.report || {};
    const verdict = (VERDICT[r.overall || r.verdict] || VERDICT.unknown)[2]();
    const specific = r.specific && r.specific.top ? r.specific.top.label : "";
    const version = r.version ? `${L("Версия", "Version")}: ${(VERSION[r.version.verdict] || VERSION.unrecognized)[1]()}${r.version.claimed ? ` (${L("заявлено", "claimed")} ${r.version.claimed.label})` : ""}` : "";
    const why = (r.reasons || []).map((row) => `- ${LANG === "en" ? row.en : row.ru}`).join("\n");
    return [`${L("Модель", "Model")}: ${run.model}`,
      `${L("Вердикт", "Verdict")}: ${verdict} (${L("уверенность", "confidence")} ${(CONF[r.confidence] || CONF.low)()}, ${r.confidence_percent}%, ${L("неопределённость", "uncertainty")} ${Math.round((r.uncertainty || 0) * 100)}%)`,
      `${L("Калиброванное распределение", "Calibrated distribution")}: ${(r.candidates || []).map((d) => `${d.family === "__uncertain__" ? L("недостаточно данных", "not enough evidence") : d.label} ${d.percent}%`).join(", ") || "—"}`,
      version,
      !r.version && specific ? `${L("Вероятная версия", "Likely version")}: ${specific}` : "",
      r.spoof_suspected ? L("⚠ Возможна подмена/спуфинг: самоидентификация расходится с поведением.", "⚠ Possible spoof/substitution: self-report disagrees with behaviour.") : "",
      r.tokenizer && r.tokenizer.counts ? `${L("Токенизатор", "Tokenizer")}: ${(TOKENIZER[r.tokenizer.verdict] || TOKENIZER.unknown)[1]()}${(r.tokenizer.matches || [])[0] ? ` — ${r.tokenizer.matches[0].label}` : ""} (${r.tokenizer.counts.join(" / ")})` : "",
      `${L("Дата знаний", "Cutoff")}: ${(r.cutoff && r.cutoff.estimate) || "—"}${r.cutoff && r.cutoff.expected ? ` (${L("ожидалось", "expected")} ${r.cutoff.expected})` : ""}`,
      r.judge && r.judge.best_guess ? `${L("ИИ-анализ", "AI analysis")}: ${r.judge.best_guess}${judgeConflict(r) ? ` (${L("противоречит фактам", "contradicts the facts")})` : ""}` : "",
      why ? `${L("Почему так", "Why")}:\n${why}` : ""].filter(Boolean).join("\n");
  }

  const V = { prompts: [], runs: [], timer: null, texts: {} };

  async function visualText(promptId) {
    if (V.texts[promptId]) return V.texts[promptId];
    const row = await api(`/api/admin/detector/visual/prompts/${encodeURIComponent(promptId)}`);
    V.texts[promptId] = row.prompt || "";
    return V.texts[promptId];
  }
  async function visualCopy(button, promptId) {
    try {
      const text = await visualText(promptId);
      const row = V.prompts.find((item) => item.id === promptId);
      await copyWithFeedback(button, text, row ? L(`Промпт «${visualTitle(row)}» скопирован`, `“${visualTitle(row)}” prompt copied`) : L("Промпт скопирован", "Prompt copied"));
    } catch (error) { toast(errorOf(error), "err"); }
  }
  function visualPromptModal(promptId, text) {
    const row = V.prompts.find((item) => item.id === promptId);
    openModal(`<h3>${esc(L("Текст задания", "Prompt text"))}${row ? ` · ${esc(visualTitle(row))}` : ""}</h3>
      <p class="field-hint">${esc(L(`${text.length.toLocaleString(locale())} символов. Можно отправить в любой чат, чтобы сравнить модели вне Proxy.`, `${text.length.toLocaleString(locale())} characters. Paste it into any chat to compare models outside Proxy.`))}</p>
      <pre class="code-block selectable det-visual-pre">${esc(text)}</pre>
      <div class="row-end">${copyBtn('id="m-copy"', L("Скопировать промпт", "Copy prompt"))}<button class="btn" id="m-close">${esc(t("modal.close"))}</button></div>`, true);
    $("#m-close").onclick = closeModal;
    $("#m-copy").onclick = (event) => copyWithFeedback(event.currentTarget, text, L("Промпт скопирован", "Prompt copied"));
  }
  const visualTitle = (row) => (LANG === "en" ? row.title_en || row.title : row.title);
  const visualModel = () => ($("#det-model") ? $("#det-model").value.trim() : "");
  const visualUrl = (run, download) => `/api/admin/detector/visual/runs/${encodeURIComponent(run.id)}/page?key=${encodeURIComponent(run.key)}${download ? "&download=1" : ""}`;

  const visualLatest = (promptId) => V.runs.find((run) => run.prompt_id === promptId && run.model === visualModel());

  async function visualLoad() {
    try {
      if (!V.prompts.length) {
        const data = await api("/api/admin/detector/visual/prompts");
        V.prompts = data.prompts || [];
      }
      V.runs = (await api("/api/admin/detector/visual/runs")).runs || [];
    } catch (error) {  }
    visualRender();
    visualWatch();

    V.prompts.filter((row) => row.group === "featured").forEach((row) => { visualText(row.id).catch(() => {}); });
  }

  function visualWatch() {
    clearTimeout(V.timer);
    if (!V.runs.some((run) => run.state === "running")) return;
    V.timer = setTimeout(async () => {
      try { V.runs = (await api("/api/admin/detector/visual/runs")).runs || []; } catch (error) {  }
      if (active()) visualRender();
      visualWatch();
    }, 1000);
  }

  function visualProgress(run) {
    const parts = [`${Math.round(run.seconds)} ${L("с", "s")}`];
    if (run.chars) parts.push(`${run.chars.toLocaleString()} ${L("симв.", "chars")}`);
    else if (run.reasoning_chars) parts.push(L("размышляет", "thinking"));
    else parts.push(L("ждёт ответа", "waiting"));
    return parts.join(" · ");
  }

  function visualSources(promptId) {
    return V.runs.filter((run) => run.prompt_id === promptId && run.state === "done").map((run) => ({
      key: `run:${run.id}`, url: visualUrl(run), run,
      label: `${run.model} · ${new Date(run.started * 1000).toLocaleString(locale(), { dateStyle: "short", timeStyle: "short" })}` }));
  }
  function visualSourceMeta(source) {
    const run = source && source.run;
    if (!run) return "";
    return esc([`${Math.round(run.seconds)} ${L("с", "s")}`, `${Math.round((run.size || 0) / 1024)} ${L("КБ", "KB")}`,
      run.tokens ? `${run.tokens.toLocaleString(locale())} ${L("ток.", "tok.")}` : "", run.complete ? "" : L("страница оборвана", "the page is cut off")].filter(Boolean).join(" · "));
  }
  function visualCompare(promptId, leftKey) {
    const sources = visualSources(promptId);
    const row = V.prompts.find((item) => item.id === promptId);
    if (sources.length < 2) { toast(L("Для сравнения нужны два готовых запуска этого задания", "Comparing needs two finished runs of this task"), "warn"); return; }
    const left = sources.find((item) => item.key === leftKey) || sources.find((item) => item.run.model === visualModel()) || sources[0];

    const right = sources.find((item) => item !== left && item.run.model !== left.run.model) || sources.find((item) => item !== left);
    const side = (id, chosen) => `<section class="det-cmp-side" aria-label="${esc(id === "l" ? L("Слева", "Left") : L("Справа", "Right"))}">
        <div class="det-cmp-head"><select id="det-cmp-${id}" aria-label="${esc(L("Что показать", "What to show"))}">${sources.map((item) =>
          `<option value="${esc(item.key)}" ${item === chosen ? "selected" : ""}>${esc(item.label)}</option>`).join("")}</select>
          <a class="btn small" id="det-cmp-${id}-open" href="${esc(chosen.url)}" target="_blank" rel="noopener">${esc(L("Открыть отдельно", "Open separately"))}</a></div>
        <div class="det-cmp-frame" id="det-cmp-${id}-box">
          <iframe id="det-cmp-${id}-frame" src="${esc(chosen.url)}" sandbox="allow-scripts allow-pointer-lock" referrerpolicy="no-referrer" title="${esc(chosen.label)}"></iframe></div>
        <div class="det-cmp-meta" id="det-cmp-${id}-meta">${visualSourceMeta(chosen)}</div></section>`;
    openModal(`<h3>${esc(L("Сравнение", "Comparison"))}${row ? ` · ${esc(visualTitle(row))}` : ""}</h3>
      <p class="field-hint">${esc(L("Обе страницы работают в песочнице: у них нет доступа к панели. Вращать сцены и смотреть анимацию можно прямо здесь.",
        "Both pages run sandboxed, with no access to the panel. Scenes can be rotated and animations watched right here."))}</p>
      <div class="det-cmp">${side("l", left)}${side("r", right)}</div>
      <div class="row-end"><button class="btn" id="m-close">${esc(t("modal.close"))}</button></div>`, true);
    $("#modal-box").classList.add("det-compare-modal");
    $("#m-close").onclick = closeModal;
    ["l", "r"].forEach((id) => {
      const select = $(`#det-cmp-${id}`);
      select.onchange = () => {
        const chosen = sources.find((item) => item.key === select.value);
        if (!chosen) return;
        $(`#det-cmp-${id}-box`).innerHTML = `<iframe id="det-cmp-${id}-frame" src="${esc(chosen.url)}" sandbox="allow-scripts allow-pointer-lock" referrerpolicy="no-referrer" title="${esc(chosen.label)}"></iframe>`;
        $(`#det-cmp-${id}-open`).href = chosen.url;
        $(`#det-cmp-${id}-meta`).innerHTML = visualSourceMeta(chosen);
      };
    });
  }

  function visualGalleryLink(row) {
    if (!row.compare_url) return "";
    let host = "";
    try { host = new URL(row.compare_url).hostname.replace(/^www\./, ""); } catch (error) { return ""; }
    return `<a class="det-visual-gallery" href="${esc(row.compare_url)}" target="_blank" rel="noopener noreferrer"
      title="${esc(row.compare_url)}"><span class="det-visual-gallery-ico" aria-hidden="true">${GALLERY_ICON}</span>
      <span class="det-visual-gallery-txt"><b>${esc(L("Как этот тест рисуют разные ИИ", "How different AIs draw this test"))}</b>
      <span>${esc(L(`Сравнение на ${host}`, `Comparison on ${host}`))}</span></span><span class="det-visual-gallery-go" aria-hidden="true">↗</span></a>`;
  }
  const GALLERY_ICON = '<svg class="i" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="4.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>';

  function visualRender() {
    const main = $("#det-visual-main");
    if (!main) return;
    const featured = V.prompts.filter((row) => row.group === "featured");
    main.innerHTML = featured.map((row) => {
      const run = visualLatest(row.id);
      const state = run ? run.state : "";
      const label = state === "running" ? `${visualTitle(row)} · ${visualProgress(run)}`
        : state === "done" ? `${L("Открыть", "Open")}: ${visualTitle(row)}` : visualTitle(row);
      return `<div class="det-visual-tile ${esc(state)}">
        <button type="button" class="btn ${state === "done" ? "primary" : ""}" data-vis="main" data-id="${esc(row.id)}" ${state === "running" ? "disabled" : ""}>${esc(label)}</button>
        <div class="det-visual-acts">
          ${copyBtn(`data-vis="copy" data-id="${esc(row.id)}"`, L("Промпт", "Prompt"), L(`Скопировать промпт «${visualTitle(row)}»`, `Copy the “${visualTitle(row)}” prompt`))}
          <button type="button" class="icon-btn" data-vis="view" data-id="${esc(row.id)}" title="${esc(L("Показать текст задания", "Show the prompt text"))}" aria-label="${esc(L("Показать текст задания", "Show the prompt text"))}">${icon("logs")}</button>
          ${state === "done" || state === "error" ? `<button type="button" class="icon-btn" data-vis="again" data-id="${esc(row.id)}" title="${esc(L("Запустить заново", "Run again"))}" aria-label="${esc(L("Запустить заново", "Run again"))}">${icon("rotate")}</button>` : ""}
        </div>
        <span class="field-hint">${esc(LANG === "en" ? row.about_en : row.about)}</span>
        ${visualGalleryLink(row)}
      </div>`;
    }).join("");
    const pick = $("#det-visual-pick");
    if (pick && pick.options.length !== V.prompts.length - featured.length) {
      pick.innerHTML = V.prompts.filter((row) => row.group !== "featured").map((row) =>
        `<option value="${esc(row.id)}">${esc(visualTitle(row))}</option>`).join("");
    }
    const box = $("#det-visual-runs");
    if (!box) return;
    box.innerHTML = V.runs.length ? `<table class="tbl det-visual-table"><tbody>${V.runs.slice(0, 12).map((run) => `<tr>
      <td><b>${esc(visualTitle(run))}</b><div class="mono dim">${esc(run.model)}</div></td>
      <td class="dim">${run.state === "running" ? esc(visualProgress(run))
        : run.state === "done" ? `${Math.round(run.seconds)} ${esc(L("с", "s"))} · ${Math.round(run.size / 1024)} ${esc(L("КБ", "KB"))}${run.tokens ? ` · ${run.tokens.toLocaleString()} ${esc(L("ток.", "tok."))}` : ""}${run.complete ? "" : ` · <span class="err">${esc(L("оборвана", "cut off"))}</span>`}`
        : `<span class="err">${esc(run.state === "cancelled" ? L("остановлен", "stopped") : translateMessage(run.error || L("ошибка", "error")))}</span>`}</td>
      <td class="actions">${run.state === "running" ? `<button class="btn small" data-vis="cancel" data-run="${esc(run.id)}">${esc(L("Остановить", "Stop"))}</button>`
        : `${run.state === "done" ? `<a class="btn small primary" href="${esc(visualUrl(run))}" target="_blank" rel="noopener">${esc(L("Открыть", "Open"))}</a>
            <a class="btn small is-icon" href="${esc(visualUrl(run, true))}" title="${esc(L("Сохранить страницу", "Save the page"))}" aria-label="${esc(L("Сохранить страницу", "Save the page"))}">${icon("download")}</a>` : ""}
          ${run.state === "done" && (V.runs.some((other) => other !== run && other.prompt_id === run.prompt_id && other.state === "done"))
            ? `<button class="btn small" data-vis="compare" data-id="${esc(run.prompt_id)}" data-run="${esc(run.id)}" title="${esc(L("Рядом с другим запуском этого задания", "Next to another run of the same task"))}">${esc(L("Сравнить", "Compare"))}</button>` : ""}
          <button class="btn small" data-vis="answer" data-run="${esc(run.id)}" title="${esc(L("Ответ модели целиком", "The model's full answer"))}">${esc(L("Ответ", "Answer"))}</button>
          ${V.prompts.some((row) => row.id === run.prompt_id) ? copyIconBtn(`data-vis="copy" data-id="${esc(run.prompt_id)}"`, L("Скопировать промпт этого теста", "Copy this test's prompt")) : ""}
          <button class="btn small danger" data-vis="delete" data-run="${esc(run.id)}" aria-label="${esc(L("Удалить", "Delete"))}">✕</button>`}</td>
    </tr>`).join("")}</tbody></table>` : "";
  }

  async function visualStart(promptId) {
    const model = visualModel();
    if (!model) { toast(L("Сначала выберите модель в поле выше", "Pick a model in the field above first"), "warn"); $("#det-model")?.focus(); return; }
    try {
      const run = await api("/api/admin/detector/visual/run", { method: "POST", body: { model, prompt_id: promptId } });
      V.runs = [run, ...V.runs.filter((row) => row.id !== run.id)];
      visualRender();
      visualWatch();
    } catch (error) { toast(errorOf(error), "err"); }
  }

  function visualBind() {
    const card = $("#det-visual");
    if (!card) return;
    $("#det-model").addEventListener("input", visualRender);
    $("#det-visual-run").onclick = () => visualStart($("#det-visual-pick").value);
    $("#det-visual-text").onclick = async () => {
      const id = $("#det-visual-pick").value;
      try { visualPromptModal(id, await visualText(id)); } catch (error) { toast(errorOf(error), "err"); }
    };
    $("#det-visual-copy").onclick = (event) => visualCopy(event.currentTarget, $("#det-visual-pick").value);
    card.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-vis]");
      if (!button) return;
      const action = button.dataset.vis;
      if (action === "main") {
        const run = visualLatest(button.dataset.id);
        if (run && run.state === "done") window.open(visualUrl(run), "_blank", "noopener");
        else visualStart(button.dataset.id);
      } else if (action === "again") {
        visualStart(button.dataset.id);
      } else if (action === "compare") {
        visualCompare(button.dataset.id, button.dataset.run ? `run:${button.dataset.run}` : "");
      } else if (action === "copy") {
        visualCopy(button, button.dataset.id);
      } else if (action === "view") {
        try { visualPromptModal(button.dataset.id, await visualText(button.dataset.id)); } catch (error) { toast(errorOf(error), "err"); }
      } else if (action === "cancel") {
        await api(`/api/admin/detector/visual/runs/${encodeURIComponent(button.dataset.run)}/cancel`, { method: "POST" }).catch(() => {});
        visualLoad();
      } else if (action === "delete") {
        await api(`/api/admin/detector/visual/runs/${encodeURIComponent(button.dataset.run)}/delete`, { method: "POST" }).catch(() => {});
        visualLoad();
      } else if (action === "answer") {
        try {
          const row = await api(`/api/admin/detector/visual/runs/${encodeURIComponent(button.dataset.run)}/answer`);
          openModal(`<h3>${esc(L("Ответ модели", "The model's answer"))}</h3><pre class="code-block selectable det-visual-pre">${esc(row.text)}</pre>
            <div class="row-end">${copyBtn('id="m-copy"', L("Скопировать ответ", "Copy the answer"))}<button class="btn" id="m-close">${esc(t("modal.close"))}</button></div>`, true);
          $("#m-close").onclick = closeModal;
          $("#m-copy").onclick = (event) => copyWithFeedback(event.currentTarget, row.text || "", L("Ответ скопирован", "Answer copied"));
        } catch (error) { toast(errorOf(error), "err"); }
      }
    });
  }

  function renderHistory() {
    const box = $("#det-history");
    if (!box) return;
    if (!S.history.length) { box.innerHTML = `<div class="empty">${esc(L("Здесь появятся прошлые проверки.", "Past checks appear here."))}</div>`; return; }
    box.innerHTML = S.history.map((h) => {
      const r = h.report || {};
      const [cls, icon] = h.state === "done" ? (VERDICT[r.overall || r.verdict] || VERDICT.unknown) : ["unknown", "–"];
      const top = (r.candidates || r.distribution || [])[0];
      return `<div class="det-hist ${S.viewing === h.id ? "active" : ""}" data-id="${esc(h.id)}">
        <button type="button" class="det-hist-open" data-det="open"><span class="det-icon small ${cls}">${icon}</span>
          <span class="grow"><span class="mono">${esc(h.model)}</span><span class="muted">${esc(fmtDateTime(h.started))} · ${esc(h.depth === "full" ? L("полная", "full") : L("быстрая", "quick"))}
          ${top ? ` · ${esc(top.family === "__uncertain__" ? L("не уверен", "unsure") : top.label)} ${top.percent}%` : h.state === "error" ? ` · ${esc(L("ошибка", "error"))}` : ""}</span></span></button>
        <button type="button" class="icon-btn" data-det="del" aria-label="${esc(L("Удалить из истории", "Remove from history"))}" title="${esc(L("Удалить из истории", "Remove from history"))}">✕</button></div>`;
    }).join("");
  }

  async function onClick(event) {
    const el = event.target.closest("[data-det]");
    if (!el) return;
    const act = el.dataset.det;
    const id = el.closest("[data-id]")?.dataset.id;
    try {
      if (act === "cancel") { await api(`/api/admin/detector/runs/${encodeURIComponent(S.run.id)}/cancel`, { method: "POST" }); return; }
      if (act === "again") { $("#det-model").value = S.run.model; $("#det-model").scrollIntoView({ block: "center", behavior: "smooth" }); return start(); }
      if (act === "copy") return copyWithFeedback(el, summary(S.run));
      if (act === "copy-prompt" || act === "copy-answer") {
        const p = (S.run.probes || [])[Number(el.dataset.index)];
        if (!p) return;
        return copyWithFeedback(el, act === "copy-prompt" ? p.prompt || "" : p.text || "",
          act === "copy-prompt" ? L("Запрос скопирован", "Prompt copied") : L("Ответ скопирован", "Answer copied"));
      }
      if (act === "export") return saveJson(S.run);
      if (act === "ask") {
        const r = S.run.report;
        return window.MemuCompanion && MemuCompanion.ask(
          r ? L("Объясни результат детектора и что мне с этим делать.", "Explain the detector result and what I should do about it.")
            : L("Почему проверка детектора не удалась?", "Why did the detector check fail?"),
          { source: "detector", model: S.run.model, message: r ? summary(S.run) : S.run.error });
      }
      if (act === "open") {
        S.viewing = id;
        clearTimeout(S.timer);
        S.run = await api(`/api/admin/detector/runs/${encodeURIComponent(id)}`);
        renderRun();
        renderHistory();
        if (S.run.state === "running") poll();
        $("#det-run").scrollIntoView({ block: "start", behavior: "smooth" });
        return;
      }
      if (act === "del") {
        await api(`/api/admin/detector/history/${encodeURIComponent(id)}/delete`, { method: "POST" });
        S.history = S.history.filter((h) => h.id !== id);
        if (S.viewing === id) { S.run = null; S.viewing = ""; renderRun(); }
        renderHistory();
      }
    } catch (error) { toast(errorOf(error), "err"); }
  }

  function saveJson(run) {
    const blob = new Blob([JSON.stringify(run, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `detector-${String(run.model).replace(/[^\w.-]+/g, "_")}-${run.id}.json`;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
    toast(t("common.downloaded"));
  }

  function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("read failed"));
      reader.readAsDataURL(file);
    });
  }

  async function runProvenance(file) {
    const nameBox = $("#det-prov-name");
    const box = $("#det-prov-result");
    if (!box) return;
    if (file.size > 9_500_000) {
      toast(L("Файл слишком большой (максимум ~9 МБ)", "File is too large (max ~9 MB)"), "err");
      return;
    }
    if (nameBox) nameBox.textContent = file.name;
    box.innerHTML = `<div class="empty">${esc(L("Сканирую…", "Scanning…"))}</div>`;
    try {
      const data = await readAsDataUrl(file);
      const result = await api("/api/admin/detector/provenance", { method: "POST", body: { data, filename: file.name, lang: LANG } });
      S.prov = result;
      renderProvenance(result);
      box.querySelector(".det-prov-verdict")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (error) {
      box.innerHTML = `<div class="warn-strip">${esc(errorOf(error))}</div>`;
    }
  }

  function renderProvenance(r) {
    const box = $("#det-prov-result");
    if (!box) return;
    const signals = r.signals || {};
    const aiTraces = !!(r.ai_digital_source_type || signals.generation_parameters_found ||
      (r.claimed_generator && !/may include AI features/i.test(r.claimed_generator)));
    const credentialsHint = !!signals.c2pa_jumbf_hint;
    const verdict = aiTraces ? {
      tone: "ai", title: L("Есть признаки ИИ-генерации", "AI-generation traces found"),
      detail: L("Метки или параметры файла указывают на генерацию ИИ. Это сильная подсказка, но метаданные можно изменить.",
        "File tags or parameters point to AI generation. This is a useful clue, but metadata can be changed."),
    } : credentialsHint ? {
      tone: "credentials", title: L("Найден след Content Credentials", "Content Credentials trace found"),
      detail: L("В файле есть маркер C2PA. Он сам по себе не сообщает, создано ли изображение ИИ; проверьте подписанный манифест по ссылке ниже.",
        "A C2PA marker is present. It does not by itself say whether AI made the image; verify the signed manifest using the link below."),
    } : {
      tone: "unknown", title: L("Происхождение не установлено", "Origin could not be determined"),
      detail: L("Признаков ИИ в доступных метаданных нет. Это не доказательство человеческого происхождения: при сохранении или публикации метаданные часто исчезают.",
        "No AI traces were found in accessible metadata. This is not proof of human origin: saving or sharing an image often strips metadata."),
    };
    const chips = [
      signals.c2pa_jumbf_hint ? L("найден намёк на манифест C2PA/JUMBF", "C2PA/JUMBF manifest hint found") : "",
      signals.iptc_digital_source_type ? `IPTC DigitalSourceType: ${signals.iptc_digital_source_type}` : "",
      signals.generation_parameters_found ? L("найдены параметры генерации (PNG)", "generation parameters found (PNG)") : "",
    ].filter(Boolean);
    box.innerHTML = `
      <div class="det-prov-verdict ${verdict.tone}">
        <div class="det-prov-verdict-kicker">${esc(L("Результат проверки", "Scan result"))}</div>
        <strong>${esc(verdict.title)}</strong>
        <p>${esc(verdict.detail)}</p>
      </div>
      <div class="det-facts">
        <div class="stat"><div class="stat-val">${esc(r.format || "—")}</div><div class="stat-lbl">${esc(L("Формат", "Format"))} · ${(r.size_bytes / 1024).toFixed(0)} ${esc(L("КБ", "KB"))}</div></div>
        <div class="stat"><div class="stat-val" style="font-size:14px;overflow-wrap:anywhere">${esc(r.claimed_generator || L("не найдено", "not found"))}</div><div class="stat-lbl">${esc(L("Заявленный генератор", "Claimed generator"))}</div></div>
      </div>
      ${chips.length ? `<p class="det-note">${chips.map(esc).join(" · ")}</p>` : ""}
      ${(r.findings || []).length ? `<details class="det-sub" open><summary>${esc(L("Найденные метаданные", "Metadata found"))}</summary>
        <table class="tbl"><tbody>${r.findings.map((f) => `<tr><td class="mono">${esc(f.source)}</td><td>${esc(f.tag)}</td><td class="selectable">${esc(f.excerpt || "—")}</td></tr>`).join("")}</tbody></table></details>` : ""}
      <details class="det-prov-caveats"><summary>${esc(L("Ограничения проверки", "Scan limitations"))}</summary>
        ${(r.caveats || []).map((c) => `<p>${esc(c)}</p>`).join("")}</details>
      <p class="field-hint">${esc(L("Официальные инструменты проверки:", "Official verification tools:"))}
        ${(r.portals || []).map((p) => `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.label)}</a>`).join(" · ")}</p>`;
  }

  return {
    load,

    pick(modelId) {
      S.picked = modelId;
      if ($("#det-model")) { $("#det-model").value = modelId; S.picked = ""; }
    },
    relabel() {
      const root = $("#detector-root");
      if (!root || !root.dataset.ready) return;
      const keep = { model: $("#det-model").value, judge: $("#det-judge").value, claimed: $("#det-claimed").value, depth: depth() };
      root.removeEventListener("click", onClick);
      delete root.dataset.ready;
      shell(root);
      fillSelects();
      $("#det-model").value = keep.model;
      $("#det-judge").value = keep.judge;
      $("#det-claimed").value = keep.claimed;
      $$(".det-depth .seg").forEach((x) => { const on = x.dataset.depth === keep.depth; x.classList.toggle("active", on); x.setAttribute("aria-checked", String(on)); });
      renderHistory();
      renderRun();
      if (S.prov) renderProvenance(S.prov);
    },
    active,
  };
})();
