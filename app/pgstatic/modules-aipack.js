/* MemuAI modules «AI pack» and package validation (Round 29).
 *
 * 1) A documentation widget: a fenced block ```memu-widget\nai-pack\n``` inside a docs article
 *    (Settings → Documentation → Modules → «AI pack») becomes a card that downloads the pack
 *    (RU / EN, full or compact without the test stand), copies it, and downloads the Node.js
 *    test stand memu-module-test.js. Backend: app/modules_ai_pack.py (/api/pg/modules-ai/*).
 * 2) «Validate and import»: Settings → Modules → Import gets a card that takes an AI answer with
 *    the files in code blocks, a ZIP, a folder, or a *.memu-module.json, checks it with
 *    app/modules_validate.py (POST /api/pg/modules-ai/validate) and imports the normalized package.
 *
 * Relies on app.js helpers (icon, toast, copyText, downloadTextFile, PG_LANG) loaded before it.
 * Global: window.MemuModuleTools = { widget, hydrate, enhanceImport, validate, reportText }.
 */
"use strict";
const MemuModuleTools = (() => {
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const L = (ru, english) => (en() ? english : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const glyph = (name) => (typeof icon === "function" ? icon(name) : "");
  const note = (text, type = "ok") => { try { toast(text, type); } catch (_) {} };
  const $ = (selector, node) => (node || document).querySelector(selector);
  const WIDGETS = new Set(["ai-pack"]);
  const MAX_FILE = 8 * 1024 * 1024;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      method: options.method || "GET",
      headers: options.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.data = data;
      throw error;
    }
    return data;
  }

  async function saveText(name, text, mime) {
    if (typeof downloadTextFile === "function") return downloadTextFile(name, text, mime);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: mime }));
    link.download = name;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1500);
    return null;
  }

  const kb = (bytes) => (bytes >= 1024 * 1024 ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} ${L("КБ", "KB")}`);

  /* ------------------------------ docs widget ------------------------------ */

  /** HTML placeholder for a ```memu-widget fence; docs.js calls this from codeBlock(). */
  function widget(source) {
    const name = String(source || "").trim().split(/\s+/)[0] || "";
    if (!WIDGETS.has(name)) return "";
    return `<div class="memu-docs-widget" data-memu-widget="${esc(name)}"></div>`;
  }

  /** Fill every widget placeholder inside a rendered article. */
  function hydrate(root) {
    (root || document).querySelectorAll("[data-memu-widget]:not([data-ready])").forEach((node) => {
      node.dataset.ready = "1";
      if (node.dataset.memuWidget === "ai-pack") drawPackWidget(node);
    });
  }

  function drawPackWidget(node) {
    const startLang = en() ? "en" : "ru";
    node.innerHTML = `
      <div class="aipack-card">
        <div class="aipack-head">
          <span class="aipack-badge">${glyph("sparkle")}</span>
          <div><b>${L("ИИ-пакет для модулей", "Modules AI pack")}</b>
            <small>${L("Один файл .txt: инструкции для ИИ, справочник моста Memu, правила песочницы, вся документация по модулям и рабочие примеры.",
              "One .txt file: instructions for the AI, the Memu bridge reference, the sandbox rules, every modules article and working examples.")}</small></div>
        </div>
        <div class="aipack-options">
          <div class="aipack-seg" role="radiogroup" aria-label="${L("Язык пакета", "Pack language")}">
            <button type="button" role="radio" data-pack-lang="ru" aria-checked="${startLang === "ru"}">RU</button>
            <button type="button" role="radio" data-pack-lang="en" aria-checked="${startLang === "en"}">EN</button>
          </div>
          <label class="aipack-check"><input type="checkbox" data-pack-compact>
            <span>${L("Компактный (без тестового стенда)", "Compact (without the test stand)")}</span></label>
        </div>
        <div class="aipack-meta" data-pack-meta>${L("Размер считается…", "Measuring…")}</div>
        <div class="aipack-actions">
          <button type="button" class="pbtn" data-pack-download>${glyph("download")} ${L("Скачать пакет", "Download the pack")}</button>
          <button type="button" class="pbtn ghost" data-pack-copy>${glyph("copy")} ${L("Копировать", "Copy")}</button>
          <button type="button" class="pbtn ghost" data-pack-harness>${glyph("terminal")} ${L("Тестовый стенд (.js)", "Test stand (.js)")}</button>
          <button type="button" class="pbtn ghost" data-pack-import>${glyph("check")} ${L("Проверить и импортировать", "Validate and import")}</button>
        </div>
        <p class="aipack-hint">${L("Приложите файл к любому ИИ-агенту и напишите: «Сделай модуль MemuAI, который …, инструкции в файле». Ответ вставьте в «Настройки → Модули → Импорт → Проверить и импортировать».",
          "Attach the file to any AI agent and write: “Make a MemuAI module that …, the instructions are in the file”. Paste the answer into Settings → Modules → Import → Validate and import.")}</p>
      </div>`;
    const state = { lang: startLang, compact: false, cache: new Map(), seq: 0, open: false };
    const lang = () => (en() ? "en" : "ru");
    // An estimate has no false precision: three significant digits, the range in thousands (tokens.js).
    const num = (value) => (window.MemuTokens ? MemuTokens.approx(value) : Math.round(Number(value) || 0)).toLocaleString(en() ? "en-US" : "ru-RU");
    const range = (low, high) => (window.MemuTokens ? MemuTokens.shortRange(low, high, lang()) : `${num(low)}–${num(high)}`);
    // Tokens depend on the language of the pack and on the model: the typical count is marked as an estimate,
    // the range and every family's count sit one click away (app/token_estimate.py).
    const tokensChip = (data) => `<button type="button" class="aipack-tok" data-pack-tokens aria-expanded="${state.open}"
        title="${esc(L("Оценка: у каждого семейства моделей свой токенизатор, русский текст стоит заметно больше английского. Нажмите — число для каждой модели.",
          "An estimate: every model family has its own tokenizer, and Russian text costs noticeably more than English. Click for each model's count."))}">`
      + `≈ ${num(data.tokens)} ${L("токенов", "tokens")} <em>${L("оценка", "estimate")}</em>`
      + (data.tokens_high > data.tokens_low ? ` <i>${range(data.tokens_low, data.tokens_high)}</i>` : "")
      + `<svg class="aipack-caret" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`;
    const tokensDetails = (data) => {
      const rows = (data.tokens_families || []).map((row) => `<li><span>${esc(row.label)}</span>`
        + `<b>${row.exact ? `${Math.round(row.tokens).toLocaleString(en() ? "en-US" : "ru-RU")} <em>${L("точно", "exact")}</em>` : `≈ ${num(row.tokens)}`}</b></li>`).join("");
      const mix = data.tokens_mix || {};
      const share = [["cyrillic", "кириллица", "Cyrillic"], ["latin", "латиница", "Latin"], ["code", "цифры и знаки", "digits and symbols"],
        ["cjk", "CJK", "CJK"], ["other", "прочее", "other"]].filter(([key]) => (mix[key] || 0) >= 0.01)
        .map(([key, ru, english]) => `${L(ru, english)} ${Math.round(mix[key] * 100)}%`).join(" · ");
      return `<div class="aipack-models" ${state.open ? "" : "hidden"}><ul>${rows}</ul>`
        + `<small>${L("Текст пакета", "Pack text")}: ${esc(share)}. ${L("Русская проза стоит в 1,3–2 раза больше токенов, чем английская того же смысла; код — одинаково.",
          "Russian prose costs 1.3–2× the tokens of the same English text; code costs the same.")}</small></div>`;
    };
    const meta = $("[data-pack-meta]", node);
    const key = () => `${state.lang}:${state.compact ? 0 : 1}`;

    async function load() {
      const id = key();
      if (state.cache.has(id)) return state.cache.get(id);
      const data = await api(`/api/pg/modules-ai/pack?format=json&lang=${state.lang}&harness=${state.compact ? 0 : 1}`);
      state.cache.set(id, data);
      return data;
    }
    async function refreshMeta() {
      const seq = ++state.seq;
      meta.textContent = L("Размер считается…", "Measuring…");
      try {
        const data = await load();
        if (seq !== state.seq) return;
        meta.innerHTML = `<span>${esc(data.name)}</span><span>${kb(data.bytes)}</span>`
          + tokensChip(data)
          + `<span>${data.pages} ${L("стр. документации", "doc pages")}</span>`
          + `<span>${(data.examples || []).length} ${L("примера", "examples")}</span>`
          + tokensDetails(data);
        const toggle = $("[data-pack-tokens]", meta);
        if (toggle) toggle.onclick = () => {
          state.open = !state.open;
          toggle.setAttribute("aria-expanded", String(state.open));
          $(".aipack-models", meta).hidden = !state.open;
        };
      } catch (error) {
        if (seq === state.seq) meta.textContent = L("Пакет не собран: ", "The pack failed: ") + error.message;
      }
    }
    node.querySelectorAll("[data-pack-lang]").forEach((button) => {
      button.onclick = () => {
        state.lang = button.dataset.packLang;
        node.querySelectorAll("[data-pack-lang]").forEach((b) => b.setAttribute("aria-checked", String(b === button)));
        refreshMeta();
      };
    });
    $("[data-pack-compact]", node).onchange = (event) => { state.compact = event.target.checked; refreshMeta(); };
    $("[data-pack-download]", node).onclick = async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const data = await load();
        await saveText(data.name, data.text, "text/plain;charset=utf-8");
      } catch (error) { note(error.message, "warn"); }
      finally { button.disabled = false; }
    };
    $("[data-pack-copy]", node).onclick = async () => {
      try {
        const data = await load();
        if (typeof copyText === "function") await copyText(data.text);
        else { await navigator.clipboard.writeText(data.text); note(L("Скопировано", "Copied")); }
      } catch (error) { note(error.message, "warn"); }
    };
    $("[data-pack-harness]", node).onclick = async () => {
      try {
        const response = await fetch("/api/pg/modules-ai/harness");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await saveText("memu-module-test.js", await response.text(), "text/javascript");
      } catch (error) { note(error.message, "warn"); }
    };
    $("[data-pack-import]", node).onclick = () => openImport();
    refreshMeta();
  }

  /** Settings → Modules → Import (the settings shell may need to switch tabs first). */
  async function openImport() {
    try {
      if (typeof currentSettingsTab !== "undefined") currentSettingsTab = "modules";
      const tab = document.querySelector('.settings-tab-btn[data-set-tab="modules"]');
      if (tab) tab.click();
      else if (typeof showSettings === "function") await showSettings(true);
      setTimeout(() => {
        try { window.MemuModules && MemuModules.mountSettings("import"); } catch (_) {}
        const card = document.querySelector(".aipack-import");
        if (card) card.scrollIntoView({ block: "start", behavior: "smooth" });
      }, 120);
    } catch (error) { note(error.message, "warn"); }
  }

  /* ---------------------------- validate & import --------------------------- */

  function validate(body) { return api("/api/pg/modules-ai/validate", { method: "POST", body }); }

  /** The report as plain text: paste it back to the AI so it fixes its module. */
  function reportText(report) {
    const english = en();
    const lines = [english ? "MemuAI module check:" : "Проверка модуля MemuAI:"];
    const rows = (report && report.problems) || [];
    if (!rows.length) lines.push(english ? "No problems found." : "Проблем не найдено.");
    rows.forEach((row) => {
      const where = row.field ? `${row.field}${row.line ? `:${row.line}` : ""}` : "";
      lines.push(`- [${row.level}] ${where ? where + " — " : ""}${english ? row.en : row.ru}`);
    });
    if (rows.some((row) => row.level === "error")) {
      lines.push(english ? "Fix every [error] and send all files again in full." : "Исправь все [error] и пришли все файлы заново целиком.");
    }
    return lines.join("\n");
  }

  const LEVELS = {
    error: ["close", "Ошибка", "Error"],
    warn: ["bell", "Внимание", "Warning"],
    info: ["info", "Подсказка", "Hint"],
  };

  function drawReport(box, report) {
    const counts = report.counts || {};
    const rows = report.problems || [];
    const stats = report.stats || {};
    const title = (en() ? report.title_en : "") || report.title || report.id || L("Без названия", "Untitled");
    box.innerHTML = `
      <div class="aipack-verdict ${report.ok ? "ok" : "bad"}">
        ${glyph(report.ok ? "check" : "close")}
        <div><b>${report.ok ? L("Можно импортировать", "Ready to import") : L("Есть ошибки — импорт закрыт", "There are errors — import is blocked")}</b>
          <small>${esc(title)}${report.id ? ` · <code>${esc(report.id)}</code>` : ""} · ${counts.error || 0} ${L("ошиб.", "errors")} · ${counts.warn || 0} ${L("предупр.", "warnings")} · ${counts.info || 0} ${L("подск.", "hints")}</small></div>
      </div>
      <div class="aipack-stats">
        <span>HTML ${kb(stats.html || 0)}</span><span>CSS ${kb(stats.css || 0)}</span><span>JS ${kb(stats.js || 0)}</span>
        ${(stats.locales || []).length ? `<span>${L("Языки", "Locales")}: ${esc(stats.locales.join(", "))}</span>` : ""}
        ${(report.permissions || []).length ? `<span>${L("Разрешения", "Permissions")}: ${esc(report.permissions.join(", "))}</span>` : ""}
      </div>
      ${rows.length ? `<ul class="aipack-problems">${rows.map((row) => {
        const meta = LEVELS[row.level] || LEVELS.info;
        return `<li class="lvl-${esc(row.level)}"><span class="aipack-lvl" title="${esc(L(meta[1], meta[2]))}">${glyph(meta[0])}</span>`
          + `<span class="aipack-where">${esc(row.field || "")}${row.line ? `:${row.line}` : ""}</span>`
          + `<span class="aipack-msg">${esc(en() ? row.en : row.ru)}</span></li>`;
      }).join("")}</ul>` : `<p class="module-hint">${L("Замечаний нет.", "No remarks.")}</p>`}`;
  }

  async function readFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return null;
    const total = files.reduce((sum, file) => sum + file.size, 0);
    if (total > MAX_FILE) throw new Error(L("Слишком большой файл (больше 8 МБ)", "The file is too large (over 8 MB)"));
    if (files.length === 1 && /\.zip$/i.test(files[0].name)) {
      const buffer = new Uint8Array(await files[0].arrayBuffer());
      let binary = "";
      for (let i = 0; i < buffer.length; i += 0x8000) binary += String.fromCharCode.apply(null, buffer.subarray(i, i + 0x8000));
      return { body: { zip: btoa(binary), name: files[0].name }, label: files[0].name };
    }
    if (files.length === 1 && !files[0].webkitRelativePath) {
      const text = await files[0].text();
      return { body: { text }, label: files[0].name, text };
    }
    // a folder (webkitdirectory) or several files: keep the package files by their base name
    const out = {};
    for (const file of files) {
      const base = file.name.toLowerCase();
      if (["manifest.json", "index.html", "style.css", "main.js", "i18n.json"].includes(base)) out[base] = await file.text();
    }
    const folder = (files[0].webkitRelativePath || "").split("/")[0] || L("папка", "folder");
    return { body: { files: out }, label: folder };
  }

  /** Called by modules.js renderImport(host): adds the «Validate and import» card on top. */
  function enhanceImport(host, hooks = {}) {
    const pane = host && host.querySelector('.modules-pane[data-view="import"]');
    if (!pane || pane.querySelector(".aipack-import")) return;
    const card = document.createElement("div");
    card.className = "module-wizard-card aipack-import";
    card.innerHTML = `
      <h4>${glyph("check")} ${L("Проверить и импортировать", "Validate and import")}</h4>
      <p class="module-hint">${L("Вставьте ответ ИИ (файлы в блоках кода), JSON-пакет — или выберите ZIP, папку модуля или файл. MemuAI проверит пакет по правилам песочницы до установки.",
        "Paste an AI answer (files in code blocks) or a JSON package — or choose a ZIP, the module folder or a file. MemuAI checks the package against the sandbox rules before installing it.")}</p>
      <textarea class="ptexta" data-av-text rows="7" spellcheck="false" placeholder="${esc(L("```json manifest.json … ``` ```html index.html … ``` …", "```json manifest.json … ``` ```html index.html … ``` …"))}"></textarea>
      <div class="mu-row aipack-actions">
        <input type="file" data-av-file accept=".zip,.json,.txt,.md,application/zip,application/json,text/plain" hidden>
        <input type="file" data-av-folder webkitdirectory multiple hidden>
        <button type="button" class="pbtn ghost" data-av-pick>${glyph("attach")} ${L("ZIP или файл", "ZIP or file")}</button>
        <button type="button" class="pbtn ghost" data-av-pick-folder>${glyph("folder")} ${L("Папка", "Folder")}</button>
        <span class="mu-grow"></span>
        <button type="button" class="pbtn" data-av-check>${glyph("check")} ${L("Проверить", "Validate")}</button>
      </div>
      <div class="aipack-report" data-av-report aria-live="polite"></div>
      <div class="mu-row aipack-actions" data-av-after hidden>
        <button type="button" class="pbtn ghost" data-av-copy>${glyph("copy")} ${L("Отчёт для ИИ", "Report for the AI")}</button>
        <span class="mu-grow"></span>
        <button type="button" class="pbtn" data-av-import>${glyph("download")} ${L("Импортировать", "Import")}</button>
      </div>`;
    const head = pane.querySelector(".module-wizard-card");
    if (head) pane.insertBefore(card, head); else pane.appendChild(card);
    const reportBox = $("[data-av-report]", card);
    const after = $("[data-av-after]", card);
    const importButton = $("[data-av-import]", card);
    let last = null;

    async function run(body, label) {
      reportBox.innerHTML = `<p class="module-hint"><span class="mu-spinner"></span> ${L("Проверяю", "Validating")}${label ? ` ${esc(label)}` : ""}…</p>`;
      after.hidden = true;
      last = null;
      try {
        last = await validate(body);
        drawReport(reportBox, last);
        after.hidden = false;
        importButton.disabled = !last.ok;
        importButton.title = last.ok ? "" : L("Сначала исправьте ошибки", "Fix the errors first");
      } catch (error) {
        reportBox.innerHTML = `<div class="aipack-verdict bad">${glyph("close")}<div><b>${L("Пакет не прочитан", "Could not read the package")}</b><small>${esc(error.message)}</small></div></div>`;
      }
    }
    $("[data-av-check]", card).onclick = () => {
      const text = $("[data-av-text]", card).value;
      if (!text.trim()) { note(L("Вставьте ответ ИИ или пакет", "Paste an AI answer or a package"), "warn"); return; }
      run({ text });
    };
    $("[data-av-pick]", card).onclick = () => $("[data-av-file]", card).click();
    $("[data-av-pick-folder]", card).onclick = () => $("[data-av-folder]", card).click();
    const onFiles = async (event) => {
      try {
        const picked = await readFiles(event.target.files);
        event.target.value = "";
        if (!picked) return;
        if (picked.text !== undefined && picked.text.length < 400000) $("[data-av-text]", card).value = picked.text;
        await run(picked.body, picked.label);
      } catch (error) { note(error.message, "warn"); }
    };
    $("[data-av-file]", card).onchange = onFiles;
    $("[data-av-folder]", card).onchange = onFiles;
    $("[data-av-copy]", card).onclick = () => {
      if (!last) return;
      const text = reportText(last);
      if (typeof copyText === "function") copyText(text);
      else navigator.clipboard.writeText(text).then(() => note(L("Скопировано", "Copied")));
    };
    importButton.onclick = async () => {
      if (!last || !last.ok || typeof hooks.doImport !== "function") return;
      importButton.disabled = true;
      try { await hooks.doImport(last.package); }
      finally { importButton.disabled = !(last && last.ok); }
    };
  }

  return Object.freeze({ widget, hydrate, enhanceImport, validate, reportText, openImport });
})();
window.MemuModuleTools = MemuModuleTools;
