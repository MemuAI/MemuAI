/* MemuAI знает сама себя: упоминание статей документации и чтение их моделью.
 *
 * «@!» в поле ввода открывает список статей; выбранная вставляется как @!id. При отправке текст
 * упомянутых статей прикладывается к запросу, так что модель отвечает по документации, а не по
 * памяти. Модель тоже может написать @!id — в сообщении это становится кнопкой, открывающей
 * статью в «Настройки → Документация».
 *
 * Читать статьи сама (инструменты docs_search и docs_read) модель может, только пока включён
 * встроенный скилл «Справка по MemuAI»: он и описывает ей эти запросы. Скилл можно выключить
 * или отредактировать в «Скиллы ИИ» — тогда остаются только ручные упоминания.
 */
"use strict";
const MemuSelfDocs = (() => {
  const SKILL_ID = "memuai_guide";
  const MAX_MENTIONS = 4, MAX_CHARS = 14000;
  const state = { lang: "", pages: [], byId: new Map(), loading: null };

  async function load() {
    const lang = PG_LANG === "en" ? "en" : "ru";
    if (state.lang === lang && state.pages.length) return state.pages;
    if (state.loading) return state.loading;
    state.loading = (async () => {
      try {
        const catalog = await api(`/api/pg/docs?lang=${lang}`);
        const pages = [];
        for (const group of catalog.groups || []) {
          for (const page of group.pages || []) pages.push({ id: page.id, title: page.title || page.id, summary: page.summary || "", group: group.title || "" });
        }
        state.pages = pages;
        state.byId = new Map(pages.map((page) => [page.id, page]));
        state.lang = lang;
      } catch (error) { /* документации нет — упоминания просто остаются текстом */ }
      state.loading = null;
      return state.pages;
    })();
    return state.loading;
  }

  const known = (id) => state.byId.has(id);
  const enabled = () => typeof getSkills === "function" && getSkills().some((skill) => skill.id === SKILL_ID && skill.enabled);

  /* ------------------------------------------------------------ поле ввода */
  /** Rows for the composer menu while the text ends with "@!…", or null when it does not. */
  function rows(value) {
    const match = String(value || "").match(/(?:^|\s)@!([\p{L}\d-]*)$/u);
    if (!match) return null;
    if (!state.pages.length) { load().then(() => { if (typeof renderSlashMenu === "function") renderSlashMenu(); }); return []; }
    const needle = match[1].toLowerCase();
    const scored = state.pages.map((page) => {
      const id = page.id.toLowerCase(), title = page.title.toLowerCase();
      const score = !needle ? 1 : id.startsWith(needle) ? 5 : title.startsWith(needle) ? 4
        : id.includes(needle) ? 3 : title.includes(needle) ? 2 : page.summary.toLowerCase().includes(needle) ? 1 : 0;
      return [score, page];
    }).filter(([score]) => score > 0).sort((a, b) => b[0] - a[0]);
    return scored.slice(0, 9).map(([, page]) => [`@!${page.id}`, page.title, page.summary || page.group, false, "doc"]);
  }

  /** Ids of the known articles mentioned in a text, in order, without repeats. */
  function mentions(text) {
    const found = [];
    for (const match of String(text || "").matchAll(/(?:^|[\s(>«"'])@!([a-z0-9][a-z0-9-]{0,63})/g)) {
      if (known(match[1]) && !found.includes(match[1])) found.push(match[1]);
    }
    return found;
  }

  /* ------------------------------------------------------------ запрос */
  /** The mentioned articles as a context block for the request ("" when there are none). */
  async function mentionContext(text) {
    await load();
    const ids = mentions(text).slice(0, MAX_MENTIONS);
    if (!ids.length) return "";
    const lang = PG_LANG === "en" ? "en" : "ru";
    const parts = [];
    for (const id of ids) {
      try {
        const page = await api(`/api/pg/docs/page?lang=${lang}&id=${encodeURIComponent(id)}`);
        const body = String(page.body || "");
        parts.push(`<article id="${id}" title="${String(page.title || id).replace(/"/g, "'")}">\n${body.slice(0, MAX_CHARS)}${body.length > MAX_CHARS ? "\n[…the article continues; call docs_read with a section for the rest…]" : ""}\n</article>`);
      } catch (error) { /* статья исчезла — пропускаем */ }
    }
    if (!parts.length) return "";
    return "<memuai_documentation>\nThe user pointed at these articles of the MemuAI documentation with @!mentions. They are the authoritative description " +
      "of how MemuAI works: answer from them, quote settings and button names exactly, and say so when they do not cover the question.\n" +
      parts.join("\n") + "\n</memuai_documentation>";
  }

  /** What the enabled skill adds to the system prompt: its instructions and the list of articles. */
  function systemBlock() {
    if (!enabled() || !state.pages.length) return "";
    const skill = getSkills().find((item) => item.id === SKILL_ID);
    const index = state.pages.map((page) => `${page.id} — ${page.title}`).join("\n");
    return "<memuai_guide>\n" + String(skill.prompt || "").trim() + "\n\n<documentation_index>\n" + index + "\n</documentation_index>\n</memuai_guide>";
  }

  /* ------------------------------------------------------------ показ */
  /** "@!id" in rendered text becomes a button that opens the article. Code is left alone. */
  function chips(html) {
    if (!state.pages.length || html.indexOf("@!") < 0) return html;
    return html.split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>|<a\b[\s\S]*?<\/a>|<[^>]+>)/).map((part, index) => (index % 2 ? part
      : part.replace(/(^|[\s(>«"'])@!([a-z0-9][a-z0-9-]{0,63})(#[a-z0-9а-яё-]{1,80})?/gi, (whole, prefix, id, anchor) => {
        const page = state.byId.get(id.toLowerCase());
        if (!page) return whole;
        const title = String(page.title).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
        return `${prefix}<button type="button" class="doc-chip" data-doc="${page.id}" data-anchor="${anchor ? anchor.slice(1) : ""}" title="${PG_LANG === "en" ? "Open in the documentation" : "Открыть в документации"}">${title}</button>`;
      }))).join("");
  }

  document.addEventListener("click", (event) => {
    const chip = event.target.closest?.(".doc-chip");
    if (!chip || typeof MemuDocs === "undefined") return;
    event.preventDefault();
    MemuDocs.open(chip.dataset.doc, chip.dataset.anchor || "");
  });

  return { load, rows, mentions, mentionContext, systemBlock, chips, known, enabled, SKILL_ID,
    get pages() { return state.pages; } };
})();

/* Подключение к окну: список «@!» в меню поля ввода и кнопки статей в сообщениях. */
(() => {
  const baseMatches = slashMatches;
  slashMatches = function () {
    const docs = MemuSelfDocs.rows($("#input")?.value || "");
    return docs === null ? baseMatches() : docs;
  };
  const baseMd = md;
  md = function (text) { return MemuSelfDocs.chips(baseMd(text)); };
  MemuSelfDocs.load().then(() => { if (typeof renderMessages === "function" && S.messages?.some((m) => String(m.content || "").includes("@!"))) renderMessages(); });
})();
