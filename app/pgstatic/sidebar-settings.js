
"use strict";
const MemuSidebar = (() => {
  const T = (ru, en) => PG_LANG === "en" ? en : ru;
  const E = (value) => esc(String(value ?? ""));
  const sections = () => [
    ["work", "#nav-work", "folder", "Work", "Work", "Проекты и рабочие чаты", "Projects and work chats"],
    ["code", "#nav-code", "terminal", "MemuCode", "MemuCode", "Разработка в проектах", "Coding in projects"],
    ["schedule", "#nav-sched", "clock", "Планирование", "Planning", "Задачи и автоматизации", "Tasks and automations"],
    ["inbox", "#nav-inbox", "bell", "Входящие", "Inbox", "Уведомления и результаты", "Notifications and results"],
    ["team", "#nav-team", "users", "Команда", "Team", "Совместная работа моделей", "Models working together"],
    ["skills", "#nav-skills", "sparkle", "Скиллы ИИ", "AI skills", "Инструкции для разных задач", "Instructions for different tasks"],
    ["modules", "#module-slot-sidebar", "grid", "Модули", "Modules", "Кнопки приложений в боковой панели", "App shortcuts in the sidebar"],
    ["pinned", "#group-pinned", "pin", "Закреплённые", "Pinned", "Закреплённые чаты", "Pinned chats"],
    ["folders", "#group-folders", "folder", "Папки", "Folders", "Папки текущего режима", "Folders in the current mode"],
    ["memes", "#group-memes", "bot", "Мемесы", "Agents", "Ваши персональные агенты", "Your personal agents"],
  ];
  const visible = (key) => key === "team" ? pref("team_sidebar_visible", true) !== false : pref("sidebar_sections", {})[key] !== false;
  function apply() {
    for (const [key, selector] of sections()) {
      const node = document.querySelector(selector);
      if (!node) continue;
      node.hidden = !visible(key);
      node.dataset.sidebarHidden = String(!visible(key));
    }

    const nav = document.querySelector(".side-nav");
    let button = document.querySelector("#tm-nav-customize");
    if (!button && nav) {
      button = document.createElement("button");
      button.type = "button";
      button.id = "tm-nav-customize";
      button.className = "tm-nav-customize";
      button.onclick = open;
      nav.append(button);
    } else if (button && nav && nav.lastElementChild !== button) {
      nav.append(button);
    }
    if (button) {
      button.hidden = pref("sidebar_customize_visible", true) === false;
      button.innerHTML = `${icon("settings")}<span>${E(T("Настроить панель", "Customize sidebar"))}</span>`;
    }
  }
  const row = (id, iconHtml, title, description, checked, extra = "", badge = "") =>
    `<label class="sb-setting-row" for="${id}">`
    + `<span class="sb-setting-icon" aria-hidden="true">${iconHtml}</span>`
    + `<span class="sb-setting-copy"><span class="sb-setting-title"><strong>${E(title)}</strong>${badge}</span><small>${E(description)}</small></span>`
    + `<span class="sb-switch"><input id="${id}" type="checkbox" ${checked ? "checked" : ""} ${extra}><span aria-hidden="true"></span></span>`
    + `</label>`;
  function syncSectionInputs() {
    document.querySelectorAll("[data-sb-section]").forEach((input) => {
      input.checked = visible(input.dataset.sbSection);
    });
  }
  async function open() {
    panel(
      `<div class="sb-settings">`
      + `<header class="sb-hero"><span class="sb-settings-symbol" aria-hidden="true">${icon("sidebar")}</span>`
      + `<div class="sb-hero-copy"><h2>${T("Ваша боковая панель", "Your sidebar")}</h2>`
      + `<p class="sub">${T("Оставьте под рукой то, чем пользуетесь каждый день. Любой раздел можно вернуть в один клик.", "Keep what you use every day close at hand. Restore any section in one click.")}</p></div></header>`
      + `<section class="sb-card" aria-labelledby="sb-sections-title">`
      + `<div class="sb-setting-heading"><div><h3 id="sb-sections-title">${T("Разделы навигации", "Navigation sections")}</h3>`
      + `<p class="sb-section-sub">${T("Режимы работы, инструменты и группы в списке чатов", "Work modes, tools, and chat list groups")}</p></div>`
      + `<button type="button" class="pbtn ghost" id="sb-show-all">${T("Показать все", "Show all")}</button></div>`
      + `<div class="sb-rows">${sections().map(([key, , ico, ru, en, desc, descEn]) => row(
        key === "team" ? "tm-nav-team-visible" : `sb-visible-${key}`,
        icon(ico), T(ru, en), T(desc, descEn), visible(key), `data-sb-section="${key}"`
      )).join("")}</div></section>`
      + `<section class="sb-card" aria-labelledby="sb-modules-title">`
      + `<div class="sb-setting-heading"><div><h3 id="sb-modules-title">${T("Кнопки модулей", "Module shortcuts")}</h3>`
      + `<p class="sb-setting-help">${T("Переключатели меняют место кнопки и в настройках модулей. Сам модуль остаётся включённым или выключенным как раньше.", "Changes also appear in module settings. Enabling a shortcut does not enable the module itself.")}</p></div>`
      + `<button type="button" class="pbtn ghost" id="sb-module-settings">${icon("settings")}<span>${T("Настройки модулей", "Module settings")}</span></button></div>`
      + `<div id="sb-module-rows" class="sb-rows" aria-live="polite"><p class="sb-setting-empty">${T("Загрузка модулей…", "Loading modules…")}</p></div></section>`
      + `<div class="sb-foot"><p id="sb-save-status" role="status" class="sb-setting-status"></p>`
      + `<footer>${T("Скрытие разделов не удаляет данные. Настройка панели всегда доступна в «Настройки → Внешний вид».", "Hiding a section preserves its data. Customize sidebar is always available in Settings → Appearance.")}</footer></div>`
      + `</div>`,
      "sb-settings-panel"
    );
    document.querySelectorAll("[data-sb-section]").forEach((input) => {
      input.onchange = async () => {
        const key = input.dataset.sbSection, checked = input.checked;
        try {
          const result = await savePreferences(key === "team" ? { team_sidebar_visible: checked } : { sidebar_sections: { ...pref("sidebar_sections", {}), [key]: checked } });
          if (result) saved(); else failure();
        } catch (error) {
          input.checked = !checked;
          failure(error);
        }
      };
    });
    document.querySelector("#sb-show-all").onclick = async () => {
      const values = Object.fromEntries(sections().filter(([key]) => key !== "team").map(([key]) => [key, true]));
      document.querySelectorAll("[data-sb-section]").forEach((input) => { input.checked = true; });
      try {
        const result = await savePreferences({ sidebar_sections: values, team_sidebar_visible: true });
        if (result) saved(); else failure();
      } catch (error) {
        syncSectionInputs();
        failure(error);
      }
    };
    document.querySelector("#sb-module-settings").onclick = () => {
      currentSettingsTab = "modules";
      showSettings(false);
    };
    renderModules();
    await window.MemuModules?.refresh();
    renderModules();
  }
  function saved() {
    const node = document.querySelector("#sb-save-status");
    if (node) { node.textContent = T("Сохранено", "Saved"); node.classList.remove("error"); }
  }
  function failure(error) {
    const node = document.querySelector("#sb-save-status");
    if (node) { node.textContent = error?.message || T("Изменения пока не сохранены. Проверьте подключение и повторите действие.", "Changes have not been saved. Check your connection and try again."); node.classList.add("error"); }
  }
  function renderModules() {
    const host = document.querySelector("#sb-module-rows");
    if (!host) return;
    const focus = document.activeElement?.id;
    const rows = window.MemuModules?.catalog || [];
    host.innerHTML = rows.length ? rows.map((item) => {
      const selected = item.launcher?.slot === "sidebar" && !item.launcher?.hidden;
      const place = ({
        sidebar: T("Боковая панель", "Sidebar"),
        "chat-list": T("Список чатов", "Chat list"),
        topbar: T("Верхняя панель", "Top bar"),
        composer: T("Поле сообщения", "Composer"),
      })[item.launcher?.slot] || "";
      const modIcon = window.MemuModuleIcon?.html ? window.MemuModuleIcon.html(item) : icon("grid");
      const badge = !item.enabled ? `<span class="sb-badge muted">${E(T("Выключен", "Disabled"))}</span>` : "";
      return row(
        `sb-module-${item.id}`,
        modIcon,
        PG_LANG === "en" ? item.title_en || item.title : item.title || item.title_en,
        (!item.enabled ? T("Модуль выключен. ", "Module disabled. ") : "") + (item.launcher?.hidden ? T("Кнопка скрыта", "Shortcut hidden") : place),
        selected,
        `data-sb-module="${E(item.id)}"`,
        badge
      );
    }).join("") : `<p class="sb-setting-empty">${T("Нет установленных модулей. Откройте настройки модулей, чтобы добавить их.", "No installed modules. Add them in module settings.")}</p>`;
    host.querySelectorAll("[data-sb-module]").forEach((input) => {
      input.onchange = async () => {
        input.disabled = true;
        try {
          await MemuModules.configure(input.dataset.sbModule, input.checked ? { slot: "sidebar", hidden: false } : { hidden: true });
          saved();
        } catch (error) {
          input.checked = !input.checked;
          failure(error);
        } finally {
          input.disabled = false;
        }
      };
    });
    if (focus?.startsWith("sb-module-")) document.getElementById(focus)?.focus({ preventScroll: true });
  }
  document.addEventListener("memu-module-catalog-changed", renderModules);
  document.addEventListener("click", event => {
    if (event.target.closest?.("#st-customize-sidebar")) open();
  });
  document.addEventListener("change", async event => {
    if (event.target.id !== "st-sidebar-customize") return;
    const result = await savePreferences({ sidebar_customize_visible: event.target.checked });
    if (!result) toast(T("Настройка ещё не сохранена", "The setting has not been saved"), "err");
  });
  document.addEventListener("memu:panel-restored", () => {
    if (!document.querySelector(".sb-settings")) return;
    syncSectionInputs();
    renderModules();
  });
  document.addEventListener("memu:lang", () => {
    apply();
    if (document.querySelector(".sb-settings")) open();
  });
  apply();
  return { apply, open };
})();
window.MemuSidebar = MemuSidebar;
