

const MemuToolDetails = (() => {
  const en = () => typeof PG_LANG !== "undefined" && PG_LANG === "en";
  const L = (ru, enText) => (en() ? enText : ru);
  const e = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ic = (name) => (typeof icon === "function" ? icon(name) : "");
  const cut = (value, size) => { const text = String(value ?? "").replace(/\s+/g, " ").trim(); return text.length > size ? text.slice(0, size - 1) + "…" : text; };
  const host = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch (error) { return ""; } };
  const safeUrl = (url) => (/^https?:\/\//i.test(String(url || "")) ? String(url) : "");
  const num = (value) => (Number.isFinite(+value) ? +value : 0);
  const plural = (n, one, few, many) => {
    if (en()) return `${n} ${n === 1 ? one[1] : one[2] || one[1] + "s"}`;
    const mod10 = n % 10, mod100 = n % 100;
    return `${n} ${mod10 === 1 && mod100 !== 11 ? one[0] : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many}`;
  };
  const size = (bytes) => { const n = num(bytes); const u = en() ? ["B", "KB", "MB", "GB", "TB"] : ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let i = 0, v = n; while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
    return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`; };
  const dur = (ms) => (ms > 0 ? (ms < 1000 ? `${Math.max(1, Math.round(ms / 10) * 10)} ${L("мс", "ms")}` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} ${L("с", "s")}`) : "");

  const links = (rows, size = 8) => (Array.isArray(rows) ? rows : []).filter((row) => row && typeof row === "object")
    .map((row) => ({ title: cut(row.title || row.name || row.url || "", 140), url: safeUrl(row.url || row.link || ""),
      snippet: cut(row.snippet || row.content || row.description || "", 180) }))
    .filter((row) => row.title || row.url).slice(0, size);

  function capture(tool, args, result) {
    const name = String(tool || "");
    const r = result && typeof result === "object" ? result : {};
    const a = args && typeof args === "object" ? args : {};
    const d = {};
    try {
      if (name === "web_search") {
        d.query = cut(r.query || a.query || "", 200);
        if (Array.isArray(r.queries) && r.queries.length > 1) d.queries = r.queries.slice(0, 4).map((q) => cut(q, 160));
        d.count = num(r.count || (r.results || []).length);
        d.links = links(r.results);
        if (r.answer?.text) d.answer = { text: cut(r.answer.text, 300), source: cut(r.answer.source || "", 80), url: safeUrl(r.answer.url) };
        if (Array.isArray(r.pages) && r.pages.length) d.pages = links(r.pages, 4);
      } else if (name === "web_fetch") {
        d.url = safeUrl(r.url || a.url) || cut(a.url || "", 300);
        d.title = cut(r.title || "", 160);
        d.chars = num(r.chars || String(r.text || "").length);
        d.truncated = !!r.truncated;
      } else if (name === "docs_search") {
        d.query = cut(r.query || a.query || "", 200);
        d.docs = (r.results || []).slice(0, 8).map((row) => ({ id: String(row.id || ""), title: cut(row.title || row.id, 100), section: cut(row.section || "", 100), anchor: String(row.anchor || "") }));
      } else if (name === "docs_read") {
        d.id = String(r.id || a.id || "").replace(/^@?!?/, "");
        d.title = cut(r.title || d.id, 120);
        d.summary = cut(r.summary || "", 260);
        d.section = cut(a.section || "", 100);
        d.sections = (r.sections || []).slice(0, 10).map((s) => cut(s, 80));
        d.truncated = !!r.truncated;
      } else if (name === "product_lookup") {
        d.query = cut(a.query || r.query || "", 200);
        d.offers = (r.offers || []).slice(0, 6).map((o) => ({ title: cut(o.title || o.name || "", 120), shop: cut(o.shop || o.store || o.source || host(o.url), 60),
          price: o.price && typeof o.price === "object" ? `${o.price.amount ?? ""} ${o.price.currency || ""}`.trim() : cut(o.price || "", 40), url: safeUrl(o.url) }));
      } else if (/^code_(?:run|terminal)$/.test(name)) {
        d.command = cut(r.command || a.command || "", 400);
        if (r.exit_code !== undefined && r.exit_code !== null) d.exit = r.exit_code;
        d.output = String(r.stdout || r.output || "").slice(-1200);
        d.stderr = String(r.stderr || "").slice(-600);
      } else if (/^(?:work|code)_/.test(name) || name === "project_tree" || name === "file_exists") {
        d.path = cut(r.path || a.path || a.file || "", 300);
        if (r.from || a.from) d.from = cut(r.from || a.from, 300);
        if (r.to || a.to) d.to = cut(r.to || a.to, 300);
        if (a.pattern || a.query || a.glob) d.query = cut(a.pattern || a.query || a.glob, 200);
        if (Array.isArray(r.matches)) d.matches = r.matches.slice(0, 8).map((m) => cut(typeof m === "string" ? m : `${m.path || m.file || ""}${m.line ? ":" + m.line : ""}  ${m.text || m.preview || ""}`, 200));
        if (Array.isArray(r.files)) d.files = r.files.slice(0, 12).map((f) => cut(typeof f === "string" ? f : f.path || f.name || "", 200));
        if (r.count !== undefined) d.count = num(r.count);
        if (r.total_lines) d.lines = r.start_line ? `${r.start_line}–${r.end_line} / ${r.total_lines}` : String(r.total_lines);
        if (r.created !== undefined) d.created = !!r.created;
      } else if (name.startsWith("mcp_")) {
        d.server = cut(r.server || "", 60);
        d.remote = cut(r.remote_tool || "", 80);
      } else if (name === "memory_save") {
        d.items = (a.items || []).slice(0, 6).map((item) => ({ text: cut(item.text, 240), updated: !!item.updated }));
        d.pending = num(a.pending);
      } else if (name === "current_time" || name === "app_version" || name === "working_directory") {
        d.value = cut(typeof r.value === "string" ? r.value : JSON.stringify(r.value ?? ""), 120);
      } else if (name === "system_info" && r.value && typeof r.value === "object") {
        const v = r.value;
        d.value = cut([v.os, v.machine, v.cpu ? `${v.cpu} CPU` : "", v.python ? `Python ${v.python}` : ""].filter(Boolean).join(" · "), 160);
      } else if (name === "disk_space" && r.value && typeof r.value === "object") {
        d.free = num(r.value.free); d.total = num(r.value.total);
        if (a.path) d.path = cut(a.path, 200);
      } else if ((name === "open_apps" || name === "process_list") && typeof r.value === "string") {
        d.count = r.value.split(/\r?\n/).filter((line) => line.trim()).length;
      } else if (name === "environment" && r.value && typeof r.value === "object") {
        d.count = Object.keys(r.value).length; d.hidden = num(r.hidden);
      } else if (name === "network_status" && r.value && typeof r.value === "object") {
        d.value = cut(r.value.hostname || "", 80);
      } else if (name === "search_files") {
        d.query = cut(r.query || a.query || "", 120);
        if (r.count !== undefined) d.count = num(r.count);
        if (Array.isArray(r.results)) d.files = r.results.slice(0, 12).map((f) => cut(f.path || "", 200));
      }
      if (name === "file_exists" && r.value && typeof r.value === "object") {
        d.exists = !!r.value.exists; d.kind = r.value.directory ? "dir" : r.value.file ? "file" : "";
      }
      if (name === "project_tree" && Array.isArray(r.value)) {
        d.count = r.value.length;
        d.files = r.value.slice(0, 12).map((f) => cut(f, 200));
      }

      const headline = r.title || r.name || r.subject || r.message || r.summary || r.panel?.title || r.issue?.title || r.file?.name || "";
      if (!d.title && headline && typeof headline === "string") d.title = cut(headline, 140);
      const link = safeUrl(r.html_url || r.web_url || r.webViewLink || r.url || r.link || "");
      if (link && !d.url && name !== "web_search") d.url = link;
    } catch (error) {  }
    return d;
  }

  const INTEGRATION = { gmail: "Gmail", github: "GitHub", gdrive: "Google Drive", drive: "Google Drive", gcal: "Google Calendar",
    calendar: "Google Calendar", notion: "Notion" };
  const VERB = {
    list: ["список", "list"], search: ["поиск", "search"], get: ["чтение", "read"], read: ["чтение", "read"], create: ["создание", "create"],
    update: ["изменение", "update"], delete: ["удаление", "delete"], send: ["отправка", "send"], draft: ["черновик", "draft"],
    reply: ["ответ", "reply"], comment: ["комментарий", "comment"], upload: ["загрузка", "upload"], download: ["скачивание", "download"],
  };
  function headline(row) {
    const tool = String(row.tool || ""), a = row.args || {}, d = row.detail || {};
    const q = (value) => (value ? `«${cut(value, 90)}»` : "");
    const qe = (value) => (value ? `“${cut(value, 90)}”` : "");
    const quote = (value) => (en() ? qe(value) : q(value));
    switch (tool) {
      case "web_search": return { glyph: "search", title: L("Поиск в интернете", "Web search"), subject: quote(d.query || a.query) };
      case "web_fetch": return { glyph: "globe", title: L("Открыта страница", "Opened page"), subject: d.title ? quote(d.title) : host(d.url || a.url) || cut(a.url, 80) };
      case "docs_search": return { glyph: "book", title: L("Поиск по документации", "Searched the documentation"), subject: quote(d.query || a.query) };
      case "docs_read": return { glyph: "book", title: L("Прочитана статья", "Read the article"), subject: quote(d.title || a.id) + (d.section || a.section ? ` · ${cut(d.section || a.section, 50)}` : "") };
      case "product_lookup": return { glyph: "search", title: L("Проверка товара", "Product check"), subject: quote(d.query || a.query) };
      case "current_time": return { glyph: "clock", title: L("Текущее время", "Current time"), subject: d.value || "" };
      case "system_info": return { glyph: "info", title: L("Сведения о системе", "System info"), subject: d.value || "" };
      case "working_directory": return { glyph: "folder", title: L("Рабочая папка", "Working folder"), subject: d.value ? `<code>${e(cut(d.value, 90))}</code>` : "", html: true };
      case "disk_space": return { glyph: "info", title: L("Место на диске", "Disk space"), subject: d.total ? L(`свободно ${size(d.free)} из ${size(d.total)}`, `${size(d.free)} free of ${size(d.total)}`) : "" };
      case "open_apps": return { glyph: "info", title: L("Открытые программы", "Open apps"), subject: "" };
      case "process_list": return { glyph: "info", title: L("Список процессов", "Process list"), subject: "" };
      case "environment": return { glyph: "info", title: L("Переменные окружения", "Environment variables"), subject: "" };
      case "network_status": return { glyph: "globe", title: L("Состояние сети", "Network status"), subject: d.value || "" };
      case "search_files": return { glyph: "search", title: L("Поиск файлов на ПК", "PC file search"), subject: quote(d.query || a.query) + (d.count !== undefined ? ` · ${d.count}` : "") };
      case "app_version": return { glyph: "info", title: L("Версия MemuAI", "MemuAI version"), subject: d.value || "" };
      case "file_exists": return { glyph: "text", title: d.exists === undefined ? L("Проверка файла", "File check")
        : d.exists ? (d.kind === "dir" ? L("Папка есть", "Folder exists") : L("Файл есть", "File exists")) : L("Файла нет", "No such file"),
        subject: d.path || a.path ? `<code>${e(cut(d.path || a.path, 90))}</code>` : "", html: true };
      case "memory_save": {
        const first = (d.items || [])[0];
        const updatedOnly = (d.items || []).length && d.items.every((item) => item.updated);
        return { glyph: "brain", title: updatedOnly ? L("Обновлено в памяти", "Updated in memory") : L("Сохранено в память", "Saved to memory"),
          subject: first ? quote(first.text) + ((d.items.length > 1) ? ` +${d.items.length - 1}` : "") : (d.pending ? L("ждёт подтверждения", "awaiting approval") : "") };
      }
      case "code_run": case "code_terminal": return { glyph: "terminal", title: L("Команда", "Command"), subject: d.command || a.command ? `<code>${e(cut(d.command || a.command, 90))}</code>` : "", html: true };
      case "project_tree": case "work_tree": return { glyph: "folder", title: L("Структура проекта", "Project structure"), subject: "" };
      case "agent_spawn": return { glyph: "users", title: L("Запущен агент", "Started an agent"), subject: quote(a.title || a.task || "") };
      case "agent_status": case "agent_result": return { glyph: "users", title: L("Агент", "Agent"), subject: quote(a.id || "") };
      default: break;
    }
    if (/^(?:work|code)_(?:read|read_cached|outline|outline_deep|symbols)$/.test(tool)) return { glyph: "text", title: L("Прочитан файл", "Read file"), subject: d.path || a.path ? `<code>${e(cut(d.path || a.path, 90))}</code>` : "", html: true };
    if (/^(?:work|code)_(?:search|grep|find)$/.test(tool)) return { glyph: "search", title: L("Поиск по файлам", "Searched files"), subject: quote(d.query || a.pattern || a.query) };
    if (/^(?:work|code)_(?:write|patch|insert|append|replace_lines|edit)$/.test(tool)) return { glyph: "edit", title: d.created ? L("Создан файл", "Created file") : L("Изменён файл", "Edited file"), subject: d.path || a.path ? `<code>${e(cut(d.path || a.path, 90))}</code>` : "", html: true };
    if (/^(?:work|code)_delete$/.test(tool)) return { glyph: "trash", title: L("Удалён файл", "Deleted file"), subject: d.path || a.path ? `<code>${e(cut(d.path || a.path, 90))}</code>` : "", html: true };
    if (/^(?:work|code)_move$/.test(tool)) return { glyph: "folder", title: L("Перемещён файл", "Moved file"), subject: `<code>${e(cut(d.from || a.from, 50))}</code> → <code>${e(cut(d.to || a.to, 50))}</code>`, html: true };
    if (tool.startsWith("panel_")) return { glyph: "chart", title: L("Панели", "Panels"), subject: quote(d.title || a.title || a.kind || tool.slice(6)) };
    if (tool.startsWith("mcp_")) return { glyph: "zap", title: `MCP${d.server ? " · " + e(d.server) : ""}`, subject: d.remote ? `<code>${e(d.remote)}</code>` : "", html: true };
    const prefix = tool.split("_")[0];
    if (INTEGRATION[prefix]) {
      const verb = Object.keys(VERB).find((key) => tool.includes("_" + key));
      return { glyph: "zap", title: INTEGRATION[prefix] + (verb ? ` · ${L(VERB[verb][0], VERB[verb][1])}` : ""), subject: quote(d.title || a.query || a.subject || a.title || a.name || a.path || "") };
    }

    const arg = typeof toolArgLabel === "function" ? toolArgLabel(a) : "";
    return { glyph: "terminal", title: `<code>${e(tool)}</code>`, subject: arg ? e(cut(arg, 90)) : "", html: true, rawTitle: true };
  }

  function metaText(row) {
    const d = row.detail || {};
    const bits = [];
    const tool = String(row.tool || "");
    if (!row.ok) bits.push(L("ошибка", "failed"));
    else if (tool === "web_search" && (d.count || d.links?.length)) bits.push(plural(d.count || d.links.length, ["результат", "result", "results"], "результата", "результатов"));
    else if (tool === "web_fetch" && d.chars) bits.push(plural(d.chars, ["символ", "character", "characters"], "символа", "символов") + (d.truncated ? "…" : ""));
    else if (tool === "docs_search" && d.docs) bits.push(plural(d.docs.length, ["статья", "article", "articles"], "статьи", "статей"));
    else if (tool === "product_lookup" && d.offers) bits.push(plural(d.offers.length, ["предложение", "offer", "offers"], "предложения", "предложений"));
    else if ((tool === "code_run" || tool === "code_terminal") && d.exit !== undefined) bits.push(`exit ${d.exit}`);
    else if (d.lines) bits.push(`${L("строки", "lines")} ${d.lines}`);
    else if ((tool === "project_tree" || tool === "open_apps" || tool === "process_list") && d.count) bits.push(plural(d.count, tool === "project_tree" ? ["файл", "file", "files"] : ["строка", "line", "lines"], tool === "project_tree" ? "файла" : "строки", tool === "project_tree" ? "файлов" : "строк"));
    else if (tool === "environment" && d.count) bits.push(plural(d.count, ["переменная", "variable", "variables"], "переменные", "переменных") + (d.hidden ? ` · ${L("скрыто", "hidden")} ${d.hidden}` : ""));
    else if (row.summary) bits.push(cut(row.summary, 60));
    if (row.ms) bits.push(dur(row.ms));
    return bits.join(" · ");
  }

  function argsTable(args) {
    const rows = Object.entries(args || {}).filter(([key]) => key !== "project_id").slice(0, 12);
    if (!rows.length) return "";
    return `<div class="tool-x-section"><div class="tool-x-caption">${L("Параметры", "Parameters")}</div><dl class="tool-x-args">${rows.map(([key, value]) =>
      `<dt>${e(key)}</dt><dd>${e(cut(typeof value === "string" ? value : JSON.stringify(value), 400))}</dd>`).join("")}</dl></div>`;
  }
  const linkList = (items) => `<div class="tool-x-links">${items.map((item, n) => item.url
    ? `<a href="${e(item.url)}" target="_blank" rel="noopener noreferrer" title="${e(item.url)}"><span class="tool-x-n">${n + 1}</span><span class="tool-x-lt"><b>${e(item.title || host(item.url))}</b><small>${e(host(item.url))}${item.snippet ? " — " + e(item.snippet) : ""}</small></span></a>`
    : `<div class="tool-x-link-plain"><span class="tool-x-n">${n + 1}</span><span class="tool-x-lt"><b>${e(item.title)}</b></span></div>`).join("")}</div>`;
  const docChip = (id, title, anchor = "") => id
    ? `<button type="button" class="doc-chip tool-x-doc" data-doc="${e(id)}" data-anchor="${e(anchor)}" title="${L("Открыть в документации", "Open in the documentation")}">${e(title || id)}</button>`
    : e(title);
  const section = (caption, html) => (html ? `<div class="tool-x-section">${caption ? `<div class="tool-x-caption">${caption}</div>` : ""}${html}</div>` : "");

  function bodyHtml(row) {
    const tool = String(row.tool || ""), d = row.detail || {};
    const parts = [];
    if (row.error) parts.push(`<div class="tool-x-error">${ic("info")}<span>${e(cut(row.error, 600))}</span></div>`);
    if (tool === "web_search") {
      if (d.queries?.length) parts.push(section(L("Запросы", "Queries"), `<div class="tool-x-chips">${d.queries.map((q) => `<span>${e(q)}</span>`).join("")}</div>`));
      if (d.answer?.text) parts.push(section(L("Готовый ответ", "Instant answer"), `<p class="tool-x-quote">${e(d.answer.text)}${d.answer.source ? ` <small>— ${d.answer.url ? `<a href="${e(d.answer.url)}" target="_blank" rel="noopener noreferrer">${e(d.answer.source)}</a>` : e(d.answer.source)}</small>` : ""}</p>`));
      if (d.links?.length) parts.push(section(L("Найдено", "Found"), linkList(d.links)));
      if (d.pages?.length) parts.push(section(L("Прочитаны страницы", "Pages read"), linkList(d.pages)));
    } else if (tool === "web_fetch") {
      if (d.url) parts.push(section("", linkList([{ title: d.title || host(d.url), url: safeUrl(d.url) || "", snippet: "" }])));
    } else if (tool === "docs_search") {
      if (d.docs?.length) parts.push(section(L("Статьи", "Articles"), `<div class="tool-x-docs">${d.docs.map((doc) => `<div>${docChip(doc.id, doc.title, doc.anchor)}${doc.section ? `<small>${e(doc.section)}</small>` : ""}</div>`).join("")}</div>`));
    } else if (tool === "docs_read") {
      if (d.id) parts.push(section("", `<div class="tool-x-docs"><div>${docChip(d.id, d.title)}${d.section ? `<small>${L("раздел", "section")}: ${e(d.section)}</small>` : ""}</div></div>`));
      if (d.summary) parts.push(section("", `<p class="tool-x-quote">${e(d.summary)}</p>`));
      if (d.sections?.length && !d.section) parts.push(section(L("Разделы статьи", "Sections"), `<div class="tool-x-chips">${d.sections.map((s) => `<span>${e(s)}</span>`).join("")}</div>`));
    } else if (tool === "product_lookup") {
      if (d.offers?.length) parts.push(section(L("Предложения", "Offers"), linkList(d.offers.map((o) => ({ title: [o.title, o.price].filter(Boolean).join(" — "), url: o.url, snippet: o.shop })))));
    } else if (tool === "code_run" || tool === "code_terminal") {
      if (d.command) parts.push(section(L("Команда", "Command"), `<pre class="step-code">${e(d.command)}</pre>`));
      if (d.output) parts.push(section(L("Вывод", "Output"), `<pre class="step-code out">${e(d.output)}</pre>`));
      if (d.stderr) parts.push(section("stderr", `<pre class="step-code err">${e(d.stderr)}</pre>`));
    } else if (tool === "memory_save") {
      if (d.items?.length) parts.push(section("", `<ul class="tool-x-list">${d.items.map((item) => `<li>${e(item.text)}${item.updated ? ` <small>(${L("обновлено", "updated")})</small>` : ""}</li>`).join("")}</ul>`));
      if (d.pending) parts.push(`<div class="step-note">${L("Ещё фактов ждут подтверждения в «Памяти»", "More facts await your approval in Memory")}: ${d.pending}</div>`);
    } else {
      if (d.path) parts.push(section(L("Файл", "File"), `<div class="step-files"><span>${e(d.path)}</span></div>`));
      if (d.matches?.length) parts.push(section(L("Совпадения", "Matches"), `<pre class="step-code out">${e(d.matches.join("\n"))}</pre>`));
      if (d.files?.length) parts.push(section(L("Файлы", "Files"), `<div class="step-files">${d.files.map((f) => `<span>${e(f)}</span>`).join("")}</div>`));
      if (d.value) parts.push(section(L("Значение", "Value"), `<p class="tool-x-quote">${e(d.value)}</p>`));
      if (d.url && safeUrl(d.url)) parts.push(section("", linkList([{ title: d.title || host(d.url), url: d.url, snippet: "" }])));
      else if (d.title && !/^panel_/.test(tool)) parts.push(section("", `<p class="tool-x-quote">${e(d.title)}</p>`));
    }
    if (tool !== "memory_save") parts.push(argsTable(row.args));
    parts.push(`<div class="tool-x-tech"><span>${L("Инструмент", "Tool")}</span><code>${e(tool)}</code>${row.ms ? `<span>${e(dur(row.ms))}</span>` : ""}</div>`);
    return parts.filter(Boolean).join("");
  }

  const userOpen = new Map();
  document.addEventListener("click", (event) => {
    const summary = event.target.closest?.("details.tool-x[data-tool-key] > summary");
    if (!summary) return;
    const details = summary.parentElement;
    setTimeout(() => { if (details.isConnected) userOpen.set(details.dataset.toolKey, details.open); }, 0);
  }, true);

  function rowHtml(row, key = "") {
    const h = headline(row);
    const meta = metaText(row);
    const state = row.ok ? "ok" : "failed";
    const open = key && userOpen.get(key) ? " open" : "";
    const label = `${h.title}${h.subject ? " " + (h.html ? h.subject : e(h.subject)) : ""}`.replace(/<[^>]+>/g, "");
    return `<details class="tool-x is-${state}"${open}${key ? ` data-tool-key="${e(key)}"` : ""}>
      <summary title="${e(label)}">
        <span class="tool-x-icon">${ic(h.glyph)}</span>
        <span class="tool-x-head"><span class="tool-x-title">${h.title}</span>${h.subject ? `<span class="tool-x-subject">${h.html ? h.subject : e(h.subject)}</span>` : ""}</span>
        ${meta ? `<span class="tool-x-meta">${e(meta)}</span>` : ""}
        <span class="tool-x-state" aria-label="${row.ok ? L("готово", "done") : L("ошибка", "failed")}">${ic(row.ok ? "check" : "close")}</span>
        <span class="tool-x-chevron">›</span>
      </summary>
      <div class="tool-x-body">${bodyHtml(row)}</div>
    </details>`;
  }

  function memoryRow(res) {
    const added = (res?.added || []).map((item) => ({ text: item.text, updated: false }));
    const updated = (res?.updated || []).map((item) => ({ text: item.text, updated: true }));
    const items = [...added, ...updated];
    const pending = (res?.pending || []).length;
    if (!items.length && !pending) return null;
    const args = { items, pending };
    return { tool: "memory_save", args, ok: true, error: "", summary: "", detail: capture("memory_save", args, {}) };
  }

  return { capture, rowHtml, memoryRow, headline, _dur: dur };
})();
if (typeof module !== "undefined" && module.exports) module.exports = MemuToolDetails;
