/* Round 33 — modules outside the MemuAI window (#6, #26). Server side: app/module_apps.py.
 *
 * Loaded in <head>, so the «module only» flag is on <html> before the first paint:
 *   /?module_app=<id>  → html[data-module-app="<id>"]: this page is one module's own window
 *                        (installed module app, or a module moved out of MemuAI). modules.js
 *                        opens just that module, filling the window; the rest of MemuAI is hidden.
 * In the main window this file adds:
 *   - «Open in a separate window» / «Install as an app…» to the module menus;
 *   - the question when MemuAI is closed while modules are open (askAppClose, asked by Python);
 *   - Settings → Modules → «Modules as apps» (installed apps, what closing MemuAI does).
 * Every visible string is RU/EN; icons come from the app's icon() set.
 */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const requested = String(params.get("module_app") || "");
  const APP = /^[a-z0-9][a-z0-9_-]{0,63}$/.test(requested) ? requested : "";
  if (APP) {
    document.documentElement.dataset.moduleApp = APP;
    window.MEMU_MODULE_APP = APP;
  }

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const t = (ru, english) => (en() ? english : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const ic = (name) => (typeof icon === "function" ? icon(name) : "");
  const M = () => window.MemuModules;
  const note = (text, type, action) => { try { if (typeof toast === "function") toast(text, type || "ok", action || null); } catch (_) { /* no toast */ } };
  const MARK = '<span class="ui-icon"><svg viewBox="0 0 24 24"><path d="M12 3.5c.5 3.9 2.6 6 6.5 6.5-3.9.5-6 2.6-6.5 6.5-.5-3.9-2.6-6-6.5-6.5 3.9-.5 6-2.6 6.5-6.5z"/><path d="M18.5 15.5c.2 1.4 1 2.2 2.5 2.5-1.5.3-2.3 1.1-2.5 2.5-.2-1.4-1-2.2-2.5-2.5 1.5-.3 2.3-1.1 2.5-2.5z"/></svg></span>';
  const APP_ICON = '<span class="ui-icon"><svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><path d="M17 13.5v7M13.5 17h7"/></svg></span>';
  const POP_ICON = '<span class="ui-icon"><svg viewBox="0 0 24 24"><rect x="3.5" y="7.5" width="13" height="13" rx="2.5"/><path d="M13 3.5h7.5V11M20.5 3.5 11 13"/></svg></span>';
  const DOCK_ICON = '<span class="ui-icon"><svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M14 8h-4v4M10 8l6.5 6.5"/></svg></span>';

  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  let state = { apps: [], on_app_close: "ask", custom_appbar: true, appbar: {}, native: false, shortcuts_supported: false, open_windows: [] };
  let detached = new Set();
  function loadState() {
    return api("/api/pg/module-apps").then((data) => {
      state = Object.assign(state, data || {});
      detached = new Set(state.open_windows || []);
      markLaunchers();
      return state;
    }).catch(() => state);
  }
  const appOf = (id) => (state.apps || []).find((row) => row.id === id && row.installed);
  const rowTitle = (row) => (row ? (en() ? (row.title_en || row.title) : (row.title || row.title_en)) : "");
  const catalogRow = (id) => ((M() && M().catalog) || []).find((row) => row.id === id) || null;

  function markLaunchers() {
    document.querySelectorAll("[data-module-open].module-launch").forEach((button) => {
      button.classList.toggle("is-detached", detached.has(button.dataset.moduleOpen));
    });
  }

  /* ----------------------------- the module glyph as an app icon ----------------------------- */
  function iconPng(row) {
    // Round 39: the module's own icon (module-icons.js) — the .ico of the app and its shortcuts match MemuAI.
    const fresh = (row && catalogRow(row.id)) || row;
    if (window.MemuModuleIcon) return window.MemuModuleIcon.png(fresh, 256);
    return new Promise((resolve) => {
      try {
        const markup = ic(row && row.icon) || ic("work");
        const inner = (markup.match(/<svg[\s\S]*<\/svg>/) || [""])[0];
        if (!inner) return resolve("");
        const svg = inner.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" stroke="#ffffff" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"');
        const style = getComputedStyle(document.documentElement);
        const accent = (row && /^#[0-9a-f]{6}$/i.test(row.accent || "") ? row.accent : style.getPropertyValue("--accent").trim()) || "#ae5630";
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 256;
        const g = canvas.getContext("2d");
        const grad = g.createLinearGradient(0, 0, 256, 256);
        grad.addColorStop(0, accent);
        grad.addColorStop(1, `color-mix(in srgb, ${accent} 62%, #000)`);
        g.fillStyle = accent;
        g.beginPath();
        if (g.roundRect) g.roundRect(8, 8, 240, 240, 58); else g.rect(8, 8, 240, 240);
        g.fill();
        try { g.fillStyle = grad; g.fill(); } catch (_) { /* color-mix in a canvas gradient is not everywhere */ }
        const img = new Image();
        img.onload = () => {
          try {
            g.drawImage(img, 48, 48, 160, 160);
            resolve(canvas.toDataURL("image/png"));
          } catch (_) { resolve(""); }
        };
        img.onerror = () => resolve("");
        img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
      } catch (_) { resolve(""); }
    });
  }

  /* ------------------------------------- dialogs ------------------------------------- */
  function dialog(html, kind, onClose) {
    if (typeof panel !== "function") return false;
    panel(html, "module-apps-panel " + (kind || ""), { onClose });
    document.getElementById("panel-back")?.classList.add("over-modules");
    return true;
  }
  function closeDialog(reason) {
    if (typeof closePanel === "function") closePanel(reason || "cancel");
    document.getElementById("panel-back")?.classList.remove("over-modules");
  }

  async function installDialog(row) {
    if (!row) return;
    await loadState();
    const title = rowTitle(row);
    const existing = appOf(row.id);
    const preview = await iconPng(row);
    const shortcuts = state.shortcuts_supported;
    const html = `<div class="mapp-dialog">
      <div class="mapp-hero">
        <span class="mapp-tile">${preview ? `<img src="${preview}" alt="">` : ic(row.icon || "work")}</span>
        <div><h2>${esc(existing ? t(`«${title}» — приложение`, `“${title}” as an app`) : t(`Установить «${title}» как приложение`, `Install “${title}” as an app`))}</h2>
          <p class="sub">${esc(t("Модуль получит свой ярлык и свою кнопку на панели задач Windows и будет открываться отдельным окном — без главного окна MemuAI. Модели, данные и настройки остаются общими с MemuAI.",
            "The module gets its own shortcut and its own taskbar button and opens as a separate window — without the main MemuAI window. Models, data and settings stay shared with MemuAI."))}</p></div>
      </div>
      <div class="mapp-name"><span class="mapp-name-ico">${preview ? `<img src="${preview}" alt="">` : ""}</span><b>${esc(title)} (MemuAI)</b>
        <span class="mapp-cmd">MemuAI.exe --module ${esc(row.id)}</span></div>
      <div class="mapp-options">
        <label class="mapp-check"><input type="checkbox" id="mapp-start" ${!existing || existing.start_menu ? "checked" : ""} ${shortcuts ? "" : "disabled"}>
          <span><b>${esc(t("Ярлык в меню «Пуск»", "Start menu shortcut"))}</b><small>${esc(t("Ищется по названию, как любое приложение.", "Found by its name, like any app."))}</small></span></label>
        <label class="mapp-check"><input type="checkbox" id="mapp-desktop" ${!existing || existing.desktop ? "checked" : ""} ${shortcuts ? "" : "disabled"}>
          <span><b>${esc(t("Ярлык на рабочем столе", "Desktop shortcut"))}</b><small>${esc(t("Отдельный файл запуска модуля.", "A separate launcher file for the module."))}</small></span></label>
        <label class="mapp-check"><input type="checkbox" id="mapp-hide" ${existing && existing.hidden_in_memuai ? "checked" : ""}>
          <span><b>${esc(t("Убрать из списка модулей MemuAI", "Remove from the MemuAI module list"))}</b><small>${esc(t("Модуль будет жить только как отдельное приложение. Вернуть — в настройках модулей.", "The module lives only as a separate app. Bring it back in the module settings."))}</small></span></label>
      </div>
      ${shortcuts ? "" : `<p class="mapp-note">${ic("info")}<span>${esc(t("Ярлыки создаются только в Windows. Здесь модуль запомнится как приложение и откроется отдельным окном.", "Shortcuts are created on Windows only. Here the module is remembered as an app and opens in a separate window."))}</span></p>`}
      <div class="row-end">
        ${existing ? `<button class="pbtn ghost danger-text" id="mapp-remove">${esc(t("Удалить приложение", "Remove the app"))}</button><span class="mu-grow"></span>` : ""}
        <button class="pbtn ghost" id="mapp-cancel">${esc(t("Отмена", "Cancel"))}</button>
        <button class="pbtn" id="mapp-install">${APP_ICON} ${esc(existing ? t("Сохранить", "Save") : t("Установить", "Install"))}</button>
      </div></div>`;
    if (!dialog(html, "mapp-install-panel")) return;
    const $ = (id) => document.getElementById(id);
    $("mapp-cancel").onclick = () => closeDialog("cancel");
    if ($("mapp-remove")) $("mapp-remove").onclick = () => { closeDialog("cancel"); uninstall(row.id); };
    $("mapp-install").onclick = async () => {
      const button = $("mapp-install");
      button.disabled = true;
      try {
        const body = { id: row.id, start_menu: $("mapp-start").checked, desktop: $("mapp-desktop").checked, hide: $("mapp-hide").checked };
        const data = existing ? await api("/api/pg/module-apps/update", body)
          : await api("/api/pg/module-apps/install", Object.assign(body, { icon_png: preview }));
        state = Object.assign(state, data);
        closeDialog("confirm");
        if (M()) await M().refresh();
        const where = [body.start_menu && shortcuts ? t("в меню «Пуск»", "in the Start menu") : "", body.desktop && shortcuts ? t("на рабочем столе", "on the desktop") : ""].filter(Boolean);
        note(existing ? t("Настройки приложения сохранены", "App settings saved")
          : (where.length ? t(`«${rowTitle(row)}» установлен: ярлык ${where.join(" и ")}`, `“${rowTitle(row)}” installed: shortcut ${where.join(" and ")}`)
            : t(`«${rowTitle(row)}» установлен как приложение`, `“${rowTitle(row)}” installed as an app`)), "ok",
          existing ? null : { label: t("Открыть", "Open"), run: () => popOut(row.id) });
        refreshSettings();
      } catch (error) {
        button.disabled = false;
        note(error.message, "err");
      }
    };
  }

  async function uninstall(id) {
    const row = catalogRow(id) || { id, title: (appOf(id) || {}).title || id };
    const ok = typeof askConfirm === "function"
      ? await askConfirm(t(`Удалить приложение «${rowTitle(row)}»?`, `Remove the “${rowTitle(row)}” app?`),
        t("Ярлыки исчезнут из меню «Пуск» и с рабочего стола. Сам модуль и его данные останутся в MemuAI.",
          "The shortcuts leave the Start menu and the desktop. The module itself and its data stay in MemuAI."),
        t("Удалить приложение", "Remove the app"))
      : window.confirm(rowTitle(row));
    if (!ok) return;
    try {
      state = Object.assign(state, await api("/api/pg/module-apps/uninstall", { id }));
      if (M()) await M().refresh();
      note(t("Приложение удалено, модуль снова в списке MemuAI", "App removed, the module is back in the MemuAI list"), "ok");
      refreshSettings();
    } catch (error) { note(error.message, "err"); }
  }

  /* ------------------------------ separate windows (main window) ------------------------------ */
  async function popOut(id) {
    if (!id) return false;
    const box = M() && M().windowBox ? M().windowBox(id) : null;
    try {
      const data = await api("/api/pg/module-apps/open", { id, width: box ? box.width : 0, height: box ? box.height + 32 : 0 });
      if (!data.native) {
        // A plain browser (dev / tests): a popup window with the same page.
        const w = box ? Math.round(box.width) : 1000, h = box ? Math.round(box.height) : 720;
        window.open(data.url || `/?module_app=${encodeURIComponent(id)}`, `memu-module-${id}`, `popup,width=${w},height=${h}`);
      } else detached.add(id);
      if (M() && M().openIds.includes(id)) await M().dropWindow(id);
      markLaunchers();
      return true;
    } catch (error) {
      note(error.message, "err");
      return false;
    }
  }

  function redirectOpen(id) {
    if (!detached.has(id)) return false;
    api("/api/pg/module-apps/window", { action: "focus", id }).catch(() => { detached.delete(id); });
    return true;
  }

  function menuItems(row, openWinId) {
    if (!row || !row.id) return [];
    const app = appOf(row.id);
    const items = [];
    if (openWinId || row.enabled) {
      items.push({ label: t("Открыть в отдельном окне", "Open in a separate window"), icon: "external", run: () => popOut(row.id) });
    }
    items.push({ label: app ? t("Приложение: ярлыки и список…", "App: shortcuts and list…") : t("Установить как приложение…", "Install as an app…"),
      icon: "monitor", run: () => installDialog(catalogRow(row.id) || row) });
    return items;
  }

  /* ---------------------------- MemuAI closes, modules are open (#6) ---------------------------- */
  let asking = false;
  function openModules() {
    const mods = M();
    if (!mods) return [];
    return mods.openIds.map((id) => Object.assign({ id, title: mods.windowTitle ? mods.windowTitle(id) : id },
      mods.windowState(id) || {}, (mods.windowBox && mods.windowBox(id)) || {}));
  }

  async function closeAllInApp() {
    const mods = M();
    if (!mods) return true;
    for (const id of mods.openIds.slice()) {
      mods.requestClose(id);
      // requestClose may ask (unsaved / generating): wait until that window is gone or the person kept it.
      for (let i = 0; i < 600 && mods.openIds.includes(id); i++) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        if (!document.getElementById("panel-back")?.classList.contains("visible") && mods.openIds.includes(id)) break;
      }
      if (mods.openIds.includes(id)) return false;
    }
    return true;
  }

  function askAppClose() {
    if (asking) return;
    const list = openModules();
    if (!list.length) { api("/api/pg/module-apps/app-close", { action: "close" }).catch(() => {}); return; }
    asking = true;
    const busy = list.filter((row) => row.busy || row.unsaved);
    const names = list.map((row) => `<li>${ic((catalogRow(row.id) || {}).icon || "work")}<b>${esc(row.title)}</b>${row.minimized ? `<em>${esc(t("свёрнут", "minimized"))}</em>` : ""}${row.busy ? `<em class="warn">${esc(t("работает", "working"))}</em>` : ""}${row.unsaved ? `<em class="warn">${esc(t("не сохранено", "unsaved"))}</em>` : ""}</li>`).join("");
    const html = `<div class="mapp-dialog mapp-close">
      <h2>${esc(t("Закрыть MemuAI?", "Close MemuAI?"))}</h2>
      <p class="sub">${esc(list.length > 1 ? t("Сейчас открыты модули — сами они вместе с MemuAI не закроются:", "These modules are open — they do not close together with MemuAI:")
        : t("Сейчас открыт модуль — сам он вместе с MemuAI не закроется:", "A module is open — it does not close together with MemuAI:"))}</p>
      <ul class="mapp-mods">${names}</ul>
      <div class="mapp-choices">
        <button type="button" class="mapp-choice primary" id="mapp-keep">${POP_ICON}<span><b>${esc(list.length > 1 ? t("Оставить модули открытыми", "Keep the modules open") : t("Оставить модуль открытым", "Keep the module open"))}</b>
          <small>${esc(t("Каждый модуль откроется в своём окне, а MemuAI уйдёт в трей.", "Each module moves to its own window, MemuAI goes to the tray."))}</small></span></button>
        <button type="button" class="mapp-choice" id="mapp-close-all">${ic("close")}<span><b>${esc(list.length > 1 ? t("Закрыть модули и MemuAI", "Close the modules and MemuAI") : t("Закрыть модуль и MemuAI", "Close the module and MemuAI"))}</b>
          <small>${esc(t("Как раньше: модули закроются, их данные сохранятся.", "As before: the modules close, their data is kept."))}</small></span></button>
      </div>
      ${busy.length ? `<p class="mapp-note warn">${ic("info")}<span>${esc(t("Модуль, который сейчас работает, откроется в своём окне заново — незавершённый ответ пропадёт. Можно отменить и дождаться конца.", "A module that is working right now reopens in its own window — an unfinished answer is lost. You can cancel and wait for it."))}</span></p>` : ""}
      <label class="mapp-remember"><input type="checkbox" id="mapp-remember"> ${esc(t("Запомнить выбор и больше не спрашивать", "Remember my choice and don't ask again"))}
        <small>${esc(t("Изменить — Настройки → Модули", "Change it in Settings → Modules"))}</small></label>
      <div class="row-end"><button class="pbtn ghost" id="mapp-cancel">${esc(t("Отмена — не закрывать", "Cancel — don't close"))}</button></div>
    </div>`;
    const done = () => { asking = false; };
    if (!dialog(html, "mapp-close-panel", done)) { asking = false; return; }
    const $ = (id) => document.getElementById(id);
    if (!state.native) {
      // No desktop shell: there is no other window to keep the modules in.
      $("mapp-keep").disabled = true;
      $("mapp-keep").title = t("Отдельные окна есть только в приложении MemuAI", "Separate windows exist in the MemuAI desktop app only");
    }
    setTimeout(() => ($("mapp-keep").disabled ? $("mapp-cancel") : $("mapp-keep")).focus(), 30);
    $("mapp-cancel").onclick = () => { closeDialog("cancel"); done(); };
    $("mapp-keep").onclick = async () => {
      const remember = $("mapp-remember").checked;
      closeDialog("confirm"); done();
      try {
        // The server opens the windows, then calls movedOut(ids) here and hides MemuAI.
        await api("/api/pg/module-apps/app-close", { action: "keep", remember, ids: list.map((row) => row.id) });
      } catch (error) { note(error.message, "err"); }
    };
    $("mapp-close-all").onclick = async () => {
      const remember = $("mapp-remember").checked;
      closeDialog("confirm"); done();
      if (!(await closeAllInApp())) return;  // the person kept a module with unsaved work
      api("/api/pg/module-apps/app-close", { action: "close", remember }).catch((error) => note(error.message, "err"));
    };
  }

  function movedOut(ids) {
    (ids || []).forEach((id) => {
      detached.add(id);
      if (M() && M().openIds.includes(id)) M().dropWindow(id);
    });
    markLaunchers();
  }

  /* ------------------------------------ state reports ------------------------------------ */
  let lastReport = "";
  function report() {
    const mods = M();
    if (!mods || !mods.windowState) return;
    let body;
    if (APP) {
      const s = mods.windowState(APP) || {};
      body = { where: "app", id: APP, busy: !!s.busy, unsaved: !!s.unsaved,
        width: Math.round(window.outerWidth || window.innerWidth), height: Math.round(window.outerHeight || window.innerHeight) };
    } else {
      body = { where: "main", modules: openModules().map((row) => ({ id: row.id, busy: !!row.busy, unsaved: !!row.unsaved,
        minimized: !!row.minimized, width: Math.round(row.width || 0), height: Math.round(row.height || 0) })) };
    }
    const sig = JSON.stringify(body);
    if (sig === lastReport) return;
    lastReport = sig;
    api("/api/pg/module-apps/state", body).then((data) => { if (data && data.native) state.native = true; }).catch(() => { lastReport = ""; });
  }
  document.addEventListener("memu-modules-changed", () => setTimeout(report, 0));
  setInterval(report, 1200);

  /* --------------------------------- the module's own window --------------------------------- */
  async function bootModuleApp(open) {
    await loadState();
    const row = catalogRow(APP);
    if (!row || !row.enabled) {
      showGone(row ? t(`Модуль «${rowTitle(row)}» выключен.`, `The “${rowTitle(row)}” module is off.`)
        : t("Этот модуль удалён из MemuAI.", "This module was removed from MemuAI."), !!row);
      return;
    }
    document.title = `${rowTitle(row)} — MemuAI`;
    await open(APP);
    addBarButton();
    report();
  }

  function addBarButton() {
    const win = document.querySelector(`.module-window[data-module="${APP}"]`);
    const tools = win && win.querySelector(".module-window-tools");
    if (!tools || tools.querySelector("[data-mapp-main]")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.mappMain = "1";
    button.className = "mapp-main-btn";
    button.innerHTML = MARK;
    const label = t("Открыть MemuAI", "Open MemuAI");
    button.title = label;
    button.setAttribute("aria-label", label);
    button.onclick = () => showMain();
    tools.insertBefore(button, tools.firstChild);
  }

  function showGone(text, canEnable) {
    const layer = document.getElementById("module-layer");
    if (!layer) return;
    layer.hidden = false;
    let box = document.getElementById("mapp-gone");
    if (!box) {
      box = document.createElement("div");
      box.id = "mapp-gone";
      box.className = "mapp-gone";
      layer.appendChild(box);
    }
    box.innerHTML = `<div class="mapp-gone-card">${ic("info")}<p>${esc(text)}</p>
      <div class="row-end">${canEnable ? `<button class="pbtn" data-mapp-enable>${esc(t("Включить и открыть", "Turn on and open"))}</button>` : ""}
      <button class="pbtn ghost" data-mapp-main>${MARK} ${esc(t("Открыть MemuAI", "Open MemuAI"))}</button></div></div>`;
    box.querySelector("[data-mapp-main]").onclick = () => showMain();
    const enable = box.querySelector("[data-mapp-enable]");
    if (enable) enable.onclick = async () => {
      enable.disabled = true;
      try {
        await fetch("/api/pg/modules/configure", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: APP, enabled: true }) });
        location.reload();
      } catch (error) { enable.disabled = false; note(error.message, "err"); }
    };
  }

  function showMain() {
    api("/api/pg/module-apps/window", { action: "main", id: APP }).then((data) => {
      if (!data.native) window.open("/", "memuai-main");
    }).catch((error) => note(error.message, "err"));
  }

  function windowMenuItems(id, row) {
    const app = appOf(id);
    return [
      { label: t("Вернуть в MemuAI", "Move back into MemuAI"), icon: "back", run: () => {
        api("/api/pg/module-apps/window", { action: "dock", id }).then((data) => {
          if (!data.native) { window.open("/", "memuai-main"); window.close(); }
        }).catch((error) => note(error.message, "err"));
      } },
      { label: t("Открыть MemuAI", "Open MemuAI"), icon: "chat", run: () => showMain() },
      { label: app ? t("Приложение: ярлыки и список…", "App: shortcuts and list…") : t("Установить как приложение…", "Install as an app…"),
        icon: "monitor", run: () => installDialog(row || catalogRow(id)) },
      // Round 38: this app's own title bar (module-appbar.js) — the change applies when the window is reopened.
      { label: (state.appbar || {})[id] !== false && state.custom_appbar !== false
        ? t("Системная рамка окна (после переоткрытия)", "System window frame (after reopening)")
        : t("Свой заголовок окна (после переоткрытия)", "Own window title bar (after reopening)"),
        icon: "layout", run: () => {
          const on = !((state.appbar || {})[id] !== false && state.custom_appbar !== false);
          const body = on && state.custom_appbar === false ? { enabled: true } : { id, enabled: on };
          api("/api/pg/module-apps/appbar", body).then((data) => {
            state = Object.assign(state, data || {});
            note(t("Сохранено — закройте и откройте окно модуля", "Saved — close and reopen the module window"), "ok");
          }).catch((error) => note(error.message, "err"));
        } },
    ];
  }

  function lastClosed(id) {
    api("/api/pg/module-apps/window", { action: "close", id: id || APP, force: true }).then((data) => {
      if (!data.native) {
        window.close();
        // A tab the script did not open cannot close itself: say so instead of a blank page.
        setTimeout(() => showGone(t("Модуль закрыт. Это окно можно закрыть.", "The module is closed. You can close this window."), false), 150);
      }
    }).catch(() => window.close());
  }

  function askWindowClose() {
    // The Windows caption × of a module window that is working / has unsaved changes.
    if (M()) M().requestClose(APP);
  }

  function minimizeNative() {
    api("/api/pg/module-apps/window", { action: "minimize", id: APP }).catch(() => {});
  }

  /* --------------------------------------- settings --------------------------------------- */
  let settingsHost = null;
  function refreshSettings() {
    if (settingsHost && settingsHost.isConnected) renderSettings(settingsHost, true);
  }

  async function renderSettings(host, again) {
    settingsHost = host;
    const pane = host.querySelector(".modules-pane") || host;
    if (!again) await loadState();
    let section = pane.querySelector("#mapp-settings");
    if (!section) {
      section = document.createElement("section");
      section.id = "mapp-settings";
      section.className = "mapp-settings";
      pane.appendChild(section);
    }
    const apps = (state.apps || []).filter((row) => row.installed);
    const choice = state.on_app_close || "ask";
    const option = (value, ru, english, sub_ru, sub_en) => `<label class="mapp-radio ${choice === value ? "on" : ""}">
      <input type="radio" name="mapp-close" value="${value}" ${choice === value ? "checked" : ""}>
      <span><b>${esc(t(ru, english))}</b><small>${esc(t(sub_ru, sub_en))}</small></span></label>`;
    section.innerHTML = `
      <div class="mapp-settings-head">${APP_ICON}<div><b>${esc(t("Модули как приложения", "Modules as apps"))}</b>
        <span>${esc(t("Любой модуль можно открыть в отдельном окне или установить как отдельное приложение со своим ярлыком и кнопкой на панели задач — ⋯ у модуля → «Установить как приложение…».",
          "Any module can open in a separate window or be installed as its own app with a shortcut and a taskbar button — ⋯ on a module → “Install as an app…”."))}</span></div></div>
      <div class="mapp-subhead">${esc(t("Когда вы закрываете MemuAI, а модули открыты", "When you close MemuAI while modules are open"))}</div>
      <div class="mapp-radios" role="radiogroup">
        ${option("ask", "Спрашивать", "Ask me", "Показать вопрос: оставить модули или закрыть их.", "Show a question: keep the modules or close them.")}
        ${option("keep", "Оставлять модули открытыми", "Keep the modules open", "Модули переходят в свои окна, MemuAI уходит в трей.", "The modules move to their own windows, MemuAI goes to the tray.")}
        ${option("close", "Закрывать модули вместе с MemuAI", "Close the modules with MemuAI", "Как раньше. Модуль, который работает или не сохранён, всё равно спросит.", "As before. A module that is working or unsaved still asks.")}
      </div>
      <div class="mapp-subhead">${esc(t("Окно модуля-приложения", "A module app's window"))}</div>
      <label class="mapp-check mapp-appbar-all"><input type="checkbox" data-mapp-appbar="" ${state.custom_appbar !== false ? "checked" : ""}>
        <span><b>${esc(t("Свой заголовок окна", "Own window title bar"))}</b><small>${esc(t("Полоса сверху в цветах модуля вместо системной рамки Windows: перетаскивание, двойной щелчок, Snap. Можно выключить у каждого приложения отдельно. Открытые окна применят изменение после повторного открытия.",
          "A bar in the module's colours instead of the Windows frame: drag, double click, Snap. Can be turned off per app. Open windows pick it up when reopened."))}</small></span></label>
      <div class="mapp-subhead">${esc(t("Установленные приложения", "Installed apps"))}</div>
      ${apps.length ? `<div class="mapp-list">${apps.map((app) => {
        const row = catalogRow(app.id);
        const title = row ? rowTitle(row) : (app.title || app.id);
        const chips = [app.start_menu && app.shortcuts_supported ? t("Пуск", "Start") : "", app.desktop && app.shortcuts_supported ? t("Рабочий стол", "Desktop") : "",
          app.hidden_in_memuai ? t("скрыт в MemuAI", "hidden in MemuAI") : t("есть в MemuAI", "also in MemuAI")].filter(Boolean);
        return `<div class="mapp-row" data-mapp-row="${esc(app.id)}">
          <span class="mapp-row-ico">${row && window.MemuModuleIcon ? window.MemuModuleIcon.html(row) : app.icon ? `<img src="${esc(app.icon)}?v=${Math.round(app.installed || 0)}" alt="">` : ic((row || {}).icon || "work")}</span>
          <div class="mapp-row-text"><b>${esc(title)}</b><span>${chips.map((chip) => `<em>${esc(chip)}</em>`).join("")}</span></div>
          <label class="mapp-appbar-one" title="${esc(t("Свой заголовок окна у этого приложения", "Own window title bar for this app"))}"><input type="checkbox" data-mapp-appbar="${esc(app.id)}" ${(state.appbar || {})[app.id] !== false ? "checked" : ""} ${state.custom_appbar === false ? "disabled" : ""}>
            <span>${esc(t("Свой заголовок", "Own title bar"))}</span></label>
          <button type="button" class="pbtn small ghost" data-mapp-open="${esc(app.id)}">${POP_ICON} ${esc(t("Открыть", "Open"))}</button>
          <button type="button" class="pbtn small ghost" data-mapp-edit="${esc(app.id)}">${ic("settings")} ${esc(t("Настроить", "Settings"))}</button>
          <button type="button" class="pbtn small ghost icon-only danger-text" data-mapp-remove="${esc(app.id)}" title="${esc(t("Удалить приложение", "Remove the app"))}" aria-label="${esc(t("Удалить приложение", "Remove the app"))}">${ic("trash")}</button>
        </div>`;
      }).join("")}</div>` : `<p class="mapp-empty">${esc(t("Пока нет ни одного. Откройте ⋯ у модуля и выберите «Установить как приложение…».", "None yet. Open ⋯ on a module and choose “Install as an app…”."))}</p>`}`;
    section.querySelectorAll('input[name="mapp-close"]').forEach((input) => {
      input.onchange = async () => {
        try {
          state = Object.assign(state, await api("/api/pg/module-apps/settings", { on_app_close: input.value }));
          note(t("Сохранено", "Saved"), "ok");
          refreshSettings();
        } catch (error) { note(error.message, "err"); }
      };
    });
    section.querySelectorAll("[data-mapp-appbar]").forEach((input) => {
      // Round 38: the custom title bar (app/module_appbar.py) — all module apps ("") or one.
      input.onchange = async () => {
        try {
          state = Object.assign(state, await api("/api/pg/module-apps/appbar", { id: input.dataset.mappAppbar, enabled: input.checked }));
          note(t("Сохранено", "Saved"), "ok");
          refreshSettings();
        } catch (error) { note(error.message, "err"); }
      };
    });
    section.querySelectorAll("[data-mapp-open]").forEach((button) => { button.onclick = () => popOut(button.dataset.mappOpen); });
    section.querySelectorAll("[data-mapp-edit]").forEach((button) => {
      button.onclick = () => installDialog(catalogRow(button.dataset.mappEdit) || { id: button.dataset.mappEdit, title: button.dataset.mappEdit });
    });
    section.querySelectorAll("[data-mapp-remove]").forEach((button) => { button.onclick = () => uninstall(button.dataset.mappRemove); });
  }

  /* Round 40: an installed app whose .ico the page draws (a glyph icon, no icon file) is re-checked once per
     start — POST /api/pg/module-apps/icon-sync gives it a new .ico, shortcuts and taskbar icon if the module's
     icon changed. Apps made from an icon file are checked by the server itself (module_apps.repair). */
  let iconSyncTries = 0;
  function syncAppIcons() {
    const rows = (state.apps || []).filter((app) => app.installed && !String(app.icon_sig || "").startsWith("file:"));
    if (!rows.length) return;
    if (!(M() && (M().catalog || []).length)) {
      if (++iconSyncTries < 4) setTimeout(syncAppIcons, 15000);
      return;
    }
    rows.reduce((chain, app) => chain.then(async () => {
      const row = catalogRow(app.id);
      const png = row ? await iconPng(row) : "";
      if (png) await api("/api/pg/module-apps/icon-sync", { id: app.id, icon_png: png }).catch(() => null);
    }), Promise.resolve());
  }

  if (!APP) {
    const start = () => loadState().then(() => setTimeout(syncAppIcons, 8000));
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true }); else start();
  }

  window.MemuModuleApps = {
    appModule: () => APP,
    bootModuleApp, lastClosed, askWindowClose, minimizeNative, windowMenuItems,
    menuItems, installDialog: (id) => installDialog(catalogRow(id) || { id, title: id }), uninstall,
    popOut, redirectOpen, renderSettings,
    askAppClose, movedOut,
    closeInApp: () => { if (M()) M().closeAll(); },
    sync: (ids) => { detached = new Set(ids || []); markLaunchers(); },
    isDetached: (id) => detached.has(id),
    state: () => state,
    reload: loadState,
  };
})();
