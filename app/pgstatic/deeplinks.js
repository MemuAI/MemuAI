/* MemuAI · Round 35 (protocol): memuai:// links in the window.

   The server (app/deeplinks*.py) validates every link and queues it; this file shows the queue:
     safe     → runs at once (open a chat / section / settings page, prefill a text — nothing is sent or saved)
     confirm  → a dialog INSIDE MemuAI: what will happen, the link, where it came from, optional preview
                (skill text, composite JSON), fields the user types (an API key) — «Выполнить» unlocks after 1.2 s.
   window.MemuLinks = { poll, registerAction(name, fn), copyLink(route, params), chatMenuItems(chat), settings }
   Other zones: a server route (deeplinks.register_route(..., ui="zone.action")) + MemuLinks.registerAction("zone.action", fn). */
(function () {
  "use strict";
  if (window.MemuLinks) return;

  const isEn = () => { try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) { /* ignore */ } return (document.documentElement.lang || "ru").startsWith("en"); };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (text, kind) => { try { if (typeof toast === "function") toast(text, kind || "ok"); } catch (e) { /* ignore */ } };
  const lang = () => (isEn() ? "en" : "ru");
  const SVG = {
    link: '<svg viewBox="0 0 24 24"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/></svg>',
    shield: '<svg viewBox="0 0 24 24"><path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M12 8v5M12 16.2v.3"/></svg>',
    term: '<svg viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M12.5 15H17"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/></svg>',
  };
  const ico = (name) => `<span class="ui-icon" aria-hidden="true">${SVG[name] || ""}</span>`;

  async function call(path, body) {
    const res = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok && data.ok === undefined) data.ok = false;
    return data;
  }

  /* ------------------------------------------------------------ UI actions */
  const actions = new Map();
  function registerAction(name, fn) { if (name && typeof fn === "function") actions.set(String(name), fn); }
  const g = (name) => (typeof window[name] === "function" ? window[name] : null);
  const clickId = (id) => { const el = document.getElementById(id); if (el) { el.click(); return true; } return false; };
  function closePanels() { try { if (typeof closePanel === "function") closePanel(); } catch (e) { /* ignore */ } }
  function prefill(text) {
    const box = document.getElementById("input");
    if (!box || !text) return box;
    box.value = String(text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.focus();
    try { box.setSelectionRange(box.value.length, box.value.length); } catch (e) { /* ignore */ }
    return box;
  }

  registerAction("chat.new", async (a) => {
    closePanels();
    const nc = g("newChat");
    if (nc) nc(!!a.temp); else clickId("btn-new");
    await new Promise((r) => setTimeout(r, 60));
    if (a.model) {
      const vis = g("visibleModels"), set = g("setCurrentModel");
      if (vis && set && vis().some((m) => m.id === a.model)) set(a.model);
      else say(L("Модели «" + a.model + "» нет в списке — выбрана обычная.", "Model \"" + a.model + "\" is not in the list — the usual one is used."), "warn");
    }
    prefill(a.q);
    // `send` reaches here only after the dialog (the server marks it confirm).
    if (a.send && a.q) { const btn = document.getElementById("btn-send"); if (btn) setTimeout(() => btn.click(), 80); }
  });
  registerAction("chat.open", async (a) => {
    closePanels();
    const has = (typeof S !== "undefined" && S.chats) ? S.chats.some((c) => c.id === a.id) : true;
    if (!has) { say(L("Чат не найден: ", "Chat not found: ") + a.id, "warn"); return; }
    const oc = g("openChat"); if (oc) await oc(a.id);
  });
  registerAction("settings.open", (a) => { const f = g("openSettingsPage"); if (f) f(a.section || "general"); });
  registerAction("tab.open", (a) => {
    const t = a.tab;
    const settingsPage = { modules: "modules", panels: "panels", memory: "profile", settings: "general", docs: "docs" }[t];
    if (settingsPage) { const f = g("openSettingsPage"); if (f) f(settingsPage); return; }
    closePanels();
    if (t === "chat" || t === "home") { if (t === "home" && g("newChat")) newChat(false); else clickId("nav-chat"); return; }
    if (t === "work") return void clickId("nav-work");
    if (t === "code") return void clickId("nav-code");
    if (t === "plan") return void (g("showSchedule") ? showSchedule(true) : clickId("nav-sched"));
    if (t === "inbox") return void (window.MemuInbox ? MemuInbox.open() : say(L("«Входящие» недоступны", "The Inbox is unavailable"), "warn"));
    if (t === "skills") return void clickId("nav-skills");
    if (t === "catalog") return void (window.showSkillsModal ? showSkillsModal({ tab: "catalog" }) : clickId("nav-skills"));
    if (t === "search") return void (window.MemuSearch ? MemuSearch.open("") : null);
  });
  registerAction("inbox.open", () => { closePanels(); if (window.MemuInbox) MemuInbox.open(); });
  registerAction("module.open", async (a) => {
    if (!window.MemuModules) { say(L("Модули недоступны", "Modules are unavailable"), "warn"); return; }
    const ok = await MemuModules.navigate(a.id, { query: a.q || "", from: "link" });
    if (ok === false) return;
  });
  registerAction("search.open", (a) => { if (window.MemuSearch) MemuSearch.open(a.q || ""); });
  registerAction("quick.open", (a) => {
    say(L("Быстрый доступ открывается только в приложении — текст вставлен в чат.", "Quick Access opens only in the app — the text is put into the chat."), "info");
    prefill(a.q);
  });
  registerAction("plan.open", () => { closePanels(); if (g("showSchedule")) showSchedule(true); else clickId("nav-sched"); });
  registerAction("dfy.start", async (a) => {
    if (!window.MemuDFY) { say(L("«Сделай за меня» недоступно", "Do For You is unavailable"), "warn"); return; }
    try {
      await MemuDFY.start(String(a.task || ""), [], "link");
      say(L("Задача «Сделай за меня» запущена — вопросы появятся во «Входящих».", "Do For You task started — its questions will appear in the Inbox."), "ok");
      if (window.MemuInbox) MemuInbox.open();
    } catch (e) { say(String(e.message || e), "error"); }
  });
  registerAction("skills.add", (a) => {
    if (!window.MemuSkillsUI) { say(L("Скиллы недоступны", "Skills are unavailable"), "warn"); return; }
    MemuSkillsUI.addImported(a.items || [], "url", a.label || "");
  });

  async function runUi(ui) {
    if (!ui || !ui.do) return;
    const fn = actions.get(ui.do);
    if (!fn) { say(L("Это окно не умеет: ", "This window cannot do: ") + ui.do, "warn"); return; }
    try { await fn(ui); } catch (e) { say(String(e.message || e), "error"); }
  }

  /* ------------------------------------------------------------ queue */
  const seen = new Set();
  let polling = false, dialogOpen = false;
  const queue = [];

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const data = await call("/api/pg/deeplink/pending?mark=1&lang=" + lang());
      for (const item of data.items || []) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        queue.push(item);
      }
    } catch (e) { /* server restarting */ }
    polling = false;
    pump();
  }

  async function pump() {
    if (dialogOpen) return;
    const item = queue.shift();
    if (!item) return;
    if (item.danger === "safe") {
      const res = await call(`/api/pg/deeplink/item/${encodeURIComponent(item.id)}/run`, { lang: lang() });
      finish(res);
      return pump();
    }
    dialogOpen = true;
    try { await confirmDialog(item); } finally { dialogOpen = false; }
    pump();
  }

  function finish(res) {
    if (!res) return;
    if (res.ok === false) { say(res.message || L("Ссылка не выполнена", "The link was not run"), "error"); return; }
    if (res.message) say(res.message, "ok");
    if (res.ui) runUi(res.ui);
  }

  /* ------------------------------------------------------------ the dialog */
  function confirmDialog(item) {
    return new Promise((resolve) => {
      const opener = document.activeElement;
      const node = document.createElement("div");
      node.className = "dlk-layer";
      const inputs = (item.inputs || []).map((f) => `<label class="dlk-field"><span>${esc(f.label)}</span>
        <input type="${f.type === "password" ? "password" : "text"}" data-dlk-input="${esc(f.id)}" autocomplete="off" spellcheck="false">
        ${f.hint ? `<small>${esc(f.hint)}</small>` : ""}</label>`).join("");
      const srcIcon = item.source === "cli" ? "term" : "link";
      node.innerHTML = `<div class="dlk-card" role="alertdialog" aria-modal="true" aria-labelledby="dlk-title" aria-describedby="dlk-what">
        <div class="dlk-head">${ico("shield")}<div><div class="dlk-kicker">${esc(L("Подтвердите действие из ссылки", "Confirm an action from a link"))}</div>
          <h3 id="dlk-title">${esc(item.title)}</h3></div></div>
        <div class="dlk-what" id="dlk-what"><div class="dlk-label">${esc(L("Что произойдёт", "What will happen"))}</div>
          <ul>${(item.lines || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
          <div class="dlk-preview" ${item.has_preview ? "" : "hidden"}><div class="dlk-loading">${esc(L("Загружаю подробности…", "Loading details…"))}</div></div></div>
        ${inputs ? `<div class="dlk-inputs">${inputs}</div>` : ""}
        <div class="dlk-src">${ico(srcIcon)}<div><div class="dlk-label">${esc(L("Откуда", "Source"))}</div><div>${esc(item.source_label)}</div>
          <code class="dlk-url" title="${esc(item.url)}">${esc(item.url)}</code></div></div>
        <div class="dlk-note">${esc(L("Не узнаёте эту ссылку — нажмите «Отмена». Ничего не произойдёт, пока вы не подтвердите.",
          "Don't recognise this link? Press Cancel. Nothing happens until you confirm."))}</div>
        <div class="dlk-actions"><button type="button" class="pbtn ghost" data-dlk-no>${esc(L("Отмена", "Cancel"))}</button>
          <button type="button" class="pbtn" data-dlk-yes disabled>${esc(L("Выполнить", "Run"))}</button></div></div>`;
      const yes = node.querySelector("[data-dlk-yes]");
      const no = node.querySelector("[data-dlk-no]");
      let previewOk = !item.has_preview, unlocked = false, done = false;
      const sync = () => { yes.disabled = !(unlocked && previewOk); };
      const close = (value) => {
        if (done) return; done = true;
        document.removeEventListener("keydown", onKey, true);
        node.classList.add("dlk-leaving");
        setTimeout(() => node.remove(), 140);
        try { opener && opener.focus && opener.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
        resolve(value);
      };
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancel(); }
        else if (e.key === "Tab") {
          const items = [...node.querySelectorAll("button:not([disabled]), input")];
          const i = items.indexOf(document.activeElement);
          e.preventDefault(); items[(i + (e.shiftKey ? items.length - 1 : 1) + items.length) % items.length]?.focus();
        }
      };
      async function cancel() {
        await call(`/api/pg/deeplink/item/${encodeURIComponent(item.id)}/cancel`, {}).catch(() => {});
        close(false);
      }
      async function confirm() {
        if (yes.disabled) return;
        const values = {};
        node.querySelectorAll("[data-dlk-input]").forEach((el) => { if (el.value) values[el.dataset.dlkInput] = el.value; });
        yes.disabled = true; no.disabled = true;
        yes.textContent = L("Выполняю…", "Running…");
        const res = await call(`/api/pg/deeplink/item/${encodeURIComponent(item.id)}/run`, { confirm: true, inputs: values, lang: lang() });
        close(true);
        finish(res);
      }
      node.addEventListener("click", (e) => {
        if (e.target.closest("[data-dlk-yes]")) confirm();
        else if (e.target.closest("[data-dlk-no]")) cancel();
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(node);
      requestAnimationFrame(() => no.focus());
      // A short pause before «Run» can be pressed: no accidental Enter / double click from the opening app.
      setTimeout(() => { unlocked = true; sync(); }, 1200);
      if (item.has_preview) {
        const box = node.querySelector(".dlk-preview");
        call(`/api/pg/deeplink/item/${encodeURIComponent(item.id)}/preview`, { lang: lang() }).then((p) => {
          if (done) return;
          if (!p || p.ok === false) {
            box.innerHTML = `<div class="dlk-error">${esc((p && p.message) || L("Не удалось загрузить подробности — выполнить нельзя.", "Could not load the details — cannot run."))}</div>`;
            return;
          }
          box.innerHTML = `${(p.lines || []).length ? `<ul>${p.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}
            ${p.code ? `<details class="dlk-code" open><summary>${esc(L("Полностью", "In full"))}</summary><pre>${esc(p.code)}</pre></details>` : ""}`;
          previewOk = true; sync();
        });
      }
    });
  }

  /* ------------------------------------------------------------ links inside MemuAI (chat replies, docs) */
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest('a[href^="memuai:" i]');
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    const inChat = !!a.closest("[data-msg], .msg, .message");
    open(a.getAttribute("href"), inChat ? "chat" : "app");
  }, true);

  async function open(url, source) {
    const res = await call("/api/pg/deeplink/dispatch", { url, source: source || "app", lang: lang() });
    if (res.ok === false) { say(res.message || L("Ссылка отклонена", "The link was rejected"), "error"); return res; }
    poll();
    return res;
  }

  /* ------------------------------------------------------------ «Копировать ссылку memuai://» */
  async function copyLink(route, params) {
    const res = await call("/api/pg/deeplink/build", { route, params: params || {}, lang: lang() });
    if (!res.ok) { say(res.message || L("Не удалось сделать ссылку", "Could not make the link"), "error"); return ""; }
    try {
      if (typeof copyText === "function") await copyText(res.link, false); else await navigator.clipboard.writeText(res.link);
      say(L("Ссылка скопирована: ", "Link copied: ") + res.link, "ok");
    } catch (e) { say(res.link, "info"); }
    return res.link;
  }
  function chatMenuItems(chat) {
    if (!chat || !chat.id || chat.temp) return [];
    return [{ label: L("Копировать ссылку memuai://", "Copy memuai:// link"), icon: "link", run: () => copyLink("chat", { id: chat.id }) }];
  }

  /* ------------------------------------------------------------ Settings → «Ссылки и командная строка» */
  const settings = (() => {
    let st = null, ctx = null;
    const root = () => document.getElementById("dlk-settings-root");
    const kit = () => (ctx && ctx.kit) || window.MemuSettingsKit;
    const examples = () => [
      ["memuai://chat/new?q=" + encodeURIComponent(L("Привет", "Hello")), L("Новый чат с текстом в поле ввода (не отправляется)", "A new chat with the text in the box (not sent)")],
      ["memuai://chat/new?q=" + encodeURIComponent(L("Кратко о новостях", "News in brief")) + "&send=1", L("Новый чат и сразу отправить — спросит подтверждение", "A new chat, sent at once — asks for confirmation")],
      ["memuai://open/plan", L("Открыть «Планирование» (также inbox, skills, catalog, work, code, search…)", "Open Scheduling (also inbox, skills, catalog, work, code, search…)")],
      ["memuai://settings/appearance", L("Страница настроек", "A settings page")],
      ["memuai://search?q=" + encodeURIComponent(L("отпуск", "vacation")), L("Поиск по MemuAI (Ctrl+K)", "Search MemuAI (Ctrl+K)")],
      ["memuai://quick?q=" + encodeURIComponent(L("Переведи", "Translate")), L("Быстрый доступ с текстом", "Quick Access with a text")],
      ["memuai://task/run?id=…", L("Запустить задачу сейчас — с подтверждением", "Run a task now — with confirmation")],
      ["memuai://proxy/composite", L("Страница MemuAI Proxy", "A MemuAI Proxy page")],
    ];
    const cli = () => [
      ['memu ask "' + L("Сколько будет 2+2?", "What is 2+2?") + '"', L("ответ модели в терминале (-m модель, --stream)", "the model's answer in the terminal (-m model, --stream)")],
      ["git diff | memu ask " + L('"Опиши изменения"', '"Describe the changes"'), L("текст из конвейера", "text from a pipe")],
      ["memu chat new \"" + L("Идея", "Idea") + "\"", L("новый чат в окне MemuAI", "a new chat in the MemuAI window")],
      ["memu open memuai://open/inbox", L("открыть любую ссылку", "open any link")],
      ["memu task list / memu task run <id>", L("задачи; запуск подтверждается в окне (или --yes)", "tasks; a run is confirmed in the window (or --yes)")],
      ["memu models · memu status · memu inbox", L("модели, состояние, «Входящие»", "models, status, the Inbox")],
      ["memu search-files \"*.pdf\" · memu note file.md", L("поиск файлов на ПК, открыть в MemuNote", "PC file search, open in MemuNote")],
      ["memu link chat/new q=" + L("Привет", "Hi"), L("собрать ссылку; --json для скриптов; memu completion powershell", "build a link; --json for scripts; memu completion powershell")],
    ];
    function codeRows(rows) {
      return `<div class="dlk-ex">${rows.map(([code, text]) => `<div class="dlk-ex-row"><code>${esc(code)}</code><span>${esc(text)}</span>
        <button type="button" class="dlk-copy" data-dlk-copy="${esc(code)}" title="${esc(L("Копировать", "Copy"))}" aria-label="${esc(L("Копировать", "Copy"))}">${ico("copy")}</button></div>`).join("")}</div>`;
    }
    function journalHtml() {
      const rows = (st && st.journal) || [];
      if (!rows.length) return `<p class="sx-desc">${esc(L("Пока ни одной ссылки.", "No links yet."))}</p>`;
      const name = { queued: L("получена", "received"), done: L("выполнена", "done"), cancelled: L("отменена", "cancelled"),
        rejected: L("отклонена", "rejected"), failed: L("ошибка", "failed"), rate_limited: L("слишком часто", "too frequent"),
        disabled: L("ссылки выключены", "links off"), cli_disabled: L("memu выключен", "memu off"), chat_disabled: L("ссылки в чатах выключены", "chat links off") };
      return `<div class="dlk-journal">${rows.slice(0, 12).map((r) => `<div class="dlk-jrow ${esc(r.event)}"><time>${esc(new Date(r.ts * 1000).toLocaleString(isEn() ? "en-GB" : "ru-RU", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" }))}</time>
        <b>${esc(name[r.event] || r.event)}</b><code title="${esc(r.url)}">${esc(r.url || r.route)}</code>${r.detail && r.event !== "queued" && r.event !== "done" ? `<small>${esc(r.detail)}</small>` : ""}</div>`).join("")}</div>
        <button type="button" class="pbtn ghost compact" data-dlk-act="clear">${esc(L("Очистить журнал", "Clear the log"))}</button>`;
    }
    function body() {
      const K = kit();
      const s = (st && st.settings) || { enabled: true, always_ask: false, cli: true, chat_links: true };
      const reg = (st && st.registration) || {};
      const toggle = (id, on) => (K ? K.toggle("dlk-" + id, !!on, `data-dlk-set="${id}"`) : `<input type="checkbox" data-dlk-set="${id}" ${on ? "checked" : ""}>`);
      const row = (label, desc, control) => (K ? K.row({ label, desc, control }) : `<div><b>${esc(label)}</b> ${control}<p>${esc(desc)}</p></div>`);
      const sec = (id, title, html, card = true, desc = "") => (K ? K.section({ id, title, desc: desc ? esc(desc) : "", body: html, card }) : `<h3>${esc(title)}</h3>${desc ? `<p>${esc(desc)}</p>` : ""}${html}`);
      const regText = !reg.supported ? L("Регистрация в Windows — только в установленном MemuAI для Windows.", "Windows registration is for the installed MemuAI on Windows only.")
        : reg.current ? L("Windows открывает memuai:// в этом MemuAI.", "Windows opens memuai:// in this MemuAI.")
        : reg.frozen ? L("Обработчик не зарегистрирован или ведёт в другую папку — MemuAI исправит это при запуске.", "The handler is missing or points elsewhere — MemuAI repairs it at start.")
        : L("Запущено из исходников — обработчик не регистрируется.", "Running from source — the handler is not registered.");
      return sec("settings-links-intro", "", `<div class="dlk-hero"><div>
          <p>${esc(L("Ссылка вида memuai://… из браузера, заметок или ярлыка открывает нужное место MemuAI: новый чат с запросом, раздел, настройки, задачу. Команда memu делает то же из терминала и умеет спрашивать модель прямо там.",
            "A memuai://… link from a browser, notes or a shortcut opens the right place in MemuAI: a new chat with a request, a section, settings, a task. The memu command does the same from a terminal and can ask a model right there."))}</p>
          <p class="dlk-safe">${ico("shield")}<span>${esc(L("Безопасно: ссылка только открывает. Всё, что меняет данные, запускает работу или тратит деньги, MemuAI сначала показывает в своём окне и ждёт вашего «Выполнить». Ключи и пароли по ссылкам не принимаются, команды Windows ссылка запустить не может.",
            "Safe: a link only opens things. Anything that changes data, starts work or spends money is shown in the MemuAI window first and waits for your «Run». Keys and passwords are never accepted from links, and a link cannot run Windows commands."))}</span></p></div></div>`, false)
        + sec("settings-links-options", L("Разрешения", "Permissions"),
          row(L("Открывать ссылки memuai://", "Open memuai:// links"), L("Выключите — и MemuAI будет отклонять все ссылки (они попадут только в журнал).", "Turn off and MemuAI rejects every link (they only go to the log)."), toggle("enabled", s.enabled))
          + row(L("Всегда спрашивать", "Always ask"), L("Даже простое «открыть раздел» сначала покажет окно подтверждения.", "Even a simple \"open a section\" shows the confirmation first."), toggle("always_ask", s.always_ask))
          + row(L("Команда memu", "The memu command"), L("Разрешить терминалу открывать чаты, задачи и ссылки в окне MemuAI. «memu ask» работает через Proxy и от этого не зависит.", "Let the terminal open chats, tasks and links in the MemuAI window. \"memu ask\" goes through the Proxy and does not depend on this."), toggle("cli", s.cli))
          + row(L("Ссылки в ответах ИИ", "Links in AI replies"), L("Клик по memuai:// в чате работает по тем же правилам, но в окне будет пометка, что ссылку мог написать ИИ.", "A click on memuai:// in a chat follows the same rules; the dialog notes the AI may have written it."), toggle("chat_links", s.chat_links))
          + row(L("Регистрация в Windows", "Windows registration"), regText, ""))
        + sec("settings-links-examples", L("Примеры ссылок", "Link examples"), codeRows(examples()), false,
          L("Ссылку можно вставить в браузер, заметку, ярлык или документ. Ссылку на чат даёт меню чата → «Копировать ссылку memuai://».", "Paste a link into a browser, a note, a shortcut or a document. A link to a chat: the chat menu → «Copy memuai:// link»."))
        + sec("settings-links-cli", L("Командная строка memu", "The memu command line"), codeRows(cli()), false,
          L("Команда memu ставится вместе с MemuAI и добавляется в PATH (откройте новый терминал после установки). memucode — отдельная команда для кода, они не мешают друг другу. Подробно: memu --help.", "The memu command ships with MemuAI and is added to PATH (open a new terminal after installing). memucode is the separate coding command; they don't conflict. Details: memu --help."))
        + sec("settings-links-journal", L("Последние ссылки", "Recent links"), journalHtml(), false);
    }
    function paint() { const el = root(); if (el) el.innerHTML = body(); }
    async function load() { st = await call("/api/pg/deeplink/settings?lang=" + lang()).catch(() => null); paint(); }
    function html(c) { ctx = c || ctx; return `<div id="dlk-settings-root" class="dlk-settings">${body()}</div>`; }
    function mount(c) {
      ctx = c || ctx;
      const el = root(); if (!el) return;
      if (!el.dataset.bound) {
        el.dataset.bound = "1";
        el.addEventListener("change", async (e) => {
          const t = e.target.closest("[data-dlk-set]"); if (!t) return;
          const res = await call("/api/pg/deeplink/settings", { [t.dataset.dlkSet]: !!t.checked });
          if (res.ok) { st = Object.assign(st || {}, { settings: res.settings }); say(L("Сохранено", "Saved"), "ok"); }
          else { t.checked = !t.checked; say(res.message || "Error", "error"); }
        });
        el.addEventListener("click", async (e) => {
          const c2 = e.target.closest("[data-dlk-copy]");
          if (c2) { try { if (typeof copyText === "function") await copyText(c2.dataset.dlkCopy, false); else await navigator.clipboard.writeText(c2.dataset.dlkCopy); say(L("Скопировано", "Copied"), "ok"); } catch (err) { /* ignore */ } return; }
          const act = e.target.closest("[data-dlk-act]");
          if (act && act.dataset.dlkAct === "clear") { await call("/api/pg/deeplink/journal/clear", {}); await load(); }
        });
      }
      load();
    }
    return { html, mount, icon: ico("link") };
  })();

  window.MemuLinks = { poll, open, registerAction, copyLink, chatMenuItems, settings, _actions: actions };
  window.MemuLinksSettings = settings;

  // The queue is read at start (a link that started MemuAI), on focus, when the server nudges
  // (evaluate_js → MemuLinks.poll) and on the «links» event of the inbox SSE stream (memu:links).
  // Round 36 (perf): no 4-second timer any more — nothing runs while no link is waiting.
  const boot = () => { poll(); document.addEventListener("memu:links", () => poll()); };
  window.addEventListener("focus", () => poll());
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") poll(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(boot, 600), { once: true });
  else setTimeout(boot, 600);
})();
