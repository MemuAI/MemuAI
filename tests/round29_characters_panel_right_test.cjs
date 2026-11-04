/* jsdom smoke test for the optional right character card panel.
 *
 * Runs the REAL renderChatSidebar() from app/module_builtins/characters/
 * main.js in a jsdom document built from the real index.html, with stubbed
 * module globals (Memu, MemuTokens, ...), and asserts what a user would
 * see: the independent panel opens on the right (real style.css),
 * the redesigned card (avatar + name/creator/tokens/tags + description),
 * the visible "+ New chat" button, the chat list with the active chat
 * marked, the character switcher, and that clicking "+ New chat" and a
 * chat row actually work. Exit code 2 means "jsdom is not installed
 * here" — the pytest wrapper turns that into a skip.
 */
"use strict";
const fs = require("fs");
const path = require("path");

let JSDOM;
try {
  JSDOM = require("jsdom").JSDOM;
} catch (e) {
  try {
    JSDOM = require("/tmp/jsdom-test/node_modules/jsdom").JSDOM;
  } catch (e2) {
    try {
      // Durable copy next to the checkout (~/workspace/memuai/jsdom-test):
      // /tmp is cleaned between sessions, so this is the fallback that lasts.
      JSDOM = require(path.join(__dirname, "..", "..", "jsdom-test", "node_modules", "jsdom")).JSDOM;
    } catch (e3) {
      console.log("SKIP: jsdom not available");
      process.exit(2);
    }
  }
}

const assert = require("assert/strict");
const ROOT = path.resolve(__dirname, "..");
const MODULE = path.join(ROOT, "app", "module_builtins", "characters");

const STRINGS = {
  home: "Главная", catalog: "Каталог ботов", import: "Импорт",
  new_character: "Новый персонаж", edit_character_full: "Редактировать персонажа",
  unnamed: "Без имени", catalog_by: "от {name}", catalog_tokens_short: "токенов",
  new_chat: "Новый чат", side_chats_with: "Чаты с {name}", side_all_chats: "Все чаты",
  side_no_chats: "Чатов с этим персонажем пока нет — начните новый.",
  chat_meta: "{count} сообщений · {date}", untitled_chat: "Без названия",
  side_switch_char: "Сменить персонажа", side_all_characters: "Все персонажи",
  default_user_name: "Пользователь", personas: "Персоны", lorebooks: "Лорбуки",
  presets: "Пресеты", settings: "Настройки", search_chars: "Искать персонажей",
  character_panel: "Карточка персонажа", edit: "Изменить", chats: "Чаты",
  search_short: "Поиск", all: "Все", favorites: "Избранное", title: "Персонажи",
  loading: "Загрузка…", char_missing: "Персонаж не найден", busy_wait: "Подождите…"
};

const flush = async (n) => { for (let i = 0; i < (n || 8); i++) await new Promise((r) => setTimeout(r, 0)); };

async function main() {
  const html = fs.readFileSync(path.join(MODULE, "index.html"), "utf8");
  const css = fs.readFileSync(path.join(MODULE, "style.css"), "utf8");
  const dom = new JSDOM("<!doctype html><html><head></head><body>" + html + "</body></html>",
    { url: "http://127.0.0.1:8132/", runScripts: "outside-only" });
  const w = dom.window;
  const document = w.document;

  // The real stylesheet in the document, as in the module.
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const store = new Map();
  const calls = { toasts: [], ready: 0 };
  const chat1 = { id: "chat-1", charId: "c1", title: "Первый чат", created: 1, updated: Date.now() - 86400000,
    messages: [], meta: { variables: {}, timedWorldInfo: {}, note: { text: "", position: "chat", depth: 4, role: 0, interval: 1 } } };
  const chat2 = { id: "chat-2", charId: "c1", title: "Разговор", count: 12, created: 2, updated: Date.now(),
    messages: [], meta: { variables: {}, timedWorldInfo: {}, note: { text: "", position: "chat", depth: 4, role: 0, interval: 1 } } };
  store.set("chats/c1/chat-1", chat1);
  store.set("chats/c1/chat-2", chat2);

  w.MemuTokens = { estimate: function (s) { return Math.max(1, Math.ceil(String(s || "").length / 4)); } };
  w.Memu = {
    lang: "ru",
    t: function (key, params) {
      let s = STRINGS[key] !== undefined ? STRINGS[key] : key;
      if (params) {
        for (const k of Object.keys(params)) s = s.split("{" + k + "}").join(String(params[k]));
      }
      return s;
    },
    i18n: {
      number: function (n) { return String(n); },
      date: function (d) { return "3 ноя, 12:00"; }
    },
    store: {
      get: function (key, fallback) { return Promise.resolve(store.has(key) ? store.get(key) : (fallback === undefined ? null : fallback)); },
      set: function (key, value) { store.set(key, value); return Promise.resolve(); },
      remove: function (key) { store.delete(key); return Promise.resolve(); },
      list: function () { return Promise.resolve([]); }
    },
    asset: function () { return Promise.resolve({ presets: [] }); },
    models: function () { return Promise.resolve([]); },
    onLanguage: function () {},
    onTheme: function () {},
    on: function () {},
    ready: function () { calls.ready++; },
    toast: function (msg, kind) { calls.toasts.push({ msg: String(msg), kind: kind || "" }); }
  };
  // renderChatSidebar uses Memu.t through t(), toasts through toast(); the
  // module defines its own toast() helper on top of Memu.toast — the real
  // one is defined in main.js itself, so no stub is needed for it here.
  // Assemble the module exactly like the host does (modules_host.
  // load_package): the /* @memu-part:*.js */ markers are replaced with
  // the part files, and the tokens marker with the shared estimator.
  let built = fs.readFileSync(path.join(MODULE, "main.js"), "utf8");
  built = built.replace(/\/\* @memu-part:([A-Za-z0-9_.-]+) \*\//g, function (m, name) {
    return "/* @memu-inlined-part:" + name + " */\n" + fs.readFileSync(path.join(MODULE, name), "utf8");
  });
  const tokensLib = fs.readFileSync(path.join(ROOT, "app", "pgstatic", "tokens.js"), "utf8");
  built = built.replace("/* @memu-shared:tokens */",
    "/* @memu-inlined:tokens */\nvar MemuTokens = (function () {\n" + tokensLib + "\nreturn MemuTokens;\n})();");
  w.eval(built);
  await flush(10);

  const S = w.MemuCharacters.S;
  assert.ok(S, "MemuCharacters.S is exported");
  S.view = "chat";
  S.index = [
    { id: "c1", name: "Микаса", tags: ["Anime", "Female"], creator: "makima" },
    { id: "c2", name: "Аква", tags: ["Comedy"], creator: "ddd" }
  ];
  S.char = { id: "c1", fav: true, avatar: "",
    data: { name: "Микаса", creator: "makima", tags: ["Anime", "Female", "Drama"],
      description: "Микаса из «Атаки титанов». Спокойная, преданная, всегда рядом.",
      personality: "Тихая и решительная.", scenario: "Тренировочный лагерь.",
      first_mes: "Привет.", mes_example: "", system_prompt: "" } };
  S.chat = chat2;
  S.chatList = [chat1, chat2];
  S.personas = [{ id: "p1", name: "Ниса", description: "", avatar: "", position: 0, depth: 2, role: 0 }];
  S.settings.personaId = "p1";

  const side = document.querySelector("#character-panel");
  w.MemuCharacters.render();
  assert.ok(side?.hidden, "character card starts hidden");
  document.querySelector('.ch-chat-head button[title="Карточка персонажа"]').click();
  assert.ok(!side.hidden, "toolbar button opens the card");

  let tests = 0;
  const test = async (name, run) => { await run(); tests++; console.log("PASS " + name); };

  await test("library and character card use separate grid columns", async () => {
    assert.match(css, /\.ch-side\{[^}]*grid-column:1;/);
    assert.ok(css.includes(".ch-character-panel{grid-column:3;"));
    assert.ok(document.querySelector("#library-resize"));
    assert.ok(document.querySelector("#character-resize"));
    assert.ok(!side.querySelector("input[type=range], .ch-side-switch, .ch-side-chats"));
  });

  await test("identity, description, and labeled editing actions", async () => {
    assert.equal(side.querySelector(".ch-side-name").textContent, "Микаса");
    assert.ok(side.querySelector(".ch-side-creator").textContent.includes("makima"));
    assert.ok(side.querySelector(".ch-side-meta").textContent.includes("токенов"));
    assert.ok(side.querySelector(".ch-inspector-section").textContent.includes("Атаки титанов"));
    assert.ok(side.querySelector(".ch-inspector-actions button[title='Изменить']"));
  });

  await test("chat menu lists the current character's chats and switches", async () => {
    side.querySelector("button[title='Чаты']").click();
    const rows = Array.from(document.querySelectorAll(".ch-chat-menu [role=menuitem]"));
    assert.equal(rows.length, 3);
    assert.ok(rows[0].textContent.includes("Разговор"));
    assert.ok(rows[0].classList.contains("active"));
    assert.ok(rows[1].textContent.includes("Первый чат"));
    rows[1].click();
    await flush(10);
    assert.equal(S.chat.id, "chat-1");
  });

  await test("new chat is a separate action", async () => {
    const before = S.chatList.length;
    side.querySelector("button[title='Новый чат']").click();
    await flush(10);
    assert.equal(S.chatList.length, before + 1);
    assert.ok(S.chat.title.startsWith("Микаса"));
  });


  console.log("characters panel-right smoke OK (" + tests + " checks)");
}

main().catch((e) => { console.error(e); process.exit(1); });
