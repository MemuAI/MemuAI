/* MemuAI · Safe mode (Round 29) — the banner, «what is disabled», restart, Settings → About.

   window.MEMU_SAFE_MODE comes from /api/pg/safe-mode/boot.js (loaded in <head>, before the
   first paint). Nothing here changes user data: safe mode is a runtime overlay decided by
   app/safe_mode.py. This file also reports a healthy start (POST /api/pg/safe-mode/healthy)
   so the crash guard knows MemuAI reached a working window.

   Exposes window.MemuSafeMode = { active, state, restart(mode), details(), settingsHtml(kit), bindSettings() }. */
(function () {
  "use strict";

  const state = window.MEMU_SAFE_MODE || { active: false };
  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) {}
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVG = {
    shield: '<svg viewBox="0 0 24 24"><path d="M12 3 5 6v6c0 4.2 2.9 7.6 7 9 4.1-1.4 7-4.8 7-9V6l-7-3z"/><path d="m9 12 2 2 4-4"/></svg>',
    restart: '<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/></svg>',
    list: '<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/></svg>',
    close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    off: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="m6.5 6.5 11 11"/></svg>',
  };
  const ico = (name) => `<span class="ui-icon" aria-hidden="true">${SVG[name] || ""}</span>`;

  async function post(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error((isEn() ? data.error_en : data.error_ru) || data.error || `HTTP ${response.status}`);
      error.payload = data;
      throw error;
    }
    return data;
  }
  const notify = (text, type = "ok") => { try { if (typeof toast === "function") toast(text, type); } catch (e) {} };

  /* ---------------------------------------------------------------- health */
  setTimeout(() => { post("/api/pg/safe-mode/healthy").catch(() => {}); }, Math.max(3, Number(state.healthy_after) || 8) * 1000);

  /* --------------------------------------------------------------- restart */
  function restartCover(text) {
    const cover = document.createElement("div");
    cover.className = "memu-safe-cover";
    cover.setAttribute("role", "status");
    cover.innerHTML = `<div class="memu-safe-cover-card">${ico("restart")}<b>${esc(text)}</b><small>${esc(L("MemuAI откроется снова через пару секунд.", "MemuAI will open again in a couple of seconds."))}</small></div>`;
    document.body.appendChild(cover);
    return cover;
  }

  async function restart(mode = "normal") {
    const cover = restartCover(mode === "safe" ? L("Перезапуск в безопасном режиме…", "Restarting in safe mode…") : L("Перезапуск MemuAI…", "Restarting MemuAI…"));
    try {
      await post("/api/pg/app/restart", { mode });
    } catch (error) {
      cover.remove();
      notify(error.message || L("Не удалось перезапустить MemuAI", "Could not restart MemuAI"), "error");
    }
  }

  /* ----------------------------------------------------------- the details */
  const SOURCE = {
    flag: ["Запущен ярлыком «MemuAI (безопасный режим)» или с ключом --safe-mode", "Started from the «MemuAI (safe mode)» shortcut or with --safe-mode"],
    env: ["Включён переменной окружения MEMUAI_SAFE_MODE", "Turned on by the MEMUAI_SAFE_MODE environment variable"],
    marker: ["Включён в настройках («Запускать в безопасном режиме»)", "Turned on in Settings («Start in safe mode»)"],
    shift: ["Вы удерживали Shift при запуске", "You held Shift while MemuAI started"],
    crash: ["MemuAI несколько раз подряд не смог нормально запуститься", "MemuAI failed to start properly several times in a row"],
    restart: ["Перезапуск из MemuAI", "Restarted from MemuAI"],
  };

  function closeDialog(dialog, opener) {
    dialog.classList.add("is-closing");
    setTimeout(() => dialog.remove(), 160);
    if (opener && opener.focus) opener.focus();
  }

  async function details(opener) {
    let data = state;
    try { data = await fetch("/api/pg/safe-mode", { cache: "no-store" }).then((r) => r.json()); } catch (e) {}
    const rows = (data.disabled || []).map((row) => {
      const count = row.count === null || row.count === undefined ? "" : `<span class="memu-safe-count" data-zero="${row.count ? 0 : 1}">${esc(row.count)}</span>`;
      return `<li>${ico("off")}<div><b>${esc(isEn() ? row.en : row.ru)}${count}</b><small>${esc(isEn() ? row.note_en : row.note_ru)}</small></div></li>`;
    }).join("");
    const source = SOURCE[data.source] || SOURCE.flag;
    const dialog = document.createElement("div");
    dialog.className = "memu-safe-dialog";
    dialog.innerHTML = `<div class="memu-safe-dialog-card" role="dialog" aria-modal="true" aria-labelledby="memu-safe-title">
      <header><span class="memu-safe-mark">${ico("shield")}</span><div><h2 id="memu-safe-title">${esc(L("Безопасный режим", "Safe mode"))}</h2>
        <p>${esc(isEn() ? source[1] : source[0])}</p></div>
        <button type="button" class="memu-safe-x" data-close aria-label="${esc(L("Закрыть", "Close"))}">${ico("close")}</button></header>
      <p class="memu-safe-lead">${esc(L("MemuAI запущен почти «чистым»: всё, что расширяет программу, отключено только на этот запуск. Ваши чаты, настройки, модули и темы не изменены и не удалены — после обычного перезапуска всё вернётся.",
        "MemuAI started almost clean: everything that extends the app is off for this run only. Your chats, settings, modules and themes are not changed or deleted — a normal restart brings everything back."))}</p>
      <ul class="memu-safe-list">${rows || `<li>${ico("off")}<div><b>${esc(L("Расширения", "Extensions"))}</b></div></li>`}</ul>
      <p class="memu-safe-hint">${esc(L("Чтобы найти виновника, перезапустите MemuAI обычным образом и отключайте расширения по одному: модули — в «Модулях», плагины — в «Быстром доступе», темы — во «Внешнем виде».",
        "To find the culprit, restart MemuAI normally and turn extensions off one by one: modules in «Modules», plugins in «Quick Ask», themes in «Appearance»."))}</p>
      <footer><button type="button" class="pbtn ghost" data-docs>${esc(L("Справка", "Help"))}</button>
        <button type="button" class="pbtn" data-restart>${ico("restart")} ${esc(L("Перезапустить в обычном режиме", "Restart normally"))}</button></footer>
    </div>`;
    document.body.appendChild(dialog);
    const card = dialog.querySelector(".memu-safe-dialog-card");
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog || event.target.closest("[data-close]")) closeDialog(dialog, opener);
      else if (event.target.closest("[data-restart]")) restart("normal");
      else if (event.target.closest("[data-docs]")) {
        closeDialog(dialog, opener);
        try { if (window.MemuDocs && typeof MemuDocs.open === "function") MemuDocs.open("safe-mode"); } catch (e) {}
      }
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.stopPropagation(); closeDialog(dialog, opener); }
      if (event.key === "Tab") {  // keep focus inside the dialog
        const items = [...card.querySelectorAll("button")];
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    requestAnimationFrame(() => card.querySelector("[data-restart]")?.focus());
  }

  /* ---------------------------------------------------------------- banner */
  function banner() {
    if (!state.active || document.querySelector(".memu-safe-banner")) return;
    const node = document.createElement("div");
    node.className = "memu-safe-banner";
    node.setAttribute("role", "status");
    const fill = () => { node.innerHTML = `<span class="memu-safe-mark">${ico("shield")}</span>
      <span class="memu-safe-text"><b>${esc(L("Безопасный режим", "Safe mode"))}</b><small>${esc(L("Расширения, свои темы и компаньон отключены. Данные не изменены.", "Extensions, custom themes and the companion are off. Your data is unchanged."))}</small></span>
      <button type="button" class="memu-safe-btn" data-what title="${esc(L("Что отключено", "What is disabled"))}">${ico("list")}<span>${esc(L("Что отключено", "What is off"))}</span></button>
      <button type="button" class="memu-safe-btn primary" data-normal>${ico("restart")}<span>${esc(L("Обычный режим", "Restart normally"))}</span></button>
      <button type="button" class="memu-safe-btn icon" data-fold aria-label="${esc(L("Свернуть", "Collapse"))}" title="${esc(L("Свернуть", "Collapse"))}">${ico("close")}</button>`; };
    fill();
    // r29c: the banner is built before the preferences arrive (PG_LANG comes from localStorage first), so an
    // English profile could get a Russian banner; follow <html lang> (pgApplyLang) and re-label.
    let lang = document.documentElement.lang;
    new MutationObserver(() => { if (document.documentElement.lang !== lang) { lang = document.documentElement.lang; fill(); } })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
    node.addEventListener("click", (event) => {
      if (event.target.closest("[data-what]")) details(event.target.closest("button"));
      else if (event.target.closest("[data-normal]")) restart("normal");
      else if (event.target.closest("[data-fold]")) { event.stopPropagation(); node.classList.add("is-folded"); }
      else if (node.classList.contains("is-folded")) node.classList.remove("is-folded");
    });
    node.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && node.classList.contains("is-folded") && event.target === node) {
        event.preventDefault(); node.classList.remove("is-folded");
      }
    });
    node.tabIndex = 0;
    document.body.appendChild(node);
    // r29c: at the bottom of an open chat the banner covered the composer (the ＋ button and the first line of
    // the text). When the composer sits near the bottom edge, float the banner just above it instead.
    const place = () => {
      const composer = document.querySelector(".composer");
      const rect = composer && composer.offsetParent !== null ? composer.getBoundingClientRect() : null;
      const lifted = rect && rect.width && window.innerHeight - rect.bottom < 140 && !node.classList.contains("is-folded");
      node.style.bottom = lifted ? `${Math.round(window.innerHeight - rect.top + 10)}px` : "";
    };
    place();
    window.addEventListener("resize", place);
    setInterval(place, 700);
    node.addEventListener("click", () => requestAnimationFrame(place));
    document.documentElement.dataset.safeMode = "1";
    const style = document.getElementById("memu-user-theme");
    if (style) style.media = "not all";
  }

  /* ------------------------------------------------- Settings → About row */
  function settingsHtml(K) {
    if (!K) return "";
    const marker = (state.marker && state.marker.exists) ? (state.marker.once ? "once" : "always") : "off";
    const active = !!state.active;
    return K.section({ id: "settings-safe-mode", title: L("Безопасный режим", "Safe mode"), icon: SVG.shield,
      keywords: "safe mode безопасный режим troubleshooting неполадки plugins плагины modules модули crash сбой shift",
      desc: L("Запуск без модулей, плагинов, своих тем, серверов MCP, компаньона и слова-активатора — когда что-то сломалось. Данные не меняются.",
        "Start without modules, plugins, custom themes, MCP servers, the companion and the wake word — for when something broke. Your data is not changed."),
      body: (active ? K.note(L("Сейчас MemuAI работает в безопасном режиме.", "MemuAI is running in safe mode right now."), "warn") : "")
        + K.row({ label: active ? L("Перезапустить в обычном режиме", "Restart normally") : L("Перезапустить в безопасном режиме", "Restart in safe mode"),
          desc: active ? L("Всё отключённое снова загрузится", "Everything that was turned off loads again")
            : L("Ещё способы: ярлык «MemuAI (безопасный режим)» в меню «Пуск» или Shift при запуске", "Other ways: the «MemuAI (safe mode)» Start-menu shortcut or holding Shift at start"),
          keywords: "restart перезапуск safe безопасный",
          control: `<button type="button" class="pbtn ${active ? "" : "ghost"} sx-btn-sm" id="safe-restart">${ico("restart")} ${esc(active ? L("Обычный режим", "Normal mode") : L("Безопасный режим", "Safe mode"))}</button>` })
        + K.row({ label: L("Запускать в безопасном режиме", "Start in safe mode"),
          desc: L("«Один раз» — только следующий запуск; «Всегда» — пока не выключите здесь или кнопкой на баннере", "«Once» — the next start only; «Always» — until you turn it off here or from the banner"),
          keywords: "always всегда once один раз marker",
          control: K.select("safe-marker", [["off", L("Нет", "No")], ["once", L("Один раз", "Once")], ["always", L("Всегда", "Always")]], marker, { width: 150 }) })
        + (active ? K.row({ label: L("Что отключено", "What is disabled"), keywords: "disabled отключено diagnostics диагностика",
          control: `<button type="button" class="pbtn ghost sx-btn-sm" id="safe-details">${ico("list")} ${esc(L("Показать", "Show"))}</button>` }) : "") });
  }

  function bindSettings() {
    const button = document.getElementById("safe-restart");
    if (button) button.onclick = () => restart(state.active ? "normal" : "safe");
    const details_ = document.getElementById("safe-details");
    if (details_) details_.onclick = () => details(details_);
    const select = document.getElementById("safe-marker");
    if (select) select.onchange = async () => {
      try {
        const data = await post("/api/pg/safe-mode/marker", { mode: select.value });
        state.marker = data.marker;
        try { window.MemuSettingsKit && MemuSettingsKit.saved && MemuSettingsKit.saved(true); } catch (e) {}
      } catch (error) { notify(error.message, "error"); }
    };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", banner);
  else banner();

  window.MemuSafeMode = { active: !!state.active, state, restart, details, settingsHtml, bindSettings };
})();
