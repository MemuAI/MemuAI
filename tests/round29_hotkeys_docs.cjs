#!/usr/bin/env node
/* Generates app/docs/{ru,en}/shortcuts.md from the hotkey registry (app/pgstatic/hotkeys.js).
 *
 *   node tests/round29_hotkeys_docs.cjs           print both pages
 *   node tests/round29_hotkeys_docs.cjs --write   rewrite the pages
 *   node tests/round29_hotkeys_docs.cjs --check   exit 1 when a page is out of date
 *
 * tests/round29_hotkeys_test.py runs --check, so editing the registry without regenerating the
 * documentation fails the test suite.
 */
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const H = require(path.join(ROOT, "app", "pgstatic", "hotkeys.js"));

const TEXT = {
  ru: {
    title: "Горячие клавиши",
    summary: "Все сочетания клавиш MemuAI по разделам, как их поменять и где они работают.",
    intro: [
      "Шпаргалка со всеми сочетаниями — ++Ctrl+/++ или ++F1++ в любом месте MemuAI. Её можно распечатать кнопкой **Печать**.",
      "Глобальное ++Alt+Space++ работает в любой программе и открывает [быстрый доступ](doc:quick-ask); его можно поменять в **Настройки → Быстрый доступ**.",
    ],
    customTitle: "Свои сочетания",
    custom: [
      "**Настройки → Горячие клавиши** показывают все сочетания по разделам, с поиском по действию и по клавише (например «Ctrl+K» или «модель»).",
      "Сочетание в рамке можно переназначить: щёлкните по нему и нажмите новое. ++Esc++ — отмена, ++Backspace++ — отключить сочетание. Кнопка ↺ возвращает сочетание по умолчанию, **Сбросить все** — все сразу.",
      "Если новое сочетание уже занято в том же разделе, строка подсвечивается и вверху появляется предупреждение о конфликте. Сочетания, которые занимает Windows (++Alt+F4++, ++Alt+Tab++, сочетания с ++Win++), и одиночные клавиши без ++Ctrl++ или ++Alt++ назначить нельзя: они мешали бы набору текста.",
      "Глобальные сочетания (быстрый доступ, область экрана, буфер обмена, свои команды и кнопки) меняются там, где они настраиваются, — кнопка **Изменить** ведёт туда.",
    ],
    keys: "Клавиши", action: "Действие", change: "Менять", yes: "да", no: "—",
    added: " *(новое)*",
    more: "Подробнее",
    notSet: "не задано",
    seeAlso: "См. также: [Интерфейс](doc:interface), [Поле ввода](doc:composer).",
  },
  en: {
    title: "Keyboard shortcuts",
    summary: "Every MemuAI key combination by area, how to change them and where they work.",
    intro: [
      "The cheat sheet with every combination is ++Ctrl+/++ or ++F1++ anywhere in MemuAI. Print it with the **Print** button.",
      "The global ++Alt+Space++ works in any program and opens [Quick Ask](doc:quick-ask); change it in **Settings → Quick Ask**.",
    ],
    customTitle: "Your own combinations",
    custom: [
      "**Settings → Keyboard shortcuts** shows every combination by area, searchable by action and by key (for example “Ctrl+K” or “model”).",
      "A framed combination can be rebound: click it and press a new one. ++Esc++ cancels, ++Backspace++ disables the shortcut. The ↺ button restores the default, **Reset all** restores every one.",
      "When the new combination is already used in the same area, the row is highlighted and a conflict warning appears at the top. Combinations Windows takes (++Alt+F4++, ++Alt+Tab++, anything with ++Win++) and single keys without ++Ctrl++ or ++Alt++ cannot be assigned: they would get in the way of typing.",
      "Global hotkeys (Quick Ask, screen region, clipboard, custom commands and buttons) are changed where they are configured — the **Change** button leads there.",
    ],
    keys: "Keys", action: "Action", change: "Change", yes: "yes", no: "—",
    added: " *(new)*",
    more: "More",
    notSet: "not set",
    seeAlso: "See also: [Interface](doc:interface), [Composer](doc:composer).",
  },
};

const LINKS = {
  chat: ["composer", "commands"], code: ["memucode"], modules: ["modules-window"], sketch: ["composer"],
  panels: ["panels"], quick: ["quick-ask"], quick_tools: ["quick-ask"], global: ["quick-ask", "quick-commands"],
  settings: ["interface"],
};
const TITLES = {
  ru: { composer: "Поле ввода", commands: "Команды", memucode: "MemuCode", "modules-window": "Окно модуля", panels: "Панели",
    "quick-ask": "Быстрый доступ", "quick-commands": "Команды быстрого доступа", interface: "Интерфейс" },
  en: { composer: "Composer", commands: "Commands", memucode: "MemuCode", "modules-window": "The module window", panels: "Panels",
    "quick-ask": "Quick Ask", "quick-commands": "Quick Ask commands", interface: "Interface" },
};

function keyMd(combo) {
  const text = String(combo || "");
  if (!text) return "";
  if (text.includes("…")) return text.split("…").map((part) => `++${H.label(part)}++`).join("…");
  return `++${H.label(text)}++`;
}

function rowKeys(row, t) {
  const keys = row.keys.filter(Boolean);
  if (!keys.length) return `*${t.notSet}*`;
  return keys.map(keyMd).join(keys.length > 3 ? ", " : " / ");
}

function page(lang) {
  const t = TEXT[lang];
  const out = ["---", `title: ${t.title}`, `summary: ${t.summary}`, "---", "", `# ${t.title}`, ""];
  t.intro.forEach((line) => out.push(line, ""));
  out.push(`## ${t.customTitle}`, "");
  t.custom.forEach((line) => out.push(line, ""));
  for (const group of H.GROUPS) {
    const rows = H.REGISTRY.filter((row) => row.group === group.id);
    if (!rows.length) continue;
    out.push(`## ${lang === "en" ? group.en : group.ru}`, "", lang === "en" ? group.desc_en : group.desc_ru, "");
    out.push(`| ${t.keys} | ${t.action} | ${t.change} |`, "| --- | --- | --- |");
    for (const row of rows) {
      const action = (lang === "en" ? row.en : row.ru) + (row.added === 29 ? t.added : "");
      const changeable = row.custom ? t.yes : (row.external ? `[${t.yes}](doc:quick-ask)` : t.no);
      out.push(`| ${rowKeys(row, t)} | ${action} | ${changeable} |`);
    }
    out.push("");
    const links = LINKS[group.id];
    if (links) out.push(`${t.more}: ${links.map((id) => `[${TITLES[lang][id]}](doc:${id})`).join(", ")}.`, "");
  }
  out.push(t.seeAlso, "");
  return out.join("\n");
}

function target(lang) { return path.join(ROOT, "app", "docs", lang, "shortcuts.md"); }

if (require.main === module) {
  const mode = process.argv[2] || "";
  let stale = [];
  for (const lang of ["ru", "en"]) {
    const text = page(lang);
    if (mode === "--write") fs.writeFileSync(target(lang), text, "utf8");
    else if (mode === "--check") { if (fs.readFileSync(target(lang), "utf8") !== text) stale.push(lang); }
    else process.stdout.write(text + "\n");
  }
  if (stale.length) {
    console.error("shortcuts.md is out of date for: " + stale.join(", ") + " — run node tests/round29_hotkeys_docs.cjs --write");
    process.exit(1);
  }
}

module.exports = { page };
