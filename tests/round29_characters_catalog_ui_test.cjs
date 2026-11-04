/* jsdom smoke test for the real catalog UI code (app/module_builtins/characters/catalog.js).
 *
 * Runs the REAL renderCatalog()/catalogRun()/catalogOpenDetail()/catalogImport()
 * in a jsdom document with stubbed module globals (S, Memu, t, h, $, icon, ...),
 * drives the view with the REAL captured API answers (same fixtures as
 * tests/round29_characters_catalog_test.py; the Chub fixture is the live
 * api.chub.ai/search answer, verbatim) and asserts what a user would see:
 * the source pills (Chub / Pygmalion.chat / SpicyChat, no Janitor), rendered
 * cards with avatars, the "To chat" button for importable rows, the
 * "Open on the site" button for SpicyChat rows, the detail modal, the
 * SpicyChat hint, the loading skeleton, the error box with retry, the empty
 * state with "Clear filters", and the avatar fallback on import (server
 * avatar missing -> the search result's avatar URL, with a warning toast).
 * Exit code 2 means "jsdom is not installed here" — the pytest wrapper
 * turns that into a skip.
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
      // /tmp is wiped between sessions, so the test must not depend on it.
      JSDOM = require(path.join(__dirname, "..", "..", "jsdom-test", "node_modules", "jsdom")).JSDOM;
    } catch (e3) {
      console.log("SKIP: jsdom not available");
      process.exit(2);
    }
  }
}

const assert = require("assert/strict");
const ROOT = path.resolve(__dirname, "..");

const PYG = {
  totalItems: "55",
  characters: [
    { id: "8e6fb05b-52e9-4688-a8cd-e4bca5baf351", displayName: "A DDLC Scenario",
      avatarUrl: "https://assets.pygmalion.chat/436cccb4-bac2-43d4-a104-ffdca34ab187",
      description: "DDLC scenario.", source: "A game by: Team Salvato",
      stars: 4, views: 419, downloads: 5, isPublic: true,
      tags: ["Female", "Scenario", "Video Game", "Human"],
      createdAt: "1765816251", updatedAt: "1765816507",
      owner: { id: "6e07e2c1-eec1-48db-b1fa-1a2b139c50bb", displayName: "Hori", username: "lilmonix3" },
      chatCount: 28, versionId: "ceb259bb-f1de-49b7-919e-0b6f2ee8276b", personalityTokenCount: 0,
      approvedAt: "1766399240" },
    { id: "5f9e33ad-2bf6-44bb-934f-c763c8c86fa3", displayName: "Mikasa",
      avatarUrl: "https://assets.pygmalion.chat/cb169184-30f5-4347-b251-0ddef8ae333d",
      description: "Mikasa from Attack on Titan.", source: "Attack on Titan",
      stars: 3, views: 40334, downloads: 1313, isPublic: true, tags: ["Female", "Anime"],
      createdAt: "1706427614", updatedAt: "1722520534",
      owner: { id: "e6681af2-8ae0-4662-9106-5984f2615638", displayName: "Makima", username: "makima" },
      chatCount: 41, versionId: "3db5657b-ce2b-4b6e-8b12-12b8590bb89f", personalityTokenCount: 1106,
      approvedAt: "1706431260" }
  ]
};

const SPICY = {
  found: 8506,
  hits: [
    { document: { character_id: "6be99c6f-a6ce-4626-a777-0538ff7770c5",
      avatar_url: "avatars/c18b811639bc006f7c23e15cd46884d2.gif",
      createdAt: 1715147475344, creator_username: "ruske",
      greeting: "*I blick slowly* \"do you wanna know your fate today?\"",
      is_nsfw: true, name: "Cat", num_messages: 4733, num_messages_24h: 0,
      tags: ["Anal", "NSFW", "Male"], title: "Cat", token_count: 962,
      type: "STANDARD", updatedAt: 1793719471986, visibility: "public" } },
    { document: { character_id: "bfbd197b-4194-41d6-b9b2-d398df6fd011",
      avatar_url: "avatars/a09de00bea6f348b9528ee19fc2783a1.png",
      createdAt: 1729745313897, creator_username: "longlivetbc",
      greeting: "*you were walking* -Meow-",
      is_nsfw: false, name: "Cat", num_messages: 3691, num_messages_24h: 0,
      tags: ["Game", "Comedy"], title: "First cat of all The Battle Cats", token_count: 149,
      type: "STANDARD", updatedAt: 1793543854094, visibility: "public" } }
  ],
  page: 1, out_of: 1459921
};

/* Live api.chub.ai/search answer (captured 2026-11-04, verbatim): the token
   count lives in nTokens, tokenCount is null, avatars are webp. */
const CHUB = {
  data: { count: 2620, nodes: [
    { id: 7900350, name: "Testa", fullPath: "clear_appointment_1805/testa-a6ad0525d3f2",
      tagline: " ", description: " ", topics: [],
      avatar_url: "https://avatars.charhub.io/avatars/clear_appointment_1805/testa-a6ad0525d3f2/avatar.webp",
      max_res_url: "https://avatars.charhub.io/avatars/clear_appointment_1805/testa-a6ad0525d3f2/chara_card_v2.png",
      tokenCount: null, tokenCountLabel: null, nTokens: 1978, nChats: 1, starCount: 0, rating: 0,
      creator: { username: "clear_appointment_1805" }, createdAt: "2026-09-30T04:48:11Z",
      lastActivityAt: "2026-09-30T04:48:11Z", nsfw_image: false },
    { id: 7655751, name: "test", fullPath: "Jones1/test-fac24dc48794",
      tagline: " ", description: " ", topics: [],
      avatar_url: "https://avatars.charhub.io/avatars/Jones1/test-fac24dc48794/avatar.webp",
      max_res_url: "https://avatars.charhub.io/avatars/Jones1/test-fac24dc48794/chara_card_v2.png",
      tokenCount: null, tokenCountLabel: null, nTokens: 398, nChats: 0, starCount: 0, rating: 0,
      creator: { username: "Jones1" }, createdAt: "2026-08-22T20:08:14Z",
      lastActivityAt: "2026-08-22T20:08:14Z", nsfw_image: false }
  ], page: 1 }
};

const SPICY_CONFIG = {
  typesenseConfig: {
    collectionNamePublicCharacter: "public_characters_alias",
    apiKeyPublicCharacter: "TESTKEY"
  }
};

const STRINGS = {
  catalog_title: "Каталог ботов", catalog_search_ph: "Имя, тема, персонаж…",
  catalog_source: "Источник каталога", catalog_unavailable: "Этот источник пока не даёт каталог.",
  catalog_sort: "Сортировка", catalog_sort_default: "Сначала популярные",
  catalog_sort_active: "Сначала активные", catalog_sort_new: "Сначала новые",
  catalog_sort_tokens: "Сначала с большим описанием", catalog_sort_chats: "Сначала с большим числом чатов",
  catalog_nsfw: "Показывать 18+", catalog_hide_low: "Hide Low Quality Bots",
  catalog_hide_low_hint: "Скрывает ботов, у которых в описании меньше 500 токенов.",
  catalog_bad_answer: "Каталог вернул непонятный ответ.",
  catalog_err_403: "Chub 403.", catalog_err_403_other: "Каталог ответил 403.",
  catalog_err_404: "Chub 404.", catalog_err_http: "Каталог ответил ошибкой: {message}",
  catalog_err_timeout: "Таймаут.", catalog_err_other: "Не удалось спросить каталог: {message}",
  catalog_err_private_dns: "Частная сеть.",
  catalog_found: "Показано {count} из {total}", catalog_no_results: "Ничего не нашлось",
  catalog_no_results_hint: "Попробуйте другой запрос.",
  catalog_loading: "Загрузка…", catalog_load_more: "Загрузить ещё",
  catalog_by: "от {name}", catalog_tokens_short: "токенов",
  catalog_tokens_unknown: "токены неизвестны", catalog_import_chat: "В чат",
  catalog_importing: "Скачиваю карточку «{name}»…", catalog_imported: "«{name}» импортирован — чат открыт.",
  catalog_not_character: "По этой ссылке скачалось не карточка персонажа.",
  catalog_no_page: "У этой карточки нет ссылки на страницу.",
  catalog_no_description: "Описания нет — подробности появятся после импорта.",
  catalog_source_line: "Источник: {source} · {page}",
  catalog_open_site: "Открыть на сайте",
  catalog_spicy_hint: "Поиск работает, но импорт в один клик — нет.",
  catalog_spicy_noimport: "Импорт в один клик недоступен.",
  catalog_greeting: "Первое сообщение",
  catalog_error_title: "Каталог не отвечает",
  catalog_clear_filters: "Сбросить фильтры",
  catalog_rating: "рейтинг {value}",
  catalog_chats: "{count} чатов",
  catalog_avatar_missing: "Аватар не скачался ({reason}) — персонаж создан без аватара.",
  catalog_avatar_linked: "Аватар не скачался, показана картинка с сайта-источника.",
  catalog_avatar_reason_missing: "картинки нет у источника",
  catalog_avatar_reason_blocked: "сайт не пустил загрузку",
  catalog_avatar_reason_gone: "картинка не найдена на сайте",
  catalog_avatar_reason_network: "нет связи с сайтом",
  catalog_avatar_reason_bad: "сайт отдал не картинку",
  catalog_avatar_reason_big: "картинка слишком большая",
  catalog_avatar_reason_http: "сайт ответил ошибкой",
  import: "Импорт", search: "Найти", all: "Все", retry: "Повторить", cancel: "Отмена"
};

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0)); };

async function main() {
  const dom = new JSDOM('<!doctype html><html><body><main id="main"></main></body></html>',
    { url: "http://127.0.0.1:8132/", runScripts: "outside-only" });
  const w = dom.window;
  const document = w.document;

  const calls = { open: [], fetchImport: [], toasts: [], openedCharacter: null, home: 0, importFlow: 0,
    createFromCardAvatar: null, loadImageUrls: [], failFetchOnce: false };

  // --- stubs for the module globals the catalog part uses ---
  w.S = { view: "catalog", settings: { catalog: {} } };
  w.Memu = {
    fetch: function (opts) {
      if (calls.failFetchOnce) {
        calls.failFetchOnce = false;
        return Promise.reject(new Error("HTTP 403 Forbidden"));
      }
      if (opts.url.indexOf("prod.nd-api.com/v2/applications/spicychat") >= 0) {
        return Promise.resolve({ text: JSON.stringify(SPICY_CONFIG) });
      }
      if (opts.url.indexOf("server.pygmalion.chat") >= 0) {
        const msg = JSON.parse(decodeURIComponent(opts.url.split("message=")[1]));
        if (msg.query === "zzz-nothing") {
          return Promise.resolve({ text: JSON.stringify({ totalItems: "0", characters: [] }) });
        }
        return Promise.resolve({ text: JSON.stringify(PYG) });
      }
      if (opts.url.indexOf("ts-lb.nd-api.com") >= 0) {
        return Promise.resolve({ text: JSON.stringify(SPICY) });
      }
      if (opts.url.indexOf("gateway.chub.ai") >= 0) {
        return Promise.resolve({ text: JSON.stringify(CHUB) });
      }
      return Promise.reject(new Error("unexpected url " + opts.url));
    },
    fetchImport: function (url) {
      calls.fetchImport.push(url);
      // the server could not download the avatar: no res.avatar here
      return Promise.resolve({ file: { base64: "", name: "card.png" }, source: "pygmalion", page: url });
    },
    open: function (body) {
      calls.open.push(body.url);
      return Promise.resolve({ ok: true });
    }
  };
  w.t = function (key, params) {
    let s = STRINGS[key] !== undefined ? STRINGS[key] : key;
    if (params) {
      for (const k of Object.keys(params)) s = s.split("{" + k + "}").join(String(params[k]));
    }
    return s;
  };
  w.esc = function (s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  };
  w.fmtNum = function (n) { return String(n); };
  w.icon = function (name) { return '<svg data-icon="' + name + '"></svg>'; };
  w.iconBtn = function (name, label, onclick) {
    const b = document.createElement("button");
    b.className = "mu-icon-btn";
    b.addEventListener("click", onclick);
    return b;
  };
  w.h = function (tag, props, children) {
    const el = document.createElement(tag);
    if (props) {
      for (const k of Object.keys(props)) {
        const v = props[k];
        if (v === undefined || v === null) continue;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k === "html") el.innerHTML = v;
        else if (k === "onclick") el.addEventListener("click", v);
        else el.setAttribute(k, String(v));
      }
    }
    if (children !== undefined && children !== null) {
      const list = Array.isArray(children) ? children : [children];
      for (const child of list) {
        if (child === null || child === undefined) continue;
        if (typeof child === "string") el.appendChild(document.createTextNode(child));
        else el.appendChild(child);
      }
    }
    return el;
  };
  w.$ = function (sel) { return document.querySelector(sel); };
  w.syncNarrowSidebar = function () {};
  w.goHome = function () { calls.home++; };
  w.importFlow = function () { calls.importFlow++; };
  w.saveSettings = function () {};
  w.defaultAvatar = function (name) { return "avatar:" + name; };
  w.toast = function (msg, kind) { calls.toasts.push({ msg: String(msg), kind: kind || "" }); };
  w.errText = function (e) { return String((e && e.message) || e || ""); };
  w.E = { base64ToBytes: function () { return new Uint8Array(0); } };
  w.loadImage = function (src) {
    calls.loadImageUrls.push(src);
    return Promise.resolve({});
  };
  w.openModal = function (opts) {
    const box = document.createElement("div");
    box.id = "modal";
    const body = document.createElement("div");
    box.appendChild(body);
    opts.content(body);
    const btns = document.createElement("div");
    (opts.buttons || []).forEach(function (b) {
      const btn = document.createElement("button");
      btn.textContent = b.label;
      btn.dataset.kind = b.kind || "";
      btn.addEventListener("click", function () { if (b.onClick) b.onClick(); });
      btns.appendChild(btn);
    });
    box.appendChild(btns);
    document.body.appendChild(box);
    return { close: function () { box.remove(); } };
  };
  w.analyzeBytes = function () {
    return Promise.resolve({ kind: "character", json: { name: "Imported" }, avatar: "", data: {} });
  };
  w.createFromCard = function (json, avatar) {
    calls.createFromCardAvatar = avatar;
    return Promise.resolve({ id: "char-1", data: { name: "Imported" }, avatar: avatar || "" });
  };
  w.openCharacter = function (id) {
    calls.openedCharacter = id;
    return Promise.resolve();
  };

  w.eval(fs.readFileSync(path.join(ROOT, "app/module_builtins/characters/catalog.js"), "utf8"));

  let tests = 0;
  const test = async (name, run) => { await run(); tests++; console.log("PASS " + name); };

  await test("source pills list three working sources, no Janitor", async () => {
    w.renderCatalog();
    const pills = Array.from(document.querySelectorAll("button.ch-cat-src"));
    assert.deepEqual(pills.map((b) => b.textContent), ["Chub", "Pygmalion.chat", "SpicyChat"]);
    assert.ok(!document.body.textContent.includes("Janitor"), "no Janitor text in the view");
    assert.ok(!document.querySelector("select.ch-cat-source"), "no source select anymore");
    assert.ok(document.querySelector("input.ch-cat-search"), "search input exists");
    const sort = document.querySelector("select.ch-cat-sort");
    assert.ok(sort, "sort select exists");
    assert.deepEqual(Array.from(sort.options).map((o) => o.value),
      ["default", "active", "new", "tokens", "chats"]);
    assert.ok(document.body.textContent.includes("Hide Low Quality Bots"), "hide-low toggle exists");
  });

  await test("loading shows skeleton cards, then Pygmalion rows with avatars", async () => {
    w.S.settings.catalog.source = "pygmalion";
    w.CAT.loaded = false;
    w.catalogRun(true);
    assert.equal(document.querySelectorAll(".ch-cat-skel").length, 6, "six skeleton cards while loading");
    await flush();
    const grid = document.querySelector("#catalog-grid");
    assert.ok(grid, "grid exists");
    assert.equal(grid.children.length, 2);
    assert.equal(document.querySelectorAll(".ch-cat-skel").length, 0, "skeletons gone after load");
    assert.ok(grid.textContent.includes("A DDLC Scenario"));
    assert.ok(grid.textContent.includes("Mikasa"));
    const imgs = Array.from(grid.querySelectorAll(".ch-cat-card img"));
    assert.equal(imgs.length, 2);
    assert.equal(imgs[0].getAttribute("src"), "https://assets.pygmalion.chat/436cccb4-bac2-43d4-a104-ffdca34ab187");
    assert.equal(imgs[1].getAttribute("src"), "https://assets.pygmalion.chat/cb169184-30f5-4347-b251-0ddef8ae333d");
    assert.ok(grid.textContent.includes("1106 токенов"), "real token count shown, got: " + grid.textContent);
    const btns = Array.from(grid.querySelectorAll("button.ch-cat-imp")).map((b) => b.textContent);
    assert.deepEqual(btns, ["В чат", "В чат"]);
    const status = document.querySelector(".ch-cat-status").textContent;
    assert.ok(status.includes("2"), "status shows a count, got: " + status);
    assert.ok(status.includes("55"), "status shows the total, got: " + status);
  });

  await test("Pygmalion one-click import falls back to the search avatar URL", async () => {
    const grid = document.querySelector("#catalog-grid");
    const btn = grid.querySelector("button.ch-cat-imp");
    calls.toasts = [];
    btn.click();
    await flush();
    assert.equal(calls.fetchImport.length, 1);
    assert.ok(calls.fetchImport[0].indexOf("https://pygmalion.chat/character/") === 0, calls.fetchImport[0]);
    assert.deepEqual(calls.loadImageUrls,
      ["https://assets.pygmalion.chat/436cccb4-bac2-43d4-a104-ffdca34ab187"],
      "the search avatar URL is checked before being stored");
    assert.equal(calls.createFromCardAvatar,
      "https://assets.pygmalion.chat/436cccb4-bac2-43d4-a104-ffdca34ab187",
      "the character is created WITH the avatar, linked to the source site");
    assert.ok(calls.toasts.some((t) => t.msg.includes("показана картинка с сайта-источника")),
      "the linked avatar is reported, not hidden: " + JSON.stringify(calls.toasts));
    assert.equal(calls.openedCharacter, "char-1");
  });

  await test("import without any avatar reports it instead of staying silent", async () => {
    calls.toasts = [];
    calls.createFromCardAvatar = "unset";
    await w.catalogImport({ name: "Ghost", pageUrl: "https://pygmalion.chat/character/00000000-0000-0000-0000-000000000000",
      avatarUrl: "", importable: true, source: "pygmalion" });
    await flush();
    assert.equal(calls.createFromCardAvatar, "", "no avatar anywhere -> empty, not a broken link");
    assert.ok(calls.toasts.some((t) => t.msg.includes("персонаж создан без аватара")),
      "the missing avatar is reported: " + JSON.stringify(calls.toasts));
  });

  await test("SpicyChat search renders rows with Open-on-site buttons and 18+ badge", async () => {
    w.S.settings.catalog.source = "spicychat";
    w.CAT.loaded = false;
    w.catalogRun(true);
    await flush();
    const hint = Array.from(document.querySelectorAll(".ch-cat-hint")).map((e) => e.textContent).join(" ");
    assert.ok(hint.includes("импорт в один клик — нет"), "spicy hint shown, got: " + hint);
    const grid = document.querySelector("#catalog-grid");
    assert.equal(grid.children.length, 2);
    assert.ok(grid.textContent.includes("First cat of all The Battle Cats"));
    const btns = Array.from(grid.querySelectorAll("button.ch-cat-imp")).map((b) => b.textContent);
    assert.deepEqual(btns, ["Открыть на сайте", "Открыть на сайте"]);
    assert.equal(grid.querySelectorAll(".ch-cat-nsfw").length, 1, "the NSFW row carries an 18+ badge");
    const status = document.querySelector(".ch-cat-status").textContent;
    assert.ok(status.includes("8506"), "status shows the SpicyChat total, got: " + status);
  });

  await test("SpicyChat detail modal shows greeting and opens the site", async () => {
    const grid = document.querySelector("#catalog-grid");
    const card = grid.querySelector(".ch-cat-card");
    card.click();
    await flush();
    const modal = document.querySelector("#modal");
    assert.ok(modal, "modal opened");
    assert.ok(modal.textContent.includes("Первое сообщение"), "greeting shown");
    assert.ok(modal.textContent.includes("Импорт в один клик недоступен"), "no-import note shown");
    assert.ok(modal.textContent.includes("https://spicychat.ai/chatbot/6be99c6f-a6ce-4626-a777-0538ff7770c5"), "page url shown");
    const primary = Array.from(modal.querySelectorAll("button")).find((b) => b.textContent === "Открыть на сайте");
    assert.ok(primary, "primary button opens the site");
    primary.click();
    await flush();
    assert.deepEqual(calls.open, ["https://spicychat.ai/chatbot/6be99c6f-a6ce-4626-a777-0538ff7770c5"]);
  });

  await test("error box with retry, then a successful reload", async () => {
    document.querySelector("#modal").remove();
    w.S.settings.catalog.source = "pygmalion";
    calls.failFetchOnce = true;
    w.CAT.loaded = false;
    w.catalogRun(true);
    await flush();
    const box = document.querySelector(".ch-cat-errorbox");
    assert.ok(box, "error box shown");
    assert.ok(box.textContent.includes("Каталог не отвечает"), "error title shown");
    assert.ok(box.textContent.includes("Chub 403.") || box.textContent.includes("Каталог ответил 403"),
      "readable error text, got: " + box.textContent);
    const retry = Array.from(box.querySelectorAll("button")).find((b) => b.textContent === "Повторить");
    assert.ok(retry, "retry button exists");
    retry.click();
    await flush();
    assert.ok(!document.querySelector(".ch-cat-errorbox"), "error box gone after retry");
    assert.equal(document.querySelectorAll("#catalog-grid .ch-cat-card").length, 2, "rows render after retry");
  });

  await test("empty state offers to clear the filters", async () => {
    w.S.settings.catalog.source = "pygmalion";
    w.CAT.query = "zzz-nothing";
    w.CAT.loaded = false;
    w.catalogRun(true);
    await flush();
    const empty = document.querySelector(".ch-cat-empty");
    assert.ok(empty, "empty state shown");
    assert.ok(empty.textContent.includes("Ничего не нашлось"));
    const clear = Array.from(empty.querySelectorAll("button")).find((b) => b.textContent === "Сбросить фильтры");
    assert.ok(clear, "clear-filters button exists");
    clear.click();
    await flush();
    assert.equal(w.CAT.query, "", "query cleared");
    assert.equal(document.querySelectorAll("#catalog-grid .ch-cat-card").length, 2, "rows render after clearing");
  });

  await test("Chub live rows: webp avatars, real nTokens, Hide Low hides the thin row", async () => {
    w.S.settings.catalog.source = "chub";
    w.S.settings.catalog.hideLow = true;
    w.CAT.loaded = false;
    w.catalogRun(true);
    await flush();
    const grid = document.querySelector("#catalog-grid");
    assert.equal(grid.children.length, 1, "only the 1978-token row survives Hide Low");
    assert.ok(grid.textContent.includes("Testa"));
    assert.ok(!grid.textContent.includes("Jones1"), "the 398-token row is hidden");
    const img = grid.querySelector(".ch-cat-card img");
    assert.equal(img.getAttribute("src"),
      "https://avatars.charhub.io/avatars/clear_appointment_1805/testa-a6ad0525d3f2/avatar.webp");
    assert.ok(grid.textContent.includes("1978 токенов"), "tokens come from nTokens, got: " + grid.textContent);
    const btn = grid.querySelector("button.ch-cat-imp");
    assert.equal(btn.textContent, "В чат");
  });

  console.log("catalog UI smoke OK (" + tests + " checks)");
}

main().catch((e) => { console.error(e); process.exit(1); });
