

(function () {
  "use strict";

  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) {  }
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVG = {
    folder: '<svg viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.6l1.9 2h8.5A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19h14"/></svg>',
    open: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 6v5h-5"/><path d="M19 11a8 8 0 1 0 1 5"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4.5 7h15M10 11v6M14 11v6M6 7l1 12.5h10L18 7M9 7V4.5h6V7"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    sparkle: '<svg viewBox="0 0 24 24"><path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/></svg>',
    bolt: '<svg viewBox="0 0 24 24"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/></svg>',
    tabs: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M8 6V4h6v2"/></svg>',
    split: '<svg viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M12 4.5v15"/></svg>',
    shield: '<svg viewBox="0 0 24 24"><path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/></svg>',
  };
  const ico = (name) => `<span class="ui-icon" aria-hidden="true">${SVG[name] || ""}</span>`;

  let ctx = null;
  let st = null;
  let busy = "";
  const kit = () => (ctx && ctx.kit) || window.MemuSettingsKit;
  const root = () => document.getElementById("mf-settings-root");
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
      no_package: L("Пакет MemuFiles не найден. Запустите установщик MemuAI и включите MemuFiles.", "The MemuFiles package was not found. Run the MemuAI Setup and turn MemuFiles on."),
      not_installed: L("MemuFiles не установлен — его можно установить в Настройках → MemuFiles.", "MemuFiles is not installed — install it in Settings → MemuFiles."),
      busy: L("MemuFiles ещё работает — закройте его окно и повторите.", "MemuFiles is still running — close its window and try again."),
      package_invalid: L("Пакет MemuFiles повреждён. Переустановите MemuAI.", "The MemuFiles package is damaged. Reinstall MemuAI."),
      dev: L("Режим исходников: запустите «python memufiles.py».", "Source checkout: run \"python memufiles.py\"."),
    };
    return map[data && data.error] || (data && (data.message || data.error)) || "";
  }
  function toast(text, kind) {
    if (ctx && ctx.toast) ctx.toast(text, kind || "ok");
    else if (typeof window.toast === "function") window.toast(text, kind || "ok");
  }

  async function load() {
    try { st = await call("/api/pg/memufiles"); }
    catch (e) { st = { error: e.message }; }
    paint();
  }

  function stateChip() {
    if (!st) return `<span class="mf-chip">${esc(L("Проверяю…", "Checking…"))}</span>`;
    if (st.error) return `<span class="mf-chip warn">${esc(L("Нет связи", "Unavailable"))}</span>`;
    if (st.installed) return `<span class="mf-chip ok">${ico("check")}${esc(st.running ? L("Запущен", "Running") : L("Установлен", "Installed"))}${st.version ? " · " + esc(st.version) : ""}</span>`;
    if (st.package && st.package.present) return `<span class="mf-chip">${esc(L("Готов к установке", "Ready to install"))}</span>`;
    return `<span class="mf-chip muted">${esc(L("Не установлен", "Not installed"))}</span>`;
  }
  function actionsHtml() {
    if (!st || st.error) return `<button class="pbtn ghost" data-mf="refresh">${ico("refresh")}${esc(L("Проверить снова", "Check again"))}</button>`;
    const b = (id, label, icon, cls = "") => `<button class="pbtn ${cls}" data-mf="${id}" ${busy ? "disabled" : ""}>${ico(icon)}<span>${esc(busy === id ? L("Подождите…", "Please wait…") : label)}</span></button>`;
    if (st.dev) return st.installed ? b("open", L("Открыть MemuFiles", "Open MemuFiles"), "open") : "";
    if (st.installed) {
      return b("open", L("Открыть MemuFiles", "Open MemuFiles"), "open")
        + (st.update_available ? b("install", L("Обновить", "Update") + (st.package.version ? " → " + st.package.version : ""), "download", "ghost") : "")
        + b("uninstall", L("Удалить", "Remove"), "trash", "ghost mf-danger");
    }
    if (st.package && st.package.present) return b("install", L("Установить MemuFiles", "Install MemuFiles"), "download");
    return "";
  }
  function heroHtml() {
    const pkg = st && st.package && st.package.present ? st.package : null;
    const sub = L("Быстрый проводник MemuAI: вкладки и две панели, мгновенный поиск по всему компьютеру, предпросмотр, массовое переименование, корзина с отменой и ИИ-ассистент, который понимает просьбы вроде «удали всё меньше 5 КБ».",
      "The fast file manager of MemuAI: tabs and two panes, instant search across the whole PC, previews, batch rename, a Recycle Bin with undo and an AI assistant that understands requests like \"delete everything under 5 KB\".");
    let foot = "";
    if (st && st.dev) foot = L("Режим исходников: MemuFiles запускается командой «python memufiles.py».", "Source checkout: MemuFiles starts with \"python memufiles.py\".");
    else if (st && st.installed && st.path) foot = st.path;
    else if (pkg) foot = L("Пакет в папке программы", "Package in the program folder") + (pkg.unpacked ? " · " + fmtMB(pkg.unpacked) + L(" после установки", " installed") : "");
    else if (st && !st.error) foot = L("Пакет не найден — запустите установщик MemuAI и включите переключатель «MemuFiles».", "No package found — run the MemuAI Setup and turn on the «MemuFiles» switch.");
    return `<div class="mf-hero">
      <div class="mf-hero-art" aria-hidden="true"><img src="/memufiles-icon.svg" alt=""></div>
      <div class="mf-hero-main">
        <div class="mf-hero-title"><b>MemuFiles</b>${stateChip()}</div>
        <p class="mf-hero-sub">${esc(sub)}</p>
        <div class="mf-hero-actions">${actionsHtml()}</div>
        ${foot ? `<div class="mf-hero-foot">${esc(foot)}</div>` : ""}
      </div></div>`;
  }
  function featuresHtml() {
    const items = [
      ["bolt", L("Скорость", "Speed"), L("Папки на 100 000 файлов открываются мгновенно и прокручиваются плавно.", "Folders with 100,000 files open instantly and scroll smoothly.")],
      ["search", L("Мгновенный поиск", "Instant search"), L("По имени, маске, размеру, дате и содержимому — в папке или на всём ПК.", "By name, mask, size, date and content — in a folder or across the PC.")],
      ["tabs", L("Вкладки", "Tabs"), L("Вкладки, закреплённые папки, восстановление сеанса, цветные метки.", "Tabs, pinned folders, session restore, colour tags.")],
      ["split", L("Две панели", "Two panes"), L("Копирование и перенос перетаскиванием, очередь операций с паузой и отменой.", "Drag-and-drop copy and move, an operations queue with pause and undo.")],
      ["sparkle", L("ИИ-ассистент", "AI assistant"), L("Спросите о файлах и содержимом или попросите навести порядок — сначала план, потом подтверждение.", "Ask about files and their contents or ask to tidy up — a plan first, then your confirmation.")],
      ["shield", L("Безопасно", "Safe"), L("Удаление в корзину с Ctrl+Z, системные папки защищены, без Proxy всё кроме ИИ работает.", "Deletes go to the Recycle Bin with Ctrl+Z, system folders are protected, everything but AI works without the Proxy.")],
    ];
    return `<div class="mf-features">${items.map(([icon, title, text]) => `<div class="mf-feature">${ico(icon)}<div><b>${esc(title)}</b><small>${esc(text)}</small></div></div>`).join("")}</div>`;
  }

  const form = window.MemuAppSettings ? MemuAppSettings.create({ app: "memufiles", idp: "mfset-", name: "MemuFiles", toast: (text, kind) => toast(text, kind),
    extra: { windows: {
      html: () => (window.MemuFilesDefaultShell ? MemuFilesDefaultShell.html() : ""),
      mount: () => window.MemuFilesDefaultShell?.mount(),
      desc: () => L("MemuFiles вместо Проводника — только по вашему желанию и с возвратом в один клик.", "MemuFiles instead of Explorer — only if you want it, undone in one click."),
    } } }) : null;
  function html(c) {
    ctx = c || ctx;
    return `<div id="mf-settings-root" class="mf-settings"><div id="mf-settings-top">${top()}</div>${form ? form.html() : ""}<div id="mf-settings-tail">${tail()}</div></div>`;
  }
  function top() {
    const K = kit();
    const hero = K ? K.section({ id: "settings-memufiles", card: false, body: heroHtml() }) : heroHtml();
    const pre = K && st && !st.error && !st.installed && !st.dev
      ? `<div class="appset-pre">${K.note(esc(L("MemuFiles ещё не установлен — настройки ниже можно подготовить заранее: он возьмёт их при первом запуске.", "MemuFiles isn't installed yet — you can prepare the settings below now: it picks them up on first start.")))}</div>` : "";
    return hero + pre;
  }
  function tail() {
    const K = kit();
    if (st && st.installed) return "";
    return K ? K.section({ id: "settings-memufiles-features", title: L("Что умеет", "What it does"), card: false, body: featuresHtml() }) : featuresHtml();
  }
  function paint() {
    const a = document.getElementById("mf-settings-top"), b = document.getElementById("mf-settings-tail");
    if (a) a.innerHTML = top();
    if (b) b.innerHTML = tail();
    kit()?.invalidate();
  }

  function confirmBox(text, okText) {
    return new Promise((resolve) => {
      const opener = document.activeElement;
      const node = document.createElement("div");
      node.className = "sync-confirm";
      node.innerHTML = `<div class="sync-confirm-card" role="alertdialog" aria-modal="true" aria-describedby="mf-confirm-text">
        <p id="mf-confirm-text">${esc(text)}</p>
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
      const ok = await confirmBox(L("Удалить MemuFiles? Ваши файлы не затрагиваются; программа вернётся в пакет, и её можно будет установить снова.",
        "Remove MemuFiles? Your files are not touched; the program goes back into its package and can be installed again."), L("Удалить", "Remove"));
      if (!ok) return;
    }
    busy = action; paint();
    try {
      if (action === "open") { await call("/api/pg/memufiles/open", {}); toast(L("MemuFiles открывается…", "Opening MemuFiles…")); }
      else if (action === "install") {
        const r = await call("/api/pg/memufiles/install", {});
        st = r; toast(r.updated ? L("MemuFiles обновлён", "MemuFiles updated") : L("MemuFiles установлен — он есть в меню «Пуск»", "MemuFiles installed — it is in the Start menu"));
      } else if (action === "uninstall") {
        st = await call("/api/pg/memufiles/uninstall", { keep_package: true }); toast(L("MemuFiles удалён", "MemuFiles removed"));
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
      el.addEventListener("click", (e) => { const b = e.target.closest("[data-mf]"); if (b && !b.disabled) act(b.dataset.mf); });
    }
    load();
    if (form) form.mount();
  }

  async function open(paths) {
    try {
      const r = await call("/api/pg/memufiles/open", { paths: (paths || []).filter(Boolean) });
      toast(L("Открываю в MemuFiles…", "Opening in MemuFiles…"));
      return r;
    } catch (e) {
      toast(e.message || String(e), "error");
      throw e;
    }
  }

  window.MemuFilesSettings = {
    icon: `<span class="ui-icon">${SVG.folder}</span>`,
    html, mount, open,
    status: () => st,
  };
})();
