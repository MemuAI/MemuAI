

(function () {
  "use strict";

  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) {  }
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVG = {
    note: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19h14"/></svg>',
    open: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 6v5h-5"/><path d="M19 11a8 8 0 1 0 1 5"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4.5 7h15M10 11v6M14 11v6M6 7l1 12.5h10L18 7M9 7V4.5h6V7"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    sparkle: '<svg viewBox="0 0 24 24"><path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/></svg>',
    bolt: '<svg viewBox="0 0 24 24"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg>',
    md: '<svg viewBox="0 0 24 24"><rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M6 15V9l2.5 3L11 9v6M15.5 9v6m0 0-2-2m2 2 2-2"/></svg>',
    doc: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="m8.5 12 1.2 5 1.3-4 1.3 4 1.2-5"/></svg>',
    tabs: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M8 6V4h6v2"/></svg>',
    shield: '<svg viewBox="0 0 24 24"><path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/></svg>',
  };
  const ico = (name) => `<span class="ui-icon" aria-hidden="true">${SVG[name] || ""}</span>`;

  let ctx = null;
  let st = null;
  let busy = "";
  const kit = () => (ctx && ctx.kit) || window.MemuSettingsKit;
  const root = () => document.getElementById("mn-settings-root");
  const fmtMB = (bytes) => (bytes ? (bytes / 1048576).toFixed(bytes < 10 * 1048576 ? 1 : 0) + L(" МБ", " MB") : "");

  async function call(path, body) {
    const response = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(errorText(data) || ("HTTP " + response.status));
      error.code = data.error; throw error;
    }
    return data;
  }
  function errorText(data) {
    const map = {
      no_package: L("Пакет MemuNote не найден. Запустите установщик MemuAI и включите MemuNote.", "The MemuNote package was not found. Run the MemuAI Setup and turn MemuNote on."),
      not_installed: L("MemuNote не установлен.", "MemuNote is not installed."),
      busy: L("MemuNote ещё работает — закройте его окно и повторите.", "MemuNote is still running — close its window and try again."),
      package_invalid: L("Пакет MemuNote повреждён. Переустановите MemuAI.", "The MemuNote package is damaged. Reinstall MemuAI."),
      dev: L("Режим исходников: запустите «python memunote.py».", "Source checkout: run \"python memunote.py\"."),
    };
    return map[data && data.error] || (data && (data.message || data.error)) || "";
  }
  function toast(text, kind) { if (ctx && ctx.toast) ctx.toast(text, kind || "ok"); }

  async function load() {
    try { st = await call("/api/pg/memunote"); }
    catch (e) { st = { error: e.message }; }
    paint();
  }

  function stateChip() {
    if (!st) return `<span class="mn-chip">${esc(L("Проверяю…", "Checking…"))}</span>`;
    if (st.error) return `<span class="mn-chip warn">${esc(L("Нет связи", "Unavailable"))}</span>`;
    if (st.installed) return `<span class="mn-chip ok">${ico("check")}${esc(L("Установлен", "Installed"))}${st.version ? " · " + esc(st.version) : ""}</span>`;
    if (st.package && st.package.present) return `<span class="mn-chip">${esc(L("Готов к установке", "Ready to install"))}</span>`;
    return `<span class="mn-chip muted">${esc(L("Не установлен", "Not installed"))}</span>`;
  }
  function actionsHtml() {
    if (!st || st.error) return `<button class="pbtn ghost" data-mn="refresh">${ico("refresh")}${esc(L("Проверить снова", "Check again"))}</button>`;
    const b = (id, label, icon, cls = "") => `<button class="pbtn ${cls}" data-mn="${id}" ${busy ? "disabled" : ""}>${ico(icon)}<span>${esc(busy === id ? L("Подождите…", "Please wait…") : label)}</span></button>`;
    if (st.dev) return st.installed ? b("open", L("Открыть MemuNote", "Open MemuNote"), "open") : "";
    if (st.installed) {
      return b("open", L("Открыть MemuNote", "Open MemuNote"), "open")
        + (st.update_available ? b("install", L("Обновить", "Update") + (st.package.version ? " → " + st.package.version : ""), "download", "ghost") : "")
        + b("uninstall", L("Удалить", "Remove"), "trash", "ghost mn-danger");
    }
    if (st.package && st.package.present) return b("install", L("Установить MemuNote", "Install MemuNote"), "download");
    return "";
  }
  function heroHtml() {
    const pkg = st && st.package && st.package.present ? st.package : null;
    let sub = L("Лёгкий ИИ-блокнот MemuAI: вкладки, огромные файлы, Markdown с таблицами, документы Word, PDF и CSV. ИИ работает через MemuAI Proxy, а без него MemuNote остаётся быстрым редактором.",
      "The light AI notepad of MemuAI: tabs, huge files, Markdown with tables, Word documents, PDF and CSV. AI works through MemuAI Proxy; without it MemuNote is a fast editor.");
    let foot = "";
    if (st && st.dev) foot = L("Режим исходников: MemuNote запускается командой «python memunote.py».", "Source checkout: MemuNote starts with \"python memunote.py\".");
    else if (st && st.installed && st.path) foot = esc(st.path);
    else if (pkg) foot = L("Пакет в папке программы", "Package in the program folder") + (pkg.unpacked ? " · " + fmtMB(pkg.unpacked) + L(" после установки", " installed") : "");
    else if (st && !st.error) foot = L("Пакет не найден — запустите установщик MemuAI и включите переключатель «MemuNote».", "No package found — run the MemuAI Setup and turn on the «MemuNote» switch.");
    return `<div class="mn-hero">
      <div class="mn-hero-art" aria-hidden="true"><img src="/memunote-icon.svg" alt=""></div>
      <div class="mn-hero-main">
        <div class="mn-hero-title"><b>MemuNote</b>${stateChip()}</div>
        <p class="mn-hero-sub">${esc(sub)}</p>
        <div class="mn-hero-actions">${actionsHtml()}</div>
        ${foot ? `<div class="mn-hero-foot">${esc(foot)}</div>` : ""}
      </div></div>`;
  }
  function featuresHtml() {
    const items = [
      ["bolt", L("Огромные файлы", "Huge files"), L("Логи и дампы на сотни мегабайт открываются за секунды.", "Logs and dumps of hundreds of megabytes open in seconds.")],
      ["tabs", L("Вкладки и сеансы", "Tabs and sessions"), L("Разделение окна, восстановление после сбоя, кодировки, поиск с регулярками.", "Split view, crash recovery, encodings, regex search.")],
      ["md", "Markdown", L("Таблицы, стили, просмотр рядом; .txt превращается в .md по вашему согласию.", "Tables, styles, side-by-side preview; a .txt becomes .md when you agree.")],
      ["doc", L("Документы", "Documents"), L("DOCX, ODT и RTF как в Word; PDF, CSV-таблицы, JSON и код.", "DOCX, ODT and RTF like in Word; PDF, CSV tables, JSON and code.")],
      ["sparkle", L("ИИ рядом с текстом", "AI next to the text"), L("Улучшить, перевести, сократить, вопросы по файлу, веб-поиск и память MemuAI.", "Improve, translate, shorten, questions about the file, web search and MemuAI memory.")],
      ["shield", L("Без Proxy тоже", "Without the Proxy too"), L("Все функции редактора работают офлайн; ИИ включится, когда Proxy запущен.", "Every editor feature works offline; AI turns on when the Proxy runs.")],
    ];
    return `<div class="mn-features">${items.map(([icon, title, text]) => `<div class="mn-feature">${ico(icon)}<div><b>${esc(title)}</b><small>${esc(text)}</small></div></div>`).join("")}</div>`;
  }
  function optionsHtml() {
    const K = kit();
    if (!st || !st.installed || st.dev || !K) return "";
    const ow = st.open_with || {};
    const control = ow.supported ? K.toggle("mn-open-with", !!ow.enabled, 'data-mn-toggle="open-with"') : `<span class="sx-desc">${esc(L("Только в Windows", "Windows only"))}</span>`;
    return K.section({ id: "settings-memunote-options", title: L("Интеграция с Windows", "Windows integration"), body:
      K.row({ label: L("В меню «Открыть с помощью»", "In the \"Open with\" menu"), desc: L("Необязательно. MemuNote появится в «Открыть с помощью» только для текста, Markdown, данных и документов (.txt, .md, .log, .csv, .json, .docx, .pdf…). Программой по умолчанию он не становится, ваши программы не меняются; скрипты и программы (.bat, .cmd, .ps1, .exe…) не затрагиваются.", "Optional. MemuNote appears in \"Open with\" for text, Markdown, data and document files only (.txt, .md, .log, .csv, .json, .docx, .pdf…). It never becomes the default program and your default apps stay as they are; scripts and programs (.bat, .cmd, .ps1, .exe…) are never touched."), control }) });
  }

  const form = window.MemuAppSettings ? MemuAppSettings.create({ app: "memunote", idp: "mnset-", name: "MemuNote", toast: (text, kind) => toast(text, kind) }) : null;
  function html(c) {
    ctx = c || ctx;
    return `<div id="mn-settings-root" class="mn-settings"><div id="mn-settings-top">${top()}</div>${form ? form.html() : ""}<div id="mn-settings-tail">${tail()}</div></div>`;
  }
  function top() {
    const K = kit();
    const hero = K ? K.section({ id: "settings-memunote", card: false, body: heroHtml() }) : heroHtml();
    const pre = K && st && !st.error && !st.installed && !st.dev
      ? `<div class="appset-pre">${K.note(esc(L("MemuNote ещё не установлен — настройки ниже можно подготовить заранее: он возьмёт их при первом запуске.", "MemuNote isn't installed yet — you can prepare the settings below now: it picks them up on first start.")))}</div>` : "";
    return hero + pre + optionsHtml();
  }
  function tail() {
    const K = kit();
    if (st && st.installed) return "";
    return K ? K.section({ id: "settings-memunote-features", title: L("Что умеет", "What it does"), card: false, body: featuresHtml() }) : featuresHtml();
  }
  function paint() {
    const a = document.getElementById("mn-settings-top"), b = document.getElementById("mn-settings-tail");
    if (a) a.innerHTML = top();
    if (b) b.innerHTML = tail();
    kit()?.invalidate();
  }

  function confirmBox(text, okText) {
    return new Promise((resolve) => {
      const opener = document.activeElement;
      const node = document.createElement("div");
      node.className = "sync-confirm";
      node.innerHTML = `<div class="sync-confirm-card" role="alertdialog" aria-modal="true" aria-describedby="mn-confirm-text">
        <p id="mn-confirm-text">${esc(text)}</p>
        <div class="sync-confirm-actions"><button type="button" class="pbtn ghost" data-no>${esc(L("Отмена", "Cancel"))}</button>
          <button type="button" class="pbtn danger" data-yes>${esc(okText)}</button></div></div>`;
      const finish = (value) => {
        node.remove(); document.removeEventListener("keydown", onKey, true);
        try { opener && opener.focus && opener.focus({ preventScroll: true }); } catch (e) {  }
        resolve(value);
      };
      const onKey = (event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(false); }
        if (event.key === "Tab") {
          const items = [...node.querySelectorAll("button")]; const i = items.indexOf(document.activeElement);
          event.preventDefault(); items[(i + (event.shiftKey ? items.length - 1 : 1)) % items.length].focus();
        }
      };
      node.addEventListener("click", (event) => {
        if (event.target.closest("[data-yes]")) finish(true);
        else if (event.target.closest("[data-no]") || event.target === node) finish(false);
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(node);
      requestAnimationFrame(() => node.querySelector("[data-no]").focus());
    });
  }

  async function act(action) {
    if (busy) return;
    if (action === "refresh") { st = null; paint(); await load(); return; }
    if (action === "uninstall") {
      const ok = await confirmBox(L("Удалить MemuNote? Ваши файлы и настройки MemuNote останутся; программа вернётся в пакет, и её можно будет установить снова.",
        "Remove MemuNote? Your files and MemuNote settings stay; the program goes back into its package and can be installed again."), L("Удалить", "Remove"));
      if (!ok) return;
    }
    busy = action; paint();
    try {
      if (action === "open") { await call("/api/pg/memunote/open", {}); toast(L("MemuNote открывается…", "Opening MemuNote…")); }
      else if (action === "install") {
        const r = await call("/api/pg/memunote/install", {});
        st = r; toast(r.updated ? L("MemuNote обновлён", "MemuNote updated") : L("MemuNote установлен — он есть в меню «Пуск»", "MemuNote installed — it is in the Start menu"));
      } else if (action === "uninstall") {
        st = await call("/api/pg/memunote/uninstall", { keep_package: true }); toast(L("MemuNote удалён", "MemuNote removed"));
      }
    } catch (e) { toast(e.message || String(e), "error"); }
    busy = "";
    if (action !== "open") await load(); else paint();
  }

  function mount(c) {
    ctx = c || ctx;
    const el = root();
    if (!el) return;
    if (!el.dataset.bound) {
      el.dataset.bound = "1";
      el.addEventListener("click", (e) => { const b = e.target.closest("[data-mn]"); if (b && !b.disabled) act(b.dataset.mn); });
      el.addEventListener("change", async (e) => {
        const t = e.target.closest("[data-mn-toggle=open-with]"); if (!t) return;
        try { await call("/api/pg/memunote/open-with", { enable: t.checked }); toast(t.checked ? L("MemuNote добавлен в «Открыть с помощью»", "MemuNote added to \"Open with\"") : L("MemuNote убран из «Открыть с помощью»", "MemuNote removed from \"Open with\"")); }
        catch (err) { t.checked = !t.checked; toast(err.message, "error"); }
      });
    }
    load();
    if (form) form.mount();
  }

  window.MemuNoteSettings = {
    icon: `<span class="ui-icon">${SVG.note}</span>`,
    html, mount,
    status: () => st,

    open: (files) => call("/api/pg/memunote/open", { files: files || [] }),
  };
})();
