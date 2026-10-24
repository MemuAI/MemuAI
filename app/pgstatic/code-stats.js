/* MemuCode — статистика использования (только режим MemuCode).
 *
 * Карточка на главной MemuCode и панель по команде /stats: сессии,
 * сообщения, токены, активные дни, пиковый час, любимая модель, тепловая
 * карта по дням за полгода и сравнение объёма с известными книгами.
 * Данные собирает сервер (app/code_stats.py) по каждому раунду модели в
 * MemuCode; прошлые сессии из истории один раз импортируются с оценкой
 * токенов.
 */
"use strict";
const MemuCodeStats = (() => {
  const WEEKS = 26;
  const state = { range: "all", tab: "overview", data: null, loading: false, error: "", loadedAt: 0, pick: Math.random() };
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);

  /* Объём известных текстов в токенах (≈ 1,3 токена на английское слово;
     для русской классики — по английскому переводу). Сравнение выбирается
     случайно из подходящих по масштабу, как в Claude Code, и меняется при
     каждом обновлении статистики или по щелчку. */
  const REFERENCES = [
    { tokens: 6_000, en: "the U.S. Constitution", ruIn: "в Конституции США", ruOf: "Конституции США" },
    { tokens: 22_000, en: "The Little Prince", ruIn: "в «Маленьком принце»", ruOf: "«Маленького принца»" },
    { tokens: 40_000, en: "Eugene Onegin", ruIn: "в «Евгении Онегине»", ruOf: "«Евгения Онегина»" },
    { tokens: 60_000, en: "The Hitchhiker's Guide to the Galaxy", ruIn: "в «Автостопом по галактике»", ruOf: "«Автостопом по галактике»" },
    { tokens: 61_000, en: "The Great Gatsby", ruIn: "в «Великом Гэтсби»", ruOf: "«Великого Гэтсби»" },
    { tokens: 105_000, en: "Harry Potter and the Philosopher's Stone", ruIn: "в «Гарри Поттере и философском камне»", ruOf: "«Гарри Поттера и философского камня»" },
    { tokens: 116_000, en: "Nineteen Eighty-Four", ruIn: "в «1984»", ruOf: "«1984»" },
    { tokens: 124_000, en: "The Hobbit", ruIn: "в «Хоббите»", ruOf: "«Хоббита»" },
    { tokens: 130_000, en: "The Twelve Chairs", ruIn: "в «Двенадцати стульях»", ruOf: "«Двенадцати стульев»" },
    { tokens: 180_000, en: "The Master and Margarita", ruIn: "в «Мастере и Маргарите»", ruOf: "«Мастера и Маргариты»" },
    { tokens: 195_000, en: "the Iliad", ruIn: "в «Илиаде»", ruOf: "«Илиады»" },
    { tokens: 245_000, en: "Dune", ruIn: "в «Дюне»", ruOf: "«Дюны»" },
    { tokens: 268_000, en: "Moby-Dick", ruIn: "в «Моби Дике»", ruOf: "«Моби Дика»" },
    { tokens: 275_000, en: "Crime and Punishment", ruIn: "в «Преступлении и наказании»", ruOf: "«Преступления и наказания»" },
    { tokens: 455_000, en: "Anna Karenina", ruIn: "в «Анне Карениной»", ruOf: "«Анны Карениной»" },
    { tokens: 560_000, en: "Don Quixote", ruIn: "в «Дон Кихоте»", ruOf: "«Дон Кихота»" },
    { tokens: 625_000, en: "The Lord of the Rings", ruIn: "во «Властелине колец»", ruOf: "«Властелина колец»" },
    { tokens: 760_000, en: "War and Peace", ruIn: "в «Войне и мире»", ruOf: "«Войны и мира»" },
    { tokens: 860_000, en: "the complete Sherlock Holmes", ruIn: "во всех рассказах о Шерлоке Холмсе", ruOf: "всего Шерлока Холмса" },
    { tokens: 1_020_000, en: "the King James Bible", ruIn: "в Библии", ruOf: "Библии" },
    { tokens: 1_150_000, en: "the complete works of Shakespeare", ruIn: "в полном собрании Шекспира", ruOf: "полного собрания Шекспира" },
    { tokens: 1_300_000, en: "the whole Witcher saga", ruIn: "во всей саге о Ведьмаке", ruOf: "всей саги о Ведьмаке" },
    { tokens: 1_410_000, en: "all seven Harry Potter books", ruIn: "во всех семи книгах о Гарри Поттере", ruOf: "всех семи книг о Гарри Поттере" },
    { tokens: 1_650_000, en: "In Search of Lost Time", ruIn: "в «В поисках утраченного времени»", ruOf: "«В поисках утраченного времени»" },
    { tokens: 2_300_000, en: "A Song of Ice and Fire", ruIn: "в «Песни льда и пламени»", ruOf: "«Песни льда и пламени»" },
    { tokens: 52_000_000, en: "the printed Encyclopaedia Britannica", ruIn: "в бумажной «Британнике»", ruOf: "бумажной «Британники»" },
    { tokens: 6_000_000_000, en: "all of English Wikipedia", ruIn: "во всей английской Википедии", ruOf: "всей английской Википедии" },
  ];

  /* Сравнения не с книгами: страницы, стопка бумаги, чтение вслух и т.д.
     Каждое возвращает текст или "", если на таком масштабе оно бессмысленно. */
  const WORDS = (tokens) => tokens / 1.3;
  const MEASURES = [
    (tokens) => {
      const pages = Math.round(tokens / 650);
      return pages >= 3 ? t(`Если это распечатать, выйдет около ${number(pages)} страниц A4.`, `Printed out, that's about ${number(pages)} A4 pages.`) : "";
    },
    (tokens) => {
      const cm = tokens / 650 * 0.01;
      if (cm < 1) return "";
      const text = cm >= 100 ? `${number(+(cm / 100).toFixed(cm >= 1000 ? 0 : 1))} ${t("м", "m")}` : `${Math.round(cm)} ${t("см", "cm")}`;
      return t(`Стопка распечаток была бы высотой ${text}.`, `A stack of printouts would be ${text} tall.`);
    },
    (tokens) => {
      const hours = WORDS(tokens) / 150 / 60;
      if (hours < 1) return "";
      return hours >= 48
        ? t(`Чтобы прочитать всё это вслух без перерывов, понадобилось бы ${number(Math.round(hours / 24))} сут.`, `Reading it all aloud non-stop would take ${number(Math.round(hours / 24))} days.`)
        : t(`Чтобы прочитать всё это вслух, понадобилось бы ${number(Math.round(hours))} ч.`, `Reading it all aloud would take ${number(Math.round(hours))} hours.`);
    },
    (tokens) => {
      const days = WORDS(tokens) / 16_000;
      return days >= 2 ? t(`Столько слов обычный человек произносит примерно за ${number(Math.round(days))} дн.`, `That's what an average person says in about ${number(Math.round(days))} days.`) : "";
    },
    (tokens) => {
      const tweets = Math.round(tokens / 70);
      return tweets >= 50 ? t(`Это около ${number(tweets)} твитов максимальной длины.`, `That's about ${number(tweets)} maximum-length tweets.`) : "";
    },
    (tokens) => {
      const km = tokens * 4 * 2.5 / 1_000_000;
      if (km < 0.1) return "";
      const text = km >= 1 ? `${number(+km.toFixed(km >= 10 ? 0 : 1))} ${t("км", "km")}` : `${Math.round(km * 1000)} ${t("м", "m")}`;
      return t(`Одной строкой обычным шрифтом этот текст растянулся бы на ${text}.`, `Written on a single line in a normal font, it would stretch ${text}.`);
    },
    (tokens) => {
      const years = WORDS(tokens) / 250 / 60 / 2 / 365;   // по 2 часа чтения в день
      if (years < 1) return "";
      const n = Math.round(years), tail = n % 100, last = n % 10;
      const word = tail >= 11 && tail <= 14 ? "лет" : last === 1 ? "год" : last >= 2 && last <= 4 ? "года" : "лет";
      return t(`Читая по 2 часа в день, это пришлось бы читать ${number(n)} ${word}.`, `Reading 2 hours a day, it would take ${number(n)} years.`);
    },
  ];

  function localDay(date = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function compact(value) {
    const n = Number(value) || 0;
    if (n >= 1e9) return `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)}B`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
    return String(n);
  }

  function number(value) { return (Number(value) || 0).toLocaleString(en() ? "en-US" : "ru-RU"); }

  /** Как app.pricing.format_usd: $0 · <$0.0001 · $0.0042 · $0.315 · $12.40 · $1,204. */
  function usd(value) {
    const n = Number(value) || 0;
    if (!n) return "$0";
    if (n < 0.0001) return "<$0.0001";
    if (n < 0.01) return `$${n.toFixed(4)}`;
    if (n < 1) return `$${n.toFixed(3)}`;
    if (n < 100) return `$${n.toFixed(2)}`;
    return `$${Math.round(n).toLocaleString("en-US")}`;
  }

  function times(n) {
    const tail = n % 100, last = n % 10;
    if (tail >= 11 && tail <= 14) return "раз";
    if (last >= 2 && last <= 4) return "раза";
    return "раз";
  }

  function peakHour(hour) {
    if (hour === null || hour === undefined) return "—";
    if (!en()) return `${String(hour).padStart(2, "0")}:00`;
    const h = hour % 12 || 12;
    return `${h} ${hour < 12 ? "AM" : "PM"}`;
  }

  function bookLine(tokens, ref) {
    const ratio = tokens / ref.tokens;
    if (ratio >= 1.5) {
      const n = ratio >= 10 ? Math.round(ratio) : Math.round(ratio * 10) / 10;
      return en() ? `You've used ~${number(n)}× more tokens than ${ref.en}.`
        : `Вы использовали примерно в ${number(n)} ${Number.isInteger(n) ? times(n) : "раза"} больше токенов, чем ${ref.ruIn}.`;
    }
    if (ratio >= 0.85) return en() ? `You've used about as many tokens as ${ref.en}.` : `Вы использовали примерно столько же токенов, сколько ${ref.ruIn}.`;
    const pct = Math.max(1, Math.round(ratio * 100));
    return en() ? `That's about ${pct}% of ${ref.en}.` : `Это примерно ${pct}% от объёма ${ref.ruOf}.`;
  }

  /** Все сравнения, подходящие по масштабу: книги от 15% до ×5000 и прочие меры. */
  function comparisons(tokens) {
    if (!tokens) return [];
    const books = REFERENCES.filter((ref) => tokens / ref.tokens >= 0.15 && tokens / ref.tokens <= 5000).map((ref) => bookLine(tokens, ref));
    const measures = MEASURES.map((measure) => measure(tokens)).filter(Boolean);
    if (!books.length) books.push(bookLine(tokens, REFERENCES[0]));
    return [...books, ...measures];
  }

  function comparison(tokens, pick = Math.random()) {
    const list = comparisons(tokens);
    return list.length ? list[Math.floor(pick * list.length) % list.length] : "";
  }

  async function load() {
    state.loading = true;
    state.pick = Math.random();   // новое сравнение при каждом обновлении
    state.error = "";
    try {
      const params = new URLSearchParams({ range: state.range, today: localDay() });
      state.data = await api(`/api/code/stats?${params}`);
      state.loadedAt = Date.now();
    } catch (error) {
      state.error = error.message || String(error);
    }
    state.loading = false;
  }

  function tiles(d) {
    const favorite = d.favorite_model ? (typeof modelDisplayName === "function" ? modelDisplayName(d.favorite_model) : d.favorite_model) : "—";
    const rows = [
      [t("Сессии", "Sessions"), number(d.sessions)],
      [t("Сообщения", "Messages"), number(d.messages)],
      [t("Всего токенов", "Total tokens"), compact(d.tokens.total), `${t("вход", "input")} ${number(d.tokens.input)} · ${t("выход", "output")} ${number(d.tokens.output)}`],
      [t("Активных дней", "Active days"), number(d.active_days)],
      [t("Пиковый час", "Peak hour"), peakHour(d.peak_hour)],
      [t("Любимая модель", "Favorite model"), favorite, d.favorite_model || ""],
    ];
    return `<div class="cs-tiles">${rows.map(([label, value, title]) => `<div class="cs-tile" ${title ? `title="${esc(title)}"` : ""}>
      <span>${esc(label)}</span><b>${esc(value)}</b></div>`).join("")}</div>`;
  }

  /** Стоимость по ценам API: каталог моделей → прайс OpenRouter → встроенная таблица (app/pricing.py). */
  function costLine(d) {
    const cost = d.cost || {};
    const unknown = (cost.unknown || []).length;
    if (!(cost.api_equivalent > 0) && !unknown) return "";
    const notes = [];
    if (cost.api_billed > 0) notes.push(`${t("по API-ключам", "on API keys")} ${usd(cost.api_billed)}`);
    if (unknown) notes.push(t(`без цены: ${unknown} ${unknown === 1 ? "модель" : unknown < 5 ? "модели" : "моделей"}`,
      `no price for ${unknown} model${unknown === 1 ? "" : "s"}`));
    const title = t("Сколько стоили бы эти токены по публичным ценам API — из модуля «Каталог моделей», прайса OpenRouter или встроенной таблицы. Аккаунты с подпиской через Proxy денег не списывают: они расходуют лимиты подписки.",
      "What these tokens would cost at public API prices — from the Model catalog module, the OpenRouter price list or a built-in table. Subscription accounts in the Proxy are not charged per token: they spend the subscription quota.");
    return `<div class="cs-cost" title="${esc(title)}"><span>${esc(t("Стоимость по ценам API", "Cost at API prices"))}</span>
      <b>≈ ${esc(usd(cost.api_equivalent))}</b>${notes.length ? `<small>${esc(notes.join(" · "))}</small>` : ""}</div>`;
  }

  function heatmap(d) {
    const byDay = new Map((d.days || []).map((row) => [row.day, row]));
    const today = new Date(`${d.today || localDay()}T12:00:00`);
    const weekday = (today.getDay() + 6) % 7;                 // понедельник = 0
    const start = new Date(today);
    start.setDate(start.getDate() - weekday - (WEEKS - 1) * 7);
    const rangeStart = state.range === "all" ? null : new Date(today.getTime() - ((state.range === "7d" ? 7 : 30) - 1) * 86400000);
    const values = (d.days || []).map((row) => row.tokens || row.messages).filter((v) => v > 0).sort((a, b) => a - b);
    const level = (value) => {
      if (!value) return 0;
      const rank = values.findIndex((v) => v >= value) / Math.max(1, values.length - 1);
      return rank >= 0.75 ? 4 : rank >= 0.5 ? 3 : rank >= 0.25 ? 2 : 1;
    };
    const cells = [];
    for (let row = 0; row < 7; row += 1) {
      for (let week = 0; week < WEEKS; week += 1) {
        const date = new Date(start);
        date.setDate(start.getDate() + week * 7 + row);
        const key = localDay(date);
        const future = date > today;
        const outside = rangeStart && date < new Date(rangeStart.toDateString());
        const info = byDay.get(key);
        const label = `${date.toLocaleDateString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short", year: "numeric" })}: ${
          info ? `${number(info.messages)} ${t("сообщ.", "messages")} · ${compact(info.tokens)} ${t("токенов", "tokens")}` : t("нет активности", "no activity")}`;
        cells.push(`<i class="cs-cell l${future ? "x" : level(info ? (info.tokens || info.messages) : 0)} ${outside ? "dim" : ""}" style="grid-row:${row + 1};grid-column:${week + 1}" ${future ? "" : `title="${esc(label)}"`}></i>`);
      }
    }
    return `<div class="cs-heat" role="img" aria-label="${esc(t("Активность по дням за полгода", "Daily activity over half a year"))}">${cells.join("")}</div>`;
  }

  function modelsTab(d) {
    if (!(d.models || []).length) return `<div class="cs-empty">${esc(t("Пока нет данных о моделях.", "No model data yet."))}</div>`;
    return `<div class="cs-models">${d.models.map((row) => `<div class="cs-model">
      <span class="cs-model-name" title="${esc(row.model)}">${esc(typeof modelDisplayName === "function" ? modelDisplayName(row.model) : row.model)}</span>
      <span class="cs-bar"><i style="width:${Math.max(2, row.share)}%"></i></span>
      <span class="cs-model-num">${compact(row.tokens)} · ${number(row.messages)} ${t("сообщ.", "msg")}${row.cost > 0
        ? ` · <span title="${esc(t("по ценам API", "at API prices"))}">${esc(usd(row.cost))}</span>` : ""}</span></div>`).join("")}</div>`;
  }

  function markup() {
    const d = state.data;
    const head = `<div class="cs-head">
      <div class="cs-tabs" role="tablist">${[["overview", t("Обзор", "Overview")], ["models", t("Модели", "Models")]].map(([id, label]) =>
        `<button type="button" role="tab" class="${state.tab === id ? "on" : ""}" data-cs-tab="${id}" aria-selected="${state.tab === id}">${esc(label)}</button>`).join("")}</div>
      <div class="cs-tabs cs-range">${[["all", t("Всё", "All")], ["30d", t("30д", "30d")], ["7d", t("7д", "7d")]].map(([id, label]) =>
        `<button type="button" class="${state.range === id ? "on" : ""}" data-cs-range="${id}">${esc(label)}</button>`).join("")}</div></div>`;
    if (state.error) return head + `<div class="cs-empty">${esc(state.error)}</div>`;
    if (!d) return head + `<div class="cs-empty">${esc(t("Загружаю статистику…", "Loading statistics…"))}</div>`;
    if (!d.messages) {
      return head + `<div class="cs-empty">${esc(t("Статистика появится после первой сессии MemuCode.", "Statistics appear after your first MemuCode session."))}</div>`;
    }
    const body = state.tab === "models" ? modelsTab(d) : tiles(d) + costLine(d) + heatmap(d);
    const fact = comparison(d.tokens.total, state.pick);
    const estimated = d.estimated ? t("Часть токенов оценена по длине текста.", "Some token counts are estimated from text length.") : "";
    return head + body + (fact || estimated ? `<p class="cs-foot">${fact ? `<button type="button" class="cs-fact" data-cs-fact title="${esc(t("Другое сравнение", "Another comparison"))}">${esc(fact)}</button>` : ""}${estimated ? ` <span>${esc(estimated)}</span>` : ""}</p>` : "");
  }

  function bind(root) {
    const fact = root.querySelector("[data-cs-fact]");
    if (fact) fact.onclick = () => {
      // Следующее сравнение — любое, кроме текущего.
      const count = Math.max(1, comparisons(state.data?.tokens?.total || 0).length);
      const current = Math.floor(state.pick * count) % count;
      const next = count > 1 ? (current + 1 + Math.floor(Math.random() * (count - 1))) % count : 0;
      state.pick = (next + 0.5) / count;
      paint(root);
    };
    root.querySelectorAll("[data-cs-tab]").forEach((button) => button.onclick = () => { state.tab = button.dataset.csTab; paint(root); });
    root.querySelectorAll("[data-cs-range]").forEach((button) => button.onclick = async () => {
      state.range = button.dataset.csRange;
      paint(root);
      await load();
      paint(root);
    });
  }

  function paint(root) {
    if (!root) return;
    root.innerHTML = markup();
    bind(root);
  }

  /** Карточка на главной MemuCode. Вызывается часто — сеть не чаще раза в 20 с. */
  async function refresh(force = false) {
    const root = document.getElementById("code-stats");
    if (!root || S.mode !== "code") return;
    root.hidden = false;
    const fresh = state.data && Date.now() - state.loadedAt < 20_000;
    if (!root.innerHTML || !fresh || force) paint(root);
    if ((fresh && !force) || state.loading) return;
    await load();
    paint(root);
  }

  /** Панель по команде /stats. */
  async function openPanel() {
    panel(`<h2>${esc(t("Статистика MemuCode", "MemuCode statistics"))}</h2><div class="code-stats in-panel" id="code-stats-panel"></div>`, "code-stats-panel");
    const root = document.getElementById("code-stats-panel");
    paint(root);
    await load();
    paint(root);
  }

  /** Раунд модели в MemuCode — для статистики (ошибки записи не мешают работе). */
  function record(event) {
    try {
      const now = new Date();
      api("/api/code/stats/record", { method: "POST", body: { ...event, day: localDay(now), hour: now.getHours() } })
        .then(() => { state.loadedAt = 0; }).catch(() => {});
    } catch (error) { /* статистика не должна ломать ответ */ }
  }

  return { refresh, openPanel, record, comparison, comparisons, _compact: compact, _usd: usd };
})();
