/* MemuAI module «Characters» (SillyTavern-style roleplay). main.js is the source of truth.
   Sections: engine (pure logic, tested in Node) -> core UI -> chat -> views -> editors -> panels -> boot. */
"use strict";
/* @memu-shared:tokens */
/* @engine-start — pure logic, no DOM: macros, World Info, prompt builder (SillyTavern Chat Completion
   compatible), cards (PNG chara/ccv3, JSON), lorebook and chat formats, token estimate.
   tests/round29_characters_engine_test.py runs this block in Node. */
var CharEngine = (function () {
  "use strict";

  /* ------------------------------------------------------------ helpers */
  function str(value) { return value === undefined || value === null ? "" : String(value); }
  function num(value, fallback) { var n = Number(value); return Number.isFinite(n) ? n : fallback; }
  function clone(value) { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)); }
  function escapeRegex(text) { return str(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function uid(prefix) {
    return (prefix || "") + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
  function hashString(text) {
    var h = 2166136261;
    var s = str(text);
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function seeded(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ------------------------------------------------------------ tokens (estimate + cache) */
  // The same language-aware estimate MemuAI shows everywhere (pgstatic/tokens.js, inlined by the host at
  // the @memu-shared:tokens marker): Russian costs more tokens than English, and each model family counts
  // differently. setTokenModel() picks the family of the chat's model; without it — the median family.
  var tokenCache = new Map();
  var tokenModel = "";
  function setTokenModel(model) {
    var next = str(model);
    if (next !== tokenModel) { tokenModel = next; tokenCache.clear(); }
  }
  function estimateTokens(text) {
    var s = str(text);
    if (!s) return 0;
    if (tokenCache.has(s)) return tokenCache.get(s);
    var count = MemuTokens.estimate(s, tokenModel);
    if (tokenCache.size > 4000) tokenCache.clear();
    tokenCache.set(s, count);
    return count;
  }
  function messageTokens(message) {
    if (!message) return 0;
    var content = message.content;
    var text = typeof content === "string" ? content : (Array.isArray(content)
      ? content.map(function (p) { return p && p.type === "text" ? p.text : (p && p.type === "image_url" ? "" : ""); }).join("\n") : "");
    var images = Array.isArray(content) ? content.filter(function (p) { return p && p.type === "image_url"; }).length : 0;
    return estimateTokens(text) + 4 + (message.name ? 1 : 0) + images * 765;
  }

  /* ------------------------------------------------------------ macros (SillyTavern legacy engine) */
  /** ctx: { user, char, group, persona, description, personality, scenario, mesExamples, mesExamplesRaw,
   *   charPrompt, charJailbreak, charDepthPrompt, creatorNotes, charVersion, model, original,
   *   chat (array of {name,is_user,mes,date,swipes}), variables (object, mutated), globals (object, mutated),
   *   input, maxContext, maxResponse, maxPrompt, chatId, now (Date), lang } */
  function rollDice(formula) {
    var f = str(formula).trim().replace(/\s+/g, "");
    if (/^\d+$/.test(f)) f = "1d" + f;
    var m = /^(\d*)d(\d+)([+-]\d+)?$/i.exec(f);
    if (!m) return "";
    var count = Math.min(100, parseInt(m[1] || "1", 10)), sides = parseInt(m[2], 10), mod = parseInt(m[3] || "0", 10);
    if (!sides) return "";
    var total = 0;
    for (var i = 0; i < count; i++) total += 1 + Math.floor(Math.random() * sides);
    return String(total + mod);
  }
  function humanizeDuration(ms, lang) {
    var ru = lang === "ru";
    var s = Math.abs(ms) / 1000;
    if (s < 45) return ru ? "несколько секунд" : "a few seconds";
    if (s < 90) return ru ? "минута" : "a minute";
    var m = s / 60;
    if (m < 45) return Math.round(m) + (ru ? " мин." : " minutes");
    if (m < 90) return ru ? "час" : "an hour";
    var h = m / 60;
    if (h < 22) return Math.round(h) + (ru ? " ч." : " hours");
    if (h < 36) return ru ? "день" : "a day";
    var d = h / 24;
    if (d < 26) return Math.round(d) + (ru ? " дн." : " days");
    if (d < 45) return ru ? "месяц" : "a month";
    if (d < 320) return Math.round(d / 30) + (ru ? " мес." : " months");
    return Math.round(d / 365) + (ru ? " г." : " years");
  }
  function formatDate(date, format, lang) {
    var locale = lang === "ru" ? "ru-RU" : "en-US";
    try {
      if (format === "LT") return date.toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
      if (format === "LL") return date.toLocaleDateString(locale, { year: "numeric", month: "long", day: "numeric" });
      if (format === "dddd") return date.toLocaleDateString(locale, { weekday: "long" });
    } catch (e) { /* old engine */ }
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return str(format)
      .replace(/YYYY/g, String(date.getFullYear())).replace(/MM/g, pad(date.getMonth() + 1))
      .replace(/DD/g, pad(date.getDate())).replace(/HH/g, pad(date.getHours()))
      .replace(/mm/g, pad(date.getMinutes())).replace(/ss/g, pad(date.getSeconds()));
  }
  function lastIndex(chat, filter) {
    for (var i = (chat || []).length - 1; i >= 0; i--) if (!filter || filter(chat[i])) return i;
    return -1;
  }
  function idleDuration(ctx) {
    var chat = ctx.chat || [];
    var takeNext = false;
    for (var i = chat.length - 1; i >= 0; i--) {
      var m = chat[i];
      if (m.is_system) continue;
      if (m.is_user && takeNext) {
        if (m.date) return humanizeDuration(Date.now() - Number(m.date), ctx.lang);
        break;
      }
      takeNext = true;
    }
    return ctx.lang === "ru" ? "только что" : "just now";
  }
  function listFrom(listString) {
    return listString.indexOf("::") >= 0 ? listString.split("::")
      : listString.replace(/\\,/g, "##COMMA##").split(",").map(function (x) { return x.trim().replace(/##COMMA##/g, ","); });
  }
  // Nested macros ({{setvar::X::… {{getvar::Lang}} …}}, used by big modern presets) are resolved inside-out, as in
  // SillyTavern's macro engine: the flat regex pipeline below would end the outer macro at the inner «}}» —
  // phase 2: «Engine» stored «…{{getvar::Language» in a variable and leaked the rest of the block into the prompt.
  // Comments {{// … }} are left alone (their content is never evaluated).
  function resolveNested(content, ctx) {
    if (content.indexOf("{{") < 0) return content;
    for (var guard = 0; guard < 500; guard++) {
      var stack = [], hit = null;
      for (var i = 0; i < content.length - 1; i++) {
        if (content.charCodeAt(i) === 123 && content.charCodeAt(i + 1) === 123) { stack.push(i); i++; continue; }
        if (content.charCodeAt(i) === 125 && content.charCodeAt(i + 1) === 125 && stack.length) {
          var start = stack.pop();
          if (stack.length && content.slice(stack[0] + 2, stack[0] + 4) !== "//") { hit = [start, i + 2]; break; }
          i++;
        }
      }
      if (!hit) break;
      content = content.slice(0, hit[0]) + substitute(content.slice(hit[0], hit[1]), ctx) + content.slice(hit[1]);
    }
    return content;
  }
  function substitute(content, ctx) {
    if (!content) return "";
    ctx = ctx || {};
    content = resolveNested(String(content), ctx);
    var raw = content;
    var vars = ctx.variables || (ctx.variables = {});
    var globals = ctx.globals || (ctx.globals = {});
    var originalUsed = false;
    function numberOf(value) { var n = Number(value); return Number.isFinite(n) ? n : 0; }
    function addVar(store, name, value) {
      var current = store[name];
      if (current !== undefined && current !== "" && Number.isFinite(Number(current)) && Number.isFinite(Number(value))) {
        store[name] = String(Number(current) + Number(value));
      } else store[name] = str(current) + str(value);
    }
    // Card fields first: {{user}}/{{char}} must be substituted last, inside {{description}} too (as in ST).
    var env = {
      charPrompt: ctx.charPrompt || "", charInstruction: ctx.charJailbreak || "", charJailbreak: ctx.charJailbreak || "",
      description: ctx.description || "", personality: ctx.personality || "", scenario: ctx.scenario || "",
      persona: ctx.persona || "", mesExamples: ctx.mesExamples || "", mesExamplesRaw: ctx.mesExamplesRaw || "",
      charVersion: ctx.charVersion || "", char_version: ctx.charVersion || "",
      charDepthPrompt: ctx.charDepthPrompt || "", creatorNotes: ctx.creatorNotes || "",
      summary: ctx.summary || ""  // ST «Summarize» extension; no summaries here → empty, as in ST without one
    };
    env.user = ctx.user || "User"; env.char = ctx.char || "";
    env.group = env.charIfNotGroup = env.groupNotMuted = ctx.group || ctx.char || "";
    env.notChar = ctx.user || "User"; env.model = ctx.model || "";
    if (typeof ctx.original === "string") env.original = function () { if (originalUsed) return ""; originalUsed = true; return ctx.original; };
    if (ctx.extra) Object.keys(ctx.extra).forEach(function (k) { env[k] = ctx.extra[k]; });
    var now = ctx.now instanceof Date ? ctx.now : new Date();
    var chat = ctx.chat || [];
    var pre = [
      [/<USER>/gi, function () { return env.user; }], [/<BOT>/gi, function () { return env.char; }],
      [/<CHAR>/gi, function () { return env.char; }], [/<CHARIFNOTGROUP>/gi, function () { return env.group; }],
      [/<GROUP>/gi, function () { return env.group; }],
      [/{{roll[ : ]([^}]+)}}/gi, function (_, f) { return rollDice(f); }],
      [/{{setvar::([^:]+)::([^}]*)}}/gi, function (_, n, v) { vars[n.trim()] = v; return ""; }],
      [/{{addvar::([^:]+)::([^}]+)}}/gi, function (_, n, v) { addVar(vars, n.trim(), v); return ""; }],
      [/{{incvar::([^}]+)}}/gi, function (_, n) { vars[n.trim()] = String(numberOf(vars[n.trim()]) + 1); return vars[n.trim()]; }],
      [/{{decvar::([^}]+)}}/gi, function (_, n) { vars[n.trim()] = String(numberOf(vars[n.trim()]) - 1); return vars[n.trim()]; }],
      [/{{getvar::([^}]+)}}/gi, function (_, n) { var v = vars[n.trim()]; return v === undefined ? "" : str(v); }],
      [/{{setglobalvar::([^:]+)::([^}]*)}}/gi, function (_, n, v) { globals[n.trim()] = v; return ""; }],
      [/{{addglobalvar::([^:]+)::([^}]+)}}/gi, function (_, n, v) { addVar(globals, n.trim(), v); return ""; }],
      [/{{incglobalvar::([^}]+)}}/gi, function (_, n) { globals[n.trim()] = String(numberOf(globals[n.trim()]) + 1); return globals[n.trim()]; }],
      [/{{decglobalvar::([^}]+)}}/gi, function (_, n) { globals[n.trim()] = String(numberOf(globals[n.trim()]) - 1); return globals[n.trim()]; }],
      [/{{getglobalvar::([^}]+)}}/gi, function (_, n) { var v = globals[n.trim()]; return v === undefined ? "" : str(v); }],
      [/{{newline}}/gi, function () { return "\n"; }],
      [/(?:\r?\n)*{{trim}}(?:\r?\n)*/gi, function () { return ""; }],
      [/{{noop}}/gi, function () { return ""; }],
      [/{{input}}/gi, function () { return str(ctx.input); }]
    ];
    var envMacros = Object.keys(env).map(function (name) {
      return [new RegExp("{{" + escapeRegex(name) + "}}", "gi"), function () {
        var v = env[name];
        return str(typeof v === "function" ? v() : v);
      }];
    });
    var lastMessage = function () { var i = lastIndex(chat); return i >= 0 ? str(chat[i].mes) : ""; };
    var post = [
      [/{{maxPrompt(?:Tokens)?}}/gi, function () { return str(ctx.maxPrompt); }],
      [/{{maxContext(?:Tokens)?}}/gi, function () { return str(ctx.maxContext); }],
      [/{{maxResponse(?:Tokens)?}}/gi, function () { return str(ctx.maxResponse); }],
      [/{{lastMessage}}/gi, lastMessage],
      [/{{lastChatMessage}}/gi, lastMessage],
      [/{{lastMessageId}}/gi, function () { var i = lastIndex(chat); return i >= 0 ? String(i) : ""; }],
      [/{{lastUserMessage}}/gi, function () { var i = lastIndex(chat, function (m) { return m.is_user && !m.is_system; }); return i >= 0 ? str(chat[i].mes) : ""; }],
      [/{{lastCharMessage}}/gi, function () { var i = lastIndex(chat, function (m) { return !m.is_user && !m.is_system; }); return i >= 0 ? str(chat[i].mes) : ""; }],
      [/{{firstIncludedMessageId}}/gi, function () { return "0"; }],
      [/{{lastSwipeId}}/gi, function () { var i = lastIndex(chat); return i >= 0 && chat[i].swipes ? String(chat[i].swipes.length) : ""; }],
      [/{{currentSwipeId}}/gi, function () { var i = lastIndex(chat); return i >= 0 ? String((chat[i].swipe_id || 0) + 1) : ""; }],
      [/{{allChatRange}}/gi, function () { return chat.length ? "0-" + (chat.length - 1) : ""; }],
      [/{{reverse:(.+?)}}/gi, function (_, s) { return Array.from(s).reverse().join(""); }],
      [/\{\{\/\/([\s\S]*?)\}\}/gm, function () { return ""; }],
      [/{{time}}/gi, function () { return formatDate(now, "LT", ctx.lang); }],
      [/{{date}}/gi, function () { return formatDate(now, "LL", ctx.lang); }],
      [/{{weekday}}/gi, function () { return formatDate(now, "dddd", ctx.lang); }],
      [/{{isotime}}/gi, function () { return formatDate(now, "HH:mm"); }],
      [/{{isodate}}/gi, function () { return formatDate(now, "YYYY-MM-DD"); }],
      [/{{datetimeformat +([^}]*)}}/gi, function (_, f) { return formatDate(now, f.trim(), ctx.lang); }],
      [/{{idle_duration}}/gi, function () { return idleDuration(ctx); }],
      [/{{time_UTC([-+]\d+)}}/gi, function (_, off) {
        var d = new Date(now.getTime() + (now.getTimezoneOffset() + parseInt(off, 10) * 60) * 60000);
        return formatDate(d, "LT", ctx.lang);
      }],
      [/{{timeDiff::(.*?)::(.*?)}}/gi, function (_, a, b) {
        var da = new Date(a), db = new Date(b);
        if (isNaN(da) || isNaN(db)) return "";
        return humanizeDuration(da - db, ctx.lang);
      }],
      [/{{banned "(.*)"}}/gi, function () { return ""; }],
      [/{{random\s?::?([^}]+)}}/gi, function (_, list) {
        var items = listFrom(list); return items.length ? items[Math.floor(Math.random() * items.length)] : "";
      }],
      [/{{pick\s?::?([^}]+)}}/gi, function (_, list, offset) {
        var items = listFrom(list);
        if (!items.length) return "";
        var rng = seeded(hashString(str(ctx.chatId) + "-" + hashString(raw) + "-" + offset));
        return items[Math.floor(rng() * items.length)];
      }]
    ];
    var all = pre.concat(envMacros, post);
    for (var i = 0; i < all.length; i++) {
      if (!content) break;
      var rx = all[i][0];
      if (rx.source.charAt(0) !== "<" && content.indexOf("{{") < 0) break;
      try { content = content.replace(rx, all[i][1]); } catch (e) { /* bad macro */ }
    }
    return content;
  }

  /* ------------------------------------------------------------ regex helpers */
  function parseRegexFromString(input) {
    var m = /^\/([\w\W]+?)\/([gimsuy]*)$/.exec(str(input));
    if (!m) return null;
    var pattern = m[1], flags = m[2];
    if (/(^|[^\\])\//.test(pattern)) return null;
    try { return new RegExp(pattern.replace(/\\\//g, "/"), flags); } catch (e) { return null; }
  }

  /* ------------------------------------------------------------ World Info */
  var WI_POS = { before: 0, after: 1, ANTop: 2, ANBottom: 3, atDepth: 4, EMTop: 5, EMBottom: 6, outlet: 7 };
  var WI_LOGIC = { AND_ANY: 0, NOT_ALL: 1, NOT_ANY: 2, AND_ALL: 3 };
  var ROLE_NAMES = ["system", "user", "assistant"];
  var ENTRY_DEFAULTS = {
    key: [], keysecondary: [], comment: "", content: "", constant: false, vectorized: false, selective: true,
    selectiveLogic: 0, addMemo: true, order: 100, position: 0, disable: false, ignoreBudget: false,
    excludeRecursion: false, preventRecursion: false, matchPersonaDescription: false,
    matchCharacterDescription: false, matchCharacterPersonality: false, matchCharacterDepthPrompt: false,
    matchScenario: false, matchCreatorNotes: false, delayUntilRecursion: 0, probability: 100,
    useProbability: true, depth: 4, outletName: "", group: "", groupOverride: false, groupWeight: 100,
    scanDepth: null, caseSensitive: null, matchWholeWords: null, useGroupScoring: null, automationId: "",
    role: 0, sticky: null, cooldown: null, delay: null, triggers: []
  };
  var WI_DEFAULTS = {
    depth: 2, budget: 25, budgetCap: 0, includeNames: true, recursive: true, overflowAlert: false,
    caseSensitive: false, matchWholeWords: false, useGroupScoring: false, strategy: 1,
    maxRecursionSteps: 0, minActivations: 0, minActivationsDepthMax: 0
  };
  function normalizeEntry(raw, uidValue) {
    var e = Object.assign(clone(ENTRY_DEFAULTS), raw || {});
    e.uid = raw && raw.uid !== undefined ? raw.uid : uidValue;
    ["key", "keysecondary", "triggers"].forEach(function (k) {
      if (typeof e[k] === "string") e[k] = e[k].split(",").map(function (x) { return x.trim(); }).filter(Boolean);
      if (!Array.isArray(e[k])) e[k] = [];
    });
    // SillyTavern writes `characterFilter` only when one is set (or `null`): keep it exactly as it came.
    if (e.characterFilter !== undefined && e.characterFilter !== null && typeof e.characterFilter !== "object") delete e.characterFilter;
    e.order = num(e.order, 100); e.position = num(e.position, 0); e.depth = num(e.depth, 4);
    e.probability = num(e.probability, 100); e.role = num(e.role, 0); e.groupWeight = num(e.groupWeight, 100);
    e.selectiveLogic = num(e.selectiveLogic, 0);
    return e;
  }
  /* World files. The internal shape IS the SillyTavern World Info file ({entries:{uid:{key, keysecondary, …}}}),
   * so an ST lorebook round-trips untouched (unknown fields included). Other shapes are converted the way
   * SillyTavern converts them (public/scripts/world-info.js): character_book (V2/V3 card, chub), chub's own
   * "card with a character_book", NovelAI (lorebookVersion), Agnai memory books (kind: "memory") and RisuAI
   * (type: "risu"). A converted character_book keeps the original in `originalData` (like ST), so exporting it
   * back as a character_book restores every field the converter did not understand. */
  var WORLD_META_SKIP = { entries: 1, originalData: 1, name: 1, id: 1, source: 1 };
  function first() { for (var i = 0; i < arguments.length; i++) if (arguments[i] !== undefined) return arguments[i]; return undefined; }
  function keyList(value) {
    if (Array.isArray(value)) return value.map(str).filter(function (x) { return x.length; });
    if (typeof value === "string") return value.split(",").map(function (x) { return x.trim(); }).filter(Boolean);
    return [];
  }
  function worldFrom(entries, name, extra) {
    var world = { name: str(name) || "World", entries: entries };
    if (extra) Object.keys(extra).forEach(function (k) { if (extra[k] !== undefined) world[k] = extra[k]; });
    return world;
  }
  /** An ST entry that may be a chub "hybrid" (ST + V2 field names side by side). */
  function stEntry(raw, uidValue) {
    var r = raw && typeof raw === "object" ? raw : {};
    var e = Object.assign({}, r);
    if (e.key === undefined && r.keys !== undefined) e.key = keyList(r.keys);
    if (e.keysecondary === undefined && r.secondary_keys !== undefined) e.keysecondary = keyList(r.secondary_keys);
    if (e.order === undefined && r.insertion_order !== undefined) e.order = r.insertion_order;
    if (e.disable === undefined && r.enabled !== undefined) e.disable = r.enabled === false;
    if (e.comment === undefined && r.name !== undefined) e.comment = str(r.name);
    return normalizeEntry(e, uidValue);
  }
  function normalizeWorld(raw, fallbackName) {
    if (!raw || typeof raw !== "object") return worldFrom({}, fallbackName);
    var entries = raw.entries;
    if (Array.isArray(entries)) {
      var book = fromCharacterBook(raw);
      if (!str(raw.name) && fallbackName) book.name = fallbackName;
      return book;
    }
    var out = {};
    if (entries && typeof entries === "object") {
      Object.keys(entries).forEach(function (k, i) {
        var e = stEntry(entries[k], num(k, i));
        if (e.uid === undefined || e.uid === null || e.uid === "") e.uid = num(k, i);
        out[e.uid] = e;
      });
    }
    var meta = {};
    Object.keys(raw).forEach(function (k) { if (!WORLD_META_SKIP[k] || k === "name") meta[k] = clone(raw[k]); });
    var world = worldFrom(out, str(raw.name) || fallbackName);
    if (Object.keys(meta).length) world.meta = meta;
    if (raw.originalData && typeof raw.originalData === "object") world.originalData = clone(raw.originalData);
    return world;
  }
  function fromCharacterBook(book) {
    book = book || {};
    var entries = {};
    (Array.isArray(book.entries) ? book.entries : []).forEach(function (entry, index) {
      entry = entry && typeof entry === "object" ? entry : {};
      var ext = entry.extensions && typeof entry.extensions === "object" ? entry.extensions : {};
      var id = entry.id === undefined || entry.id === null || entry.id === "" ? index : entry.id;
      if (entries[id] !== undefined) id = index + 100000;              // duplicate ids in the wild
      var pos = first(ext.position);
      var e = normalizeEntry({
        uid: id, key: keyList(entry.keys), keysecondary: keyList(entry.secondary_keys),
        comment: str(first(entry.comment, entry.name, "")) || str(entry.name), content: str(entry.content),
        constant: !!entry.constant, selective: !!first(entry.selective, false),
        order: num(first(entry.insertion_order, entry.priority), 100),
        position: pos !== undefined ? num(pos, 0) : (entry.position === "before_char" ? 0 : 1),
        excludeRecursion: !!first(ext.exclude_recursion, ext.excludeRecursion, false),
        preventRecursion: !!first(ext.prevent_recursion, ext.preventRecursion, false),
        delayUntilRecursion: first(ext.delay_until_recursion, ext.delayUntilRecursion, false),
        disable: entry.enabled === false, addMemo: !!first(entry.comment, entry.name),
        displayIndex: first(ext.display_index, ext.displayIndex, index),
        probability: num(first(ext.probability, entry.probability), 100),
        useProbability: first(ext.useProbability, ext.use_probability, true) !== false,
        depth: num(first(ext.depth, entry.depth), 4),
        selectiveLogic: num(first(ext.selectiveLogic, ext.selective_logic, entry.selectiveLogic), 0),
        outletName: str(first(ext.outlet_name, ext.outletName, "")),
        group: str(first(ext.group, "")), groupOverride: !!first(ext.group_override, ext.groupOverride, false),
        groupWeight: num(first(ext.group_weight, ext.groupWeight), 100),
        scanDepth: first(ext.scan_depth, ext.scanDepth, null),
        caseSensitive: first(ext.case_sensitive, ext.caseSensitive, entry.case_sensitive, null),
        matchWholeWords: first(ext.match_whole_words, ext.matchWholeWords, null),
        useGroupScoring: first(ext.use_group_scoring, ext.useGroupScoring, null),
        automationId: str(first(ext.automation_id, ext.automationId, "")), role: num(first(ext.role, 0), 0),
        vectorized: !!first(ext.vectorized, false),
        sticky: first(ext.sticky, null), cooldown: first(ext.cooldown, null), delay: first(ext.delay, null),
        matchPersonaDescription: !!first(ext.match_persona_description, false),
        matchCharacterDescription: !!first(ext.match_character_description, false),
        matchCharacterPersonality: !!first(ext.match_character_personality, false),
        matchCharacterDepthPrompt: !!first(ext.match_character_depth_prompt, false),
        matchScenario: !!first(ext.match_scenario, false), matchCreatorNotes: !!first(ext.match_creator_notes, false),
        extensions: clone(ext), triggers: Array.isArray(ext.triggers) ? ext.triggers.slice() : [],
        ignoreBudget: !!first(ext.ignore_budget, ext.ignoreBudget, false)
      }, id);
      if (ext.characterFilter !== undefined && ext.characterFilter !== null) e.characterFilter = clone(ext.characterFilter);
      if (entry.use_regex !== undefined) e.useRegex = !!entry.use_regex;
      entries[e.uid] = e;
    });
    var world = worldFrom(entries, book.name);
    world.originalData = clone(book);
    return world;
  }
  /** The ST world (or any internal world) -> a V2/V3 character_book. Fields of the original book survive. */
  function toCharacterBook(world) {
    world = world || {};
    var original = world.originalData && Array.isArray(world.originalData.entries) ? world.originalData : null;
    var originals = {};
    if (original) original.entries.forEach(function (x, i) {
      if (x && typeof x === "object") originals[String(x.id === undefined || x.id === null ? i : x.id)] = x;
    });
    var keys = Object.keys(world.entries || {});
    var out = original ? clone(original) : {};
    delete out.entries;
    out.name = str(world.name || out.name);
    if (out.extensions === undefined) out.extensions = {};
    var bookExt = world.meta || {};
    ["description", "scan_depth", "token_budget", "recursive_scanning"].forEach(function (k) {
      if (out[k] === undefined && bookExt[k] !== undefined) out[k] = clone(bookExt[k]);
    });
    if (bookExt.extensions && typeof bookExt.extensions === "object") out.extensions = Object.assign({}, bookExt.extensions, out.extensions);
    out.entries = keys.map(function (k, i) {
      var e = normalizeEntry(world.entries[k], k);
      var orig = originals[String(e.uid)] ? clone(originals[String(e.uid)]) : {};
      var row = orig;
      row.id = num(e.uid, i);
      row.keys = (e.key || []).slice();
      row.secondary_keys = (e.keysecondary || []).slice();
      // ST reads `comment || ""`; chub books keep the title in `name` and an empty comment — restore it as it was.
      row.comment = orig.comment === "" && orig.name !== undefined && str(orig.name) === str(e.comment) ? "" : str(e.comment);
      row.content = str(e.content);
      row.constant = !!e.constant;
      row.selective = !!e.selective;
      row.insertion_order = num(e.order, 100);
      row.enabled = !e.disable;
      row.position = num(e.position, 0) === 0 ? "before_char" : "after_char";
      row.use_regex = e.useRegex !== undefined ? !!e.useRegex : true;     // ST keys are always regex-capable
      if (e.caseSensitive !== null && e.caseSensitive !== undefined) row.case_sensitive = !!e.caseSensitive;
      var ext = Object.assign({}, orig.extensions || {}, e.extensions && typeof e.extensions === "object" ? e.extensions : {});
      Object.assign(ext, {
        position: num(e.position, 0), exclude_recursion: !!e.excludeRecursion,
        display_index: e.displayIndex !== undefined ? e.displayIndex : i,
        probability: e.probability === undefined ? null : e.probability, useProbability: e.useProbability !== false,
        depth: num(e.depth, 4), selectiveLogic: num(e.selectiveLogic, 0), outlet_name: str(e.outletName),
        group: str(e.group), group_override: !!e.groupOverride, group_weight: e.groupWeight === undefined ? null : e.groupWeight,
        prevent_recursion: !!e.preventRecursion, delay_until_recursion: e.delayUntilRecursion === undefined ? false : e.delayUntilRecursion,
        scan_depth: e.scanDepth === undefined ? null : e.scanDepth, match_whole_words: e.matchWholeWords === undefined ? null : e.matchWholeWords,
        use_group_scoring: e.useGroupScoring === undefined || e.useGroupScoring === null ? false : e.useGroupScoring,
        case_sensitive: e.caseSensitive === undefined ? null : e.caseSensitive, automation_id: str(e.automationId),
        role: num(e.role, 0), vectorized: !!e.vectorized,
        sticky: e.sticky === undefined ? null : e.sticky, cooldown: e.cooldown === undefined ? null : e.cooldown,
        delay: e.delay === undefined ? null : e.delay,
        match_persona_description: !!e.matchPersonaDescription, match_character_description: !!e.matchCharacterDescription,
        match_character_personality: !!e.matchCharacterPersonality, match_character_depth_prompt: !!e.matchCharacterDepthPrompt,
        match_scenario: !!e.matchScenario, match_creator_notes: !!e.matchCreatorNotes,
        triggers: (e.triggers || []).slice(), ignore_budget: !!e.ignoreBudget
      });
      var cf = e.characterFilter;
      if (cf && ((cf.names || []).length || (cf.tags || []).length || cf.isExclude)) ext.characterFilter = clone(cf);
      row.extensions = ext;
      return row;
    });
    return out;
  }
  /** The world as a SillyTavern World Info file. */
  function exportWorld(world) {
    var out = {};
    var meta = (world && world.meta) || {};
    Object.keys(meta).forEach(function (k) { if (!WORLD_META_SKIP[k]) out[k] = clone(meta[k]); });
    if (meta.name !== undefined) out.name = str(world.name);        // the file named itself: keep the key (renamed or not)
    out.entries = {};
    Object.keys((world && world.entries) || {}).forEach(function (k, i) {
      var e = clone(normalizeEntry(world.entries[k], k));
      if (e.displayIndex === undefined) e.displayIndex = i;
      delete e.useRegex;
      out.entries[e.uid] = e;
    });
    if (world && world.originalData) out.originalData = clone(world.originalData);
    return out;
  }
  /** NovelAI lorebook (lorebookVersion), as SillyTavern converts it. */
  function fromNovelLorebook(json) {
    var entries = {};
    (json.entries || []).forEach(function (entry, index) {
      var name = entry.displayName;
      entries[index] = normalizeEntry({ uid: index, key: keyList(entry.keys), keysecondary: [], comment: str(name),
        content: str(entry.text), constant: false, selective: false, order: num(entry.contextConfig && entry.contextConfig.budgetPriority, 0),
        position: 0, disable: entry.enabled === false, addMemo: !!(name && String(name).trim()), displayIndex: index }, index);
    });
    return worldFrom(entries, json.name || (json.settings && json.settings.name));
  }
  /** Agnai memory book (kind: "memory"). */
  function fromAgnaiMemory(json) {
    var entries = {};
    (json.entries || []).forEach(function (entry, index) {
      entries[index] = normalizeEntry({ uid: index, key: keyList(entry.keywords), keysecondary: [], comment: str(entry.name),
        content: str(entry.entry), constant: false, selective: false, order: num(entry.weight, 100), position: 0,
        disable: entry.enabled === false, addMemo: !!entry.name, displayIndex: index }, index);
    });
    return worldFrom(entries, json.name, json.description ? { meta: { description: str(json.description) } } : null);
  }
  /** RisuAI lorebook export (type: "risu"). */
  function fromRisuLorebook(json) {
    var entries = {};
    (json.data || []).forEach(function (entry, index) {
      entries[index] = normalizeEntry({ uid: index, key: keyList(entry.key), keysecondary: keyList(entry.secondkey),
        comment: str(entry.comment), content: str(entry.content), constant: !!entry.alwaysActive, selective: !!entry.selective,
        order: num(entry.insertorder, 100), position: 0, disable: false, addMemo: true, displayIndex: index,
        probability: num(entry.activationPercent, 100), useProbability: true,
        useRegex: entry.useRegex === undefined ? undefined : !!entry.useRegex }, index);
      if (entries[index].useRegex === undefined) delete entries[index].useRegex;
    });
    return worldFrom(entries, json.name);
  }
  /** Agnai's native book back from a world (for completeness of the export menu). */
  function toAgnaiMemory(world) {
    var rows = Object.keys((world && world.entries) || {}).map(function (k) { return normalizeEntry(world.entries[k], k); });
    return { kind: "memory", name: str(world && world.name), description: str(world && world.meta && world.meta.description),
      entries: rows.map(function (e) { return { name: str(e.comment), entry: str(e.content), keywords: (e.key || []).slice(),
        priority: num(e.order, 100), weight: num(e.order, 100), enabled: !e.disable }; }) };
  }
  /** Which kind of JSON this is: "lorebook" (any supported book format), "character", "preset", "chat" or "". */
  function detectJson(json) {
    if (!json || typeof json !== "object" || Array.isArray(json)) return "";
    if (json.lorebookVersion !== undefined || json.kind === "memory" || json.type === "risu") return "lorebook";
    var data = json.data && typeof json.data === "object" ? json.data : null;
    if (json.entries && typeof json.entries === "object" && !json.spec && !(data && data.name)) return "lorebook";
    if (json.spec === "lorebook_v3" && data && data.entries) return "lorebook";
    if (json.kind === "character" || (json.persona && json.greeting !== undefined && json.sampleChat !== undefined)) return "character";
    if (json.spec || (data && (data.name || data.first_mes !== undefined)) || json.char_name !== undefined
      || (json.name !== undefined && (json.first_mes !== undefined || json.description !== undefined || json.mes_example !== undefined))) return "character";
    if (json.prompts || json.prompt_order || json.temperature !== undefined) return "preset";
    return "";
  }
  /** A chub "lorebook card" ({spec, data:{character_book}} with no character in it). */
  function isBookCard(json) {
    var d = json && json.data;
    return !!(d && d.character_book && Array.isArray(d.character_book.entries)
      && !str(d.first_mes).trim() && !str(d.description).trim() && !str(d.personality).trim());
  }
  /** Any supported lorebook JSON (or a card that carries a character_book) -> { world, format }. */
  function importWorld(json, fallbackName) {
    if (!json || typeof json !== "object") throw new Error("bad_lorebook");
    var world, format;
    if (json.lorebookVersion !== undefined) { world = fromNovelLorebook(json); format = "novelai"; }
    else if (json.kind === "memory") { world = fromAgnaiMemory(json); format = "agnai"; }
    else if (json.type === "risu") { world = fromRisuLorebook(json); format = "risu"; }
    else if (json.spec === "lorebook_v3" && json.data) { world = fromCharacterBook(json.data); format = "lorebook_v3"; }
    else if (Array.isArray(json.entries)) { world = fromCharacterBook(json); format = "character_book"; }
    else if (json.entries && typeof json.entries === "object") { world = normalizeWorld(json, fallbackName); format = "sillytavern"; }
    else if (json.data && json.data.character_book) { world = fromCharacterBook(json.data.character_book); format = isBookCard(json) ? "chub" : "card";
      if (!str(world.name)) world.name = str(json.data.name); }
    else if (json.character_book) { world = fromCharacterBook(json.character_book); format = "card"; }
    else throw new Error("bad_lorebook");
    if (!str(world.name) || world.name === "World") world.name = str(fallbackName) || world.name;
    return { world: world, format: format, count: Object.keys(world.entries).length };
  }

  function matchKeys(haystack, needle, entry, settings) {
    var rx = parseRegexFromString(needle);
    if (rx) { rx.lastIndex = 0; return rx.test(haystack); }
    var cs = entry.caseSensitive === null || entry.caseSensitive === undefined ? settings.caseSensitive : entry.caseSensitive;
    var h = cs ? haystack : haystack.toLowerCase();
    var n = cs ? needle : needle.toLowerCase();
    var whole = entry.matchWholeWords === null || entry.matchWholeWords === undefined ? settings.matchWholeWords : entry.matchWholeWords;
    if (whole) {
      if (n.split(/\s+/).length > 1) return h.indexOf(n) >= 0;
      return new RegExp("(?:^|\\W)(" + escapeRegex(n) + ")(?:$|\\W)").test(h);
    }
    return h.indexOf(n) >= 0;
  }

  /** args: { chatLines (newest first, strings), worlds: [{id, name, entries, source:'global'|'char'|'chat'|'persona'}],
   *  settings (WI_DEFAULTS), maxContext, meta (chat metadata, mutated: timedWorldInfo), chatLength, trigger,
   *  globalScan: {personaDescription, characterDescription, characterPersonality, characterDepthPrompt, scenario, creatorNotes},
   *  injects: [strings to scan], charName, charTags, subst(text) } */
  function checkWorldInfo(args) {
    var settings = Object.assign({}, WI_DEFAULTS, args.settings || {});
    var subst = args.subst || function (x) { return x; };
    var lines = args.chatLines || [];
    var meta = args.meta || {};
    var chatLength = args.chatLength === undefined ? lines.length : args.chatLength;
    var empty = { worldInfoBefore: "", worldInfoAfter: "", EMEntries: [], WIDepthEntries: [], ANBeforeEntries: [], ANAfterEntries: [], outletEntries: {}, activated: [] };
    // Sorted entries: strategy 0 evenly, 1 character first, 2 global first (by order desc inside)
    var groups = { global: [], char: [], chat: [], persona: [] };
    (args.worlds || []).forEach(function (world) {
      Object.keys(world.entries || {}).forEach(function (k) {
        var e = normalizeEntry(world.entries[k], k);
        e.world = world.id || world.name;
        e.worldName = world.name;
        e.hash = hashString(JSON.stringify([e.world, e.uid, e.content, e.key]));
        e.decorators = [];
        var content = str(e.content);
        while (/^@@(activate|dont_activate)\s*\n?/.test(content)) {
          var d = /^@@(activate|dont_activate)/.exec(content)[0];
          e.decorators.push(d);
          content = content.replace(/^@@(activate|dont_activate)\s*\n?/, "");
        }
        e.content = content;
        (groups[world.source] || groups.global).push(e);
      });
    });
    var sortFn = function (a, b) { return b.order - a.order; };
    var sorted;
    var head = groups.chat.concat(groups.persona).sort(sortFn);
    if (settings.strategy === 0) sorted = head.concat(groups.global.concat(groups.char).sort(sortFn));
    else if (settings.strategy === 2) sorted = head.concat(groups.global.sort(sortFn), groups.char.sort(sortFn));
    else sorted = head.concat(groups.char.sort(sortFn), groups.global.sort(sortFn));
    if (!sorted.length) return empty;

    // timed effects
    var timed = meta.timedWorldInfo || (meta.timedWorldInfo = {});
    timed.sticky = timed.sticky && typeof timed.sticky === "object" ? timed.sticky : {};
    timed.cooldown = timed.cooldown && typeof timed.cooldown === "object" ? timed.cooldown : {};
    var active = { sticky: [], cooldown: [], delay: [] };
    var dry = !!args.dryRun;
    function keyOf(e) { return e.world + "." + e.uid; }
    function effect(type, e, isProtected) {
      return { hash: e.hash, start: chatLength, end: chatLength + num(e[type], 0), protected: !!isProtected };
    }
    ["sticky", "cooldown"].forEach(function (type) {
      if (dry) return;
      Object.keys(timed[type]).forEach(function (k) {
        var v = timed[type][k];
        var entry = sorted.find(function (x) { return String(x.hash) === String(v.hash); });
        if (chatLength <= num(v.start, 0) && !v.protected) { delete timed[type][k]; return; }
        if (!entry) { if (chatLength >= num(v.end, 0)) delete timed[type][k]; return; }
        if (!entry[type]) { delete timed[type][k]; return; }
        if (chatLength >= num(v.end, 0)) {
          delete timed[type][k];
          if (type === "sticky" && entry.cooldown) {
            timed.cooldown[keyOf(entry)] = effect("cooldown", entry, true);
            active.cooldown.push(entry);
          }
          return;
        }
        active[type].push(entry);
      });
    });
    sorted.forEach(function (e) { if (e.delay && chatLength < e.delay) active.delay.push(e); });
    function isActive(type, e) { return active[type].some(function (x) { return x.hash === e.hash; }); }

    // buffer
    var depthBuffer = lines.slice(0, 1000).map(function (x) { return str(x).trim(); });
    var recurseBuffer = [];
    var injectBuffer = (args.injects || []).filter(Boolean);
    var skew = 0;
    var MATCHER = "\x01", JOINER = "\n" + MATCHER;
    var gs = args.globalScan || {};
    function bufferGet(e, state) {
      var depth = e.scanDepth === null || e.scanDepth === undefined ? settings.depth + skew : num(e.scanDepth, settings.depth);
      if (depth <= 0) return "";
      var result = MATCHER + depthBuffer.slice(0, depth).join(JOINER);
      if (e.matchPersonaDescription && gs.personaDescription) result += JOINER + gs.personaDescription;
      if (e.matchCharacterDescription && gs.characterDescription) result += JOINER + gs.characterDescription;
      if (e.matchCharacterPersonality && gs.characterPersonality) result += JOINER + gs.characterPersonality;
      if (e.matchCharacterDepthPrompt && gs.characterDepthPrompt) result += JOINER + gs.characterDepthPrompt;
      if (e.matchScenario && gs.scenario) result += JOINER + gs.scenario;
      if (e.matchCreatorNotes && gs.creatorNotes) result += JOINER + gs.creatorNotes;
      if (injectBuffer.length) result += JOINER + injectBuffer.join(JOINER);
      if (recurseBuffer.length && state !== 3) result += JOINER + recurseBuffer.join(JOINER);
      return result;
    }
    function score(e, state) {
      var buf = bufferGet(e, state);
      var p = 0, s = 0;
      (e.key || []).forEach(function (k) { if (matchKeys(buf, k, e, settings)) p++; });
      (e.keysecondary || []).forEach(function (k) { if (matchKeys(buf, k, e, settings)) s++; });
      if (!(e.key || []).length) return 0;
      if ((e.keysecondary || []).length) {
        if (e.selectiveLogic === WI_LOGIC.AND_ANY) return p + s;
        if (e.selectiveLogic === WI_LOGIC.AND_ALL) return s === e.keysecondary.length ? p + s : p;
      }
      return p;
    }

    var budget = Math.round(settings.budget * num(args.maxContext, 8192) / 100) || 1;
    if (settings.budgetCap > 0 && budget > settings.budgetCap) budget = settings.budgetCap;
    var recursionLevels = [];
    sorted.forEach(function (e) {
      if (e.delayUntilRecursion) {
        var level = e.delayUntilRecursion === true ? 1 : num(e.delayUntilRecursion, 1);
        if (recursionLevels.indexOf(level) < 0) recursionLevels.push(level);
      }
    });
    recursionLevels.sort(function (a, b) { return a - b; });
    var currentLevel = recursionLevels.shift() || 0;
    var state = 1, count = 0, overflowed = false;
    var activated = new Map();
    var failed = new Set();
    var allText = "";
    var guard = 0;
    while (state && guard++ < 100) {
      if (settings.maxRecursionSteps && settings.maxRecursionSteps <= count) break;
      count++;
      var next = 0;
      var now = [];
      for (var i = 0; i < sorted.length; i++) {
        var e = sorted[i];
        if (failed.has(e) || activated.has(keyOf(e))) continue;
        if (e.disable) continue;
        if (Array.isArray(e.triggers) && e.triggers.length && e.triggers.indexOf(args.trigger || "normal") < 0) continue;
        var cf = e.characterFilter || {};
        if (cf.names && cf.names.length) {
          var included = cf.names.indexOf(args.charName) >= 0;
          if (cf.isExclude ? included : !included) continue;
        }
        if (cf.tags && cf.tags.length) {
          var hasTag = (args.charTags || []).some(function (tag) { return cf.tags.indexOf(tag) >= 0; });
          if (cf.isExclude ? hasTag : !hasTag) continue;
        }
        var sticky = isActive("sticky", e);
        if (isActive("delay", e)) continue;
        if (isActive("cooldown", e) && !sticky) continue;
        if (state !== 2 && e.delayUntilRecursion && !sticky) continue;
        if (state === 2 && e.delayUntilRecursion && e.delayUntilRecursion > currentLevel && !sticky) continue;
        if (state === 2 && settings.recursive && e.excludeRecursion && !sticky) continue;
        if (e.decorators.indexOf("@@activate") >= 0) { now.push(e); continue; }
        if (e.decorators.indexOf("@@dont_activate") >= 0) continue;
        if (e.constant) { now.push(e); continue; }
        if (sticky) { now.push(e); continue; }
        if (!e.key.length) continue;
        var text = bufferGet(e, state);
        var primary = e.key.find(function (k) { var sk = subst(k); return sk && matchKeys(text, sk.trim(), e, settings); });
        if (!primary) continue;
        var hasSecondary = e.selective && e.keysecondary.length;
        if (!hasSecondary) { now.push(e); continue; }
        var logic = e.selectiveLogic || 0;
        var any = false, allMatch = true, ok = false;
        for (var j = 0; j < e.keysecondary.length; j++) {
          var sk2 = subst(e.keysecondary[j]);
          var hit = !!(sk2 && matchKeys(text, sk2.trim(), e, settings));
          if (hit) any = true; else allMatch = false;
          if (logic === WI_LOGIC.AND_ANY && hit) { ok = true; break; }
          if (logic === WI_LOGIC.NOT_ALL && !hit) { ok = true; break; }
        }
        if (!ok && logic === WI_LOGIC.NOT_ANY && !any) ok = true;
        if (!ok && logic === WI_LOGIC.AND_ALL && allMatch) ok = true;
        if (ok) now.push(e);
      }
      var index = new Map(sorted.map(function (x, n) { return [x, n]; }));
      var fresh = now.slice().sort(function (a, b) {
        return (isActive("sticky", b) ? 1 : 0) - (isActive("sticky", a) ? 1 : 0) || index.get(a) - index.get(b);
      });
      // inclusion groups
      var grouped = {};
      fresh.filter(function (x) { return x.group; }).forEach(function (x) {
        x.group.split(/,\s*/).filter(Boolean).forEach(function (g) { (grouped[g] = grouped[g] || []).push(x); });
      });
      var remove = function (x) { var at = fresh.indexOf(x); if (at >= 0) fresh.splice(at, 1); };
      Object.keys(grouped).forEach(function (g) {
        var group = grouped[g];
        var stickies = group.filter(function (x) { return isActive("sticky", x); });
        if (stickies.length) { group.forEach(function (x) { if (stickies.indexOf(x) < 0) remove(x); }); return; }
        group.filter(function (x) { return isActive("cooldown", x) || isActive("delay", x); }).forEach(remove);
        var live = group.filter(function (x) { return fresh.indexOf(x) >= 0; });
        if (settings.useGroupScoring || live.some(function (x) { return x.useGroupScoring; })) {
          var scores = live.map(function (x) { return score(x, state); });
          var max = Math.max.apply(null, scores.concat([0]));
          live.forEach(function (x, n) {
            var scored = x.useGroupScoring === null || x.useGroupScoring === undefined ? settings.useGroupScoring : x.useGroupScoring;
            if (scored && scores[n] < max) remove(x);
          });
          live = live.filter(function (x) { return fresh.indexOf(x) >= 0; });
        }
        var already = Array.from(activated.values()).some(function (x) { return x.group === g; });
        if (already) { live.forEach(remove); return; }
        if (live.length <= 1) return;
        var prios = live.filter(function (x) { return x.groupOverride; }).sort(sortFn);
        if (prios.length) { live.forEach(function (x) { if (x !== prios[0]) remove(x); }); return; }
        var total = live.reduce(function (acc, x) { return acc + num(x.groupWeight, 100); }, 0);
        var roll = Math.random() * total, acc = 0, winner = null;
        for (var w = 0; w < live.length; w++) { acc += num(live[w].groupWeight, 100); if (roll <= acc) { winner = live[w]; break; } }
        if (winner) live.forEach(function (x) { if (x !== winner) remove(x); });
      });
      var newContent = "";
      var baseTokens = estimateTokens(allText);
      var ignoring = fresh.filter(function (x) { return x.ignoreBudget; }).length;
      for (var f = 0; f < fresh.length; f++) {
        var entry = fresh[f];
        ignoring -= entry.ignoreBudget ? 1 : 0;
        if (overflowed && !entry.ignoreBudget) { if (ignoring > 0) continue; break; }
        if (entry.useProbability && entry.probability !== 100 && !isActive("sticky", entry)) {
          if (!(Math.random() * 100 <= entry.probability)) { failed.add(entry); continue; }
        }
        entry.content = subst(entry.content);
        newContent += entry.content + "\n";
        if (!entry.ignoreBudget && baseTokens + estimateTokens(newContent) >= budget) { overflowed = true; continue; }
        activated.set(keyOf(entry), entry);
      }
      var success = fresh.filter(function (x) { return !failed.has(x) && activated.has(keyOf(x)); });
      var forRecursion = success.filter(function (x) { return !x.preventRecursion; });
      if (settings.recursive && !overflowed && forRecursion.length) next = 2;
      if (settings.recursive && !overflowed && state === 3 && recurseBuffer.length) next = 2;
      if (!next && !overflowed && settings.minActivations > 0 && activated.size < settings.minActivations) {
        var overMax = (settings.minActivationsDepthMax > 0 && settings.depth + skew > settings.minActivationsDepthMax) || settings.depth + skew > lines.length;
        if (!overMax) { next = 3; skew++; }
      }
      if (!next && recursionLevels.length) { next = 2; currentLevel = recursionLevels.shift(); }
      state = next;
      if (state) {
        var add = forRecursion.map(function (x) { return x.content; }).join("\n");
        if (add) { recurseBuffer.push(add); allText = add + "\n" + allText; }
      }
    }
    var before = [], after = [], em = [], anTop = [], anBottom = [], depthEntries = [], outlets = {};
    Array.from(activated.values()).sort(sortFn).forEach(function (e) {
      var content = e.content;
      if (!content) return;
      switch (e.position) {
        case WI_POS.before: before.unshift(content); break;
        case WI_POS.after: after.unshift(content); break;
        case WI_POS.EMTop: em.unshift({ position: 0, content: content }); break;
        case WI_POS.EMBottom: em.unshift({ position: 1, content: content }); break;
        case WI_POS.ANTop: anTop.unshift(content); break;
        case WI_POS.ANBottom: anBottom.unshift(content); break;
        case WI_POS.atDepth: {
          var d = e.depth === undefined || e.depth === null ? 4 : e.depth;
          var role = e.role === undefined || e.role === null ? 0 : e.role;
          var existing = depthEntries.find(function (x) { return x.depth === d && x.role === role; });
          if (existing) existing.entries.unshift(content);
          else depthEntries.push({ depth: d, entries: [content], role: role });
          break;
        }
        case WI_POS.outlet:
          if (e.outletName) (outlets[e.outletName] = outlets[e.outletName] || []).push(content);
          break;
        default: break;
      }
    });
    if (!dry) {
      Array.from(activated.values()).forEach(function (e) {
        ["sticky", "cooldown"].forEach(function (type) {
          if (!e[type]) return;
          var k = keyOf(e);
          if (timed[type][k]) return;
          if (type === "cooldown" && e.sticky) return;
          timed[type][k] = effect(type, e, false);
        });
      });
    }
    return {
      worldInfoBefore: before.join("\n"), worldInfoAfter: after.join("\n"), EMEntries: em,
      WIDepthEntries: depthEntries, ANBeforeEntries: anTop, ANAfterEntries: anBottom, outletEntries: outlets,
      activated: Array.from(activated.values()).map(function (e) {
        return { world: e.worldName, uid: e.uid, comment: e.comment, position: e.position, tokens: estimateTokens(e.content) };
      }),
      budget: budget, overflowed: overflowed
    };
  }

  /* ------------------------------------------------------------ presets */
  var PRESET_DEFAULTS = {
    temperature: 1, frequency_penalty: 0, presence_penalty: 0, top_p: 1, top_k: 0, top_a: 0, min_p: 0,
    repetition_penalty: 1, openai_max_context: 32000, openai_max_tokens: 2048, seed: -1, names_behavior: 0,
    send_if_empty: "", impersonation_prompt: "[Write your next reply from the point of view of {{user}}, using the chat history so far as a guideline for the writing style of {{user}}. Don't write as {{char}} or system. Don't describe actions of {{char}}.]",
    new_chat_prompt: "[Start a new Chat]", new_group_chat_prompt: "[Start a new group chat. Group members: {{group}}]",
    new_example_chat_prompt: "[Example Chat]", continue_nudge_prompt: "[Continue your last message without repeating its original content.]",
    wi_format: "{0}", scenario_format: "{{scenario}}", personality_format: "{{personality}}",
    group_nudge_prompt: "[Write the next reply only as {{char}}.]", assistant_prefill: "", assistant_impersonation: "",
    squash_system_messages: false, continue_prefill: false, continue_postfix: " ", show_thoughts: true,
    reasoning_effort: "auto", wrap_in_quotes: false, stream_openai: true, custom_prompt_post_processing: "",
    prompts: null, prompt_order: null
  };
  var DEFAULT_PROMPTS = [
    { name: "Main Prompt", system_prompt: true, role: "system", content: "Write {{char}}'s next reply in a fictional chat between {{charIfNotGroup}} and {{user}}.", identifier: "main" },
    { name: "Auxiliary Prompt", system_prompt: true, role: "system", content: "", identifier: "nsfw" },
    { identifier: "dialogueExamples", name: "Chat Examples", system_prompt: true, marker: true },
    { name: "Post-History Instructions", system_prompt: true, role: "system", content: "", identifier: "jailbreak" },
    { identifier: "chatHistory", name: "Chat History", system_prompt: true, marker: true },
    { identifier: "worldInfoAfter", name: "World Info (after)", system_prompt: true, marker: true },
    { identifier: "worldInfoBefore", name: "World Info (before)", system_prompt: true, marker: true },
    { identifier: "enhanceDefinitions", role: "system", name: "Enhance Definitions", content: "If you have more knowledge of {{char}}, add to the character's lore and personality to enhance them but keep the Character Sheet's definitions absolute.", system_prompt: true, marker: false },
    { identifier: "charDescription", name: "Char Description", system_prompt: true, marker: true },
    { identifier: "charPersonality", name: "Char Personality", system_prompt: true, marker: true },
    { identifier: "scenario", name: "Scenario", system_prompt: true, marker: true },
    { identifier: "personaDescription", name: "Persona Description", system_prompt: true, marker: true }
  ];
  var DEFAULT_ORDER = [
    { identifier: "main", enabled: true }, { identifier: "worldInfoBefore", enabled: true },
    { identifier: "personaDescription", enabled: true }, { identifier: "charDescription", enabled: true },
    { identifier: "charPersonality", enabled: true }, { identifier: "scenario", enabled: true },
    { identifier: "enhanceDefinitions", enabled: false }, { identifier: "nsfw", enabled: true },
    { identifier: "worldInfoAfter", enabled: true }, { identifier: "dialogueExamples", enabled: true },
    { identifier: "chatHistory", enabled: true }, { identifier: "jailbreak", enabled: true }
  ];
  var MARKERS = ["worldInfoBefore", "worldInfoAfter", "charDescription", "charPersonality", "scenario",
    "personaDescription", "dialogueExamples", "chatHistory"];
  var PRESET_KEYS = Object.keys(PRESET_DEFAULTS).filter(function (k) { return k !== "prompts" && k !== "prompt_order"; });

  function normalizePreset(raw) {
    var p = Object.assign({}, PRESET_DEFAULTS, raw || {});
    var prompts = Array.isArray(raw && raw.prompts) && raw.prompts.length ? raw.prompts : DEFAULT_PROMPTS;
    p.prompts = prompts.filter(function (x) { return x && x.identifier; }).map(function (x) {
      var q = Object.assign({}, x);
      q.identifier = str(q.identifier); q.name = str(q.name || q.identifier);
      if (MARKERS.indexOf(q.identifier) >= 0) q.marker = true;
      if (q.role === undefined && !q.marker) q.role = "system";
      if (q.injection_position === undefined) q.injection_position = 0;
      if (q.injection_depth === undefined) q.injection_depth = 4;
      return q;
    });
    // Every built-in marker must exist, like the ST prompt manager guarantees.
    DEFAULT_PROMPTS.forEach(function (d) {
      if (!p.prompts.some(function (x) { return x.identifier === d.identifier; })) p.prompts.push(clone(d));
    });
    var order = null;
    if (Array.isArray(raw && raw.prompt_order)) {
      var global = raw.prompt_order.find(function (o) { return String(o.character_id) === "100001"; })
        || raw.prompt_order.find(function (o) { return String(o.character_id) === "100000"; }) || raw.prompt_order[0];
      if (global && Array.isArray(global.order)) order = global.order;
    }
    if (!order) order = DEFAULT_ORDER;
    p.prompt_order = [{ character_id: 100001, order: order.filter(function (o) { return o && o.identifier; })
      .map(function (o) { return { identifier: str(o.identifier), enabled: o.enabled !== false }; }) }];
    return p;
  }
  function presetOrder(preset) { return preset.prompt_order[0].order; }
  function exportPreset(preset) {
    var out = {};
    PRESET_KEYS.forEach(function (k) { if (preset[k] !== undefined) out[k] = preset[k]; });
    out.prompts = clone(preset.prompts);
    out.prompt_order = [{ character_id: 100000, order: clone(presetOrder(preset)) }, { character_id: 100001, order: clone(presetOrder(preset)) }];
    out.extensions = {};
    return out;
  }

  /* ------------------------------------------------------------ examples */
  function parseMesExamples(text) {
    var s = str(text);
    if (!s || s === "<START>") return [];
    if (!/^<START>/i.test(s)) s = "<START>\n" + s.trim();
    return s.split(/<START>/gi).slice(1).map(function (block) { return "<START>\n" + block.trim() + "\n"; });
  }
  function parseExampleIntoIndividual(text, name1, name2) {
    var result = [], lines = str(text).split("\n"), cur = [], inUser = false, inBot = false;
    function add(name, system) {
      var parsed = cur.join("\n").replace(name + ":", "").trim();
      result.push({ role: "system", content: parsed, name: system });
      cur = [];
    }
    for (var i = 1; i < lines.length; i++) {
      var line = lines[i];
      if (line.indexOf(name1 + ":") === 0) { inUser = true; if (inBot) add(name2, "example_assistant"); inBot = false; }
      else if (line.indexOf(name2 + ":") === 0) { inBot = true; if (inUser) add(name1, "example_user"); inUser = false; }
      cur.push(line);
    }
    if (inUser) add(name1, "example_user"); else if (inBot) add(name2, "example_assistant");
    return result;
  }

  /* ------------------------------------------------------------ prompt builder */
  function stringFormat(format, value) { return str(format).replace(/\{0\}/g, value); }

  /** Build the Chat Completion message array exactly in SillyTavern's order.
   * opts: { type, preset, char (card data), charName, user (persona name), persona {description, position, depth, role},
   *  chat: [{is_user, is_system, name, mes, date, hidden}], worlds, wiSettings, meta, note {text, position, depth, role, interval},
   *  settings: {preferCharPrompt, preferCharJailbreak, pinExamples, nameField, postProcessing, sendPrefill, maxContext, modelContext},
   *  globals, lang, chatId, model, quietPrompt, regexPrompt(fn(text, kind, depth)) } */
  function buildPrompt(opts) {
    var preset = normalizePreset(opts.preset);
    var type = opts.type || "normal";
    var s = opts.settings || {};
    // «Send the prefill» off = the model can't start its reply from an assistant message, so «Continue» can't
    // either: use the continue nudge instead (phase 2: DeepSeek via an OpenAI-compatible API treated the trailing
    // assistant message as a finished turn and replied with the greeting again).
    var continuePrefill = !!preset.continue_prefill && s.sendPrefill !== false;
    var card = opts.char || {};
    var name1 = opts.user || "User", name2 = opts.charName || card.name || "Character";
    var meta = opts.meta || {};
    meta.variables = meta.variables || {};
    var maxContext = num(preset.openai_max_context, 32000);
    if (s.modelContext && s.clampContext !== false) maxContext = Math.min(maxContext, num(s.modelContext, maxContext));
    var maxTokens = num(preset.openai_max_tokens, 2048);
    var persona = opts.persona || {};
    var warnings = [];

    // coreChat: hidden/system messages are not sent
    var coreChat = (opts.chat || []).filter(function (m) { return !m.is_system && !m.hidden; });
    var isContinue = type === "continue";
    var cyclePrompt = "";
    if (isContinue && coreChat.length) {
      var lastMsg = coreChat[coreChat.length - 1];
      cyclePrompt = str(lastMsg.mes) + str(preset.continue_postfix);
      coreChat = coreChat.slice(0, -1).concat([Object.assign({}, lastMsg, { mes: cyclePrompt })]);
    }
    if (typeof opts.regexPrompt === "function") {
      coreChat = coreChat.map(function (m, index) {
        var depth = coreChat.length - index - (isContinue ? 2 : 1);
        return Object.assign({}, m, { mes: opts.regexPrompt(str(m.mes), m.is_user ? "user" : "ai", depth) });
      });
    }
    var charPrompt = card.system_prompt || "";
    var charJailbreak = card.post_history_instructions || "";
    var ext = card.extensions || {};
    var depthPrompt = ext.depth_prompt || {};
    var macroCtx = {
      user: name1, char: name2, group: name2, persona: persona.description || "",
      description: card.description || "", personality: card.personality || "", scenario: card.scenario || "",
      mesExamplesRaw: card.mes_example || "", charPrompt: charPrompt, charJailbreak: charJailbreak,
      charDepthPrompt: depthPrompt.prompt || "", creatorNotes: card.creator_notes || "",
      charVersion: card.character_version || "", model: opts.model || "", variables: meta.variables,
      globals: opts.globals || {}, chat: opts.chat || [], chatId: opts.chatId || "", lang: opts.lang || "en",
      maxContext: maxContext, maxResponse: maxTokens, maxPrompt: maxContext - maxTokens, input: opts.input || ""
    };
    macroCtx.mesExamples = parseMesExamples(card.mes_example).join("");
    function sub(text, extra) {
      var ctx = macroCtx;
      if (extra) { ctx = Object.assign({}, macroCtx, extra); ctx.variables = meta.variables; ctx.globals = macroCtx.globals; }
      return substitute(text, ctx);
    }

    // World Info scan (newest first)
    var chatForWI = coreChat.slice().reverse().map(function (m) {
      return s.wiIncludeNames === false ? str(m.mes) : str(m.name || (m.is_user ? name1 : name2)) + ": " + str(m.mes);
    });
    var wiInjects = [];
    var wi = checkWorldInfo({
      chatLines: chatForWI, worlds: opts.worlds || [], settings: opts.wiSettings, maxContext: maxContext,
      meta: meta, chatLength: (opts.chat || []).length, trigger: type === "regenerate" ? "regenerate" : type,
      globalScan: { personaDescription: persona.description || "", characterDescription: card.description || "",
        characterPersonality: card.personality || "", characterDepthPrompt: depthPrompt.prompt || "",
        scenario: card.scenario || "", creatorNotes: card.creator_notes || "" },
      injects: wiInjects, charName: name2, charTags: card.tags || [], subst: function (x) { return sub(x); },
      dryRun: !!opts.dryRun
    });

    // Example messages (+ EM entries)
    var mesExamplesArray = parseMesExamples(sub(card.mes_example || ""));
    wi.EMEntries.forEach(function (entry) {
      var block = "<START>\n" + entry.content.trim() + "\n";
      if (entry.position === 0) mesExamplesArray.unshift(block); else mesExamplesArray.push(block);
    });
    var messageExamples = mesExamplesArray.map(function (item) {
      return parseExampleIntoIndividual(item.replace(/<START>/i, "{Example Dialogue:}").replace(/\r/g, ""), name1, name2);
    });

    // Extension prompts: key -> {value, position (0 in_prompt,1 in_chat,2 before), depth, role}
    var extensionPrompts = {};
    var note = opts.note || {};
    var userCount = (opts.chat || []).filter(function (m) { return m.is_user; }).length;
    var interval = num(note.interval, 1);
    var noteActive = interval > 0 && (interval === 1 || (userCount > 0 && userCount % interval === 0)) && note.position !== "off";
    var noteText = noteActive ? str(note.text) : "";
    if (noteActive && (wi.ANBeforeEntries.length || wi.ANAfterEntries.length)) {
      noteText = (wi.ANBeforeEntries.join("\n") + "\n" + noteText + "\n" + wi.ANAfterEntries.join("\n")).replace(/(^\n)|(\n$)/g, "");
    }
    var pdPos = num(persona.position, 0);
    if (persona.description && noteActive && (pdPos === 2 || pdPos === 3)) {
      noteText = pdPos === 2 ? persona.description + "\n" + noteText : noteText + "\n" + persona.description;
    }
    var notePos = note.position === "before" ? 2 : note.position === "after" ? 0 : 1;
    if (noteText.trim()) extensionPrompts["2_floating_prompt"] = { value: noteText, position: notePos, depth: num(note.depth, 4), role: num(note.role, 0) };
    if (depthPrompt.prompt) extensionPrompts.DEPTH_PROMPT = { value: depthPrompt.prompt, position: 1, depth: num(depthPrompt.depth, 4), role: ROLE_NAMES.indexOf(depthPrompt.role || "system") < 0 ? 0 : ROLE_NAMES.indexOf(depthPrompt.role || "system") };
    if (persona.description && pdPos === 4) extensionPrompts.PERSONA_DESCRIPTION = { value: persona.description, position: 1, depth: num(persona.depth, 2), role: num(persona.role, 0) };
    wi.WIDepthEntries.forEach(function (entry) {
      extensionPrompts["customDepthWI_" + entry.depth + "_" + entry.role] = { value: entry.entries.join("\n"), position: 1, depth: entry.depth, role: entry.role };
    });
    function getExtensionPrompt(position, depth, role) {
      return Object.keys(extensionPrompts).sort().map(function (k) { return extensionPrompts[k]; })
        .filter(function (x) { return x.position === position && x.value; })
        .filter(function (x) { return depth === undefined || x.depth === depth; })
        .filter(function (x) { return role === undefined || x.role === role; })
        .map(function (x) { return sub(x.value.trim()); }).join("\n");
    }

    // ---------- PromptManager collection
    var promptById = {};
    preset.prompts.forEach(function (p) { promptById[p.identifier] = p; });
    var collection = [];
    function prepare(p, original) {
      var q = Object.assign({}, p);
      q.content = sub(p.content || "", typeof original === "string" ? { original: original } : null);
      return q;
    }
    presetOrder(preset).forEach(function (entry) {
      var p = promptById[entry.identifier];
      if (!p) return;
      var triggers = Array.isArray(p.injection_trigger) && p.injection_trigger.length ? p.injection_trigger : null;
      var allowed = entry.enabled && (!triggers || triggers.indexOf(type === "regenerate" ? "regenerate" : type) >= 0);
      if (allowed) collection.push(prepare(p));
      else if (entry.identifier === "main") { var r = clone(p); r.content = ""; r._disabled = true; collection.push(prepare(r)); }
    });
    function cIndex(id) { for (var i = 0; i < collection.length; i++) if (collection[i].identifier === id) return i; return -1; }
    function cGet(id) { var i = cIndex(id); return i >= 0 ? collection[i] : null; }
    var orderEnabled = {};
    presetOrder(preset).forEach(function (o) { orderEnabled[o.identifier] = o.enabled; });

    var scenarioText = card.scenario && preset.scenario_format ? sub(preset.scenario_format) : (card.scenario || "");
    var personalityText = card.personality && preset.personality_format ? sub(preset.personality_format) : (card.personality || "");
    function formatWI(value) { if (!value) return ""; return str(preset.wi_format).trim() ? stringFormat(preset.wi_format, value) : value; }
    var systemPrompts = [
      { role: "system", content: formatWI(wi.worldInfoBefore), identifier: "worldInfoBefore" },
      { role: "system", content: formatWI(wi.worldInfoAfter), identifier: "worldInfoAfter" },
      { role: "system", content: card.description || "", identifier: "charDescription" },
      { role: "system", content: personalityText, identifier: "charPersonality" },
      { role: "system", content: scenarioText, identifier: "scenario" },
      { role: "system", content: preset.impersonation_prompt ? sub(preset.impersonation_prompt) : "", identifier: "impersonate" },
      { role: "system", content: opts.quietPrompt || "", identifier: "quietPrompt" }
    ];
    if (extensionPrompts["2_floating_prompt"] && extensionPrompts["2_floating_prompt"].position !== 1) {
      var an = extensionPrompts["2_floating_prompt"];
      systemPrompts.push({ role: ROLE_NAMES[an.role] || "system", content: an.value, identifier: "authorsNote", position: an.position === 2 ? "start" : "end" });
    }
    if (persona.description && pdPos === 0) systemPrompts.push({ role: "system", content: persona.description, identifier: "personaDescription" });
    systemPrompts.forEach(function (p) {
      var existing = cGet(p.identifier);
      if (existing) {
        p.injection_position = existing.injection_position !== undefined ? existing.injection_position : p.injection_position;
        p.injection_depth = existing.injection_depth !== undefined ? existing.injection_depth : p.injection_depth;
        p.injection_order = existing.injection_order !== undefined ? existing.injection_order : p.injection_order;
        p.role = existing.role || p.role;
        p.name = existing.name;
      }
      var prepared = prepare(p);
      var at = cIndex(p.identifier);
      if (at >= 0) collection[at] = prepared; else collection.push(prepared);
    });
    var mainPrompt = cGet("main");
    if (charPrompt && mainPrompt && mainPrompt.forbid_overrides !== true && orderEnabled.main && s.preferCharPrompt !== false) {
      var mainOriginal = promptById.main ? str(promptById.main.content) : "";
      collection[cIndex("main")] = prepare(Object.assign({}, mainPrompt, { content: charPrompt }), sub(mainOriginal));
    }
    var jb = cGet("jailbreak");
    if (charJailbreak && jb && jb.forbid_overrides !== true && orderEnabled.jailbreak && s.preferCharJailbreak !== false) {
      var jbOriginal = promptById.jailbreak ? str(promptById.jailbreak.content) : "";
      collection[cIndex("jailbreak")] = prepare(Object.assign({}, jb, { content: charJailbreak }), sub(jbOriginal));
    }

    // ---------- ChatCompletion with token budget
    var budget = maxContext - maxTokens;
    var slots = [];      // index -> {identifier, messages: []}
    var tail = [];       // control prompts
    var overBudget = false;
    function spend(tokens) { budget -= tokens; if (budget < 0) overBudget = true; }
    function makeMessage(role, content, identifier, name) {
      var m = { role: role || "system", content: str(content), identifier: identifier };
      if (name) m.name = name;
      m.tokens = messageTokens(m);
      return m;
    }
    function addCollection(identifier, messages, index) {
      var slot = { identifier: identifier, messages: messages || [] };
      slot.messages.forEach(function (m) { if (m.content) spend(m.tokens); });
      if (index !== null && index !== undefined && index >= 0) slots[index] = slot; else slots.push(slot);
      return slot;
    }
    function slotOf(identifier) { for (var i = 0; i < slots.length; i++) if (slots[i] && slots[i].identifier === identifier) return slots[i]; return null; }
    function canAfford(messages) { var t = 0; messages.forEach(function (m) { t += m.tokens; }); return budget - t >= 0; }
    function addToCompletion(source) {
      var p = cGet(source);
      if (!p) return;
      if (orderEnabled[source] === false && source !== "main") return;
      if (num(p.injection_position, 0) === 1) return;
      addCollection(source, [makeMessage(p.role, p.content, source)], cIndex(source));
    }
    spend(3);
    ["worldInfoBefore", "main", "worldInfoAfter", "charDescription", "charPersonality", "scenario", "personaDescription"].forEach(addToCompletion);
    var controlPrompts = [];
    if (type === "impersonate") { var imp = cGet("impersonate"); if (imp && imp.content) controlPrompts.push(makeMessage(imp.role, imp.content, "impersonate")); }
    var quiet = cGet("quietPrompt");
    if (quiet && quiet.content) controlPrompts.push(makeMessage(quiet.role, quiet.content, "quietPrompt"));
    controlPrompts.forEach(function (m) { spend(m.tokens); });
    var userRelative = collection.filter(function (p) { return p.system_prompt === false && num(p.injection_position, 0) !== 1; })
      .map(function (p) { return p.identifier; });
    var absolutePrompts = collection.filter(function (p) { return num(p.injection_position, 0) === 1 && p.content; });
    ["nsfw", "jailbreak"].concat(userRelative).forEach(addToCompletion);
    if (cGet("enhanceDefinitions")) addToCompletion("enhanceDefinitions");
    // relative extension prompts (Author's Note before/after main)
    var anPrompt = cGet("authorsNote");
    if (anPrompt && anPrompt.position) {
      var mainSlot = slotOf("main");
      var anMsg = makeMessage(anPrompt.role, anPrompt.content, "authorsNote");
      if (mainSlot) {
        if (anPrompt.position === "start") mainSlot.messages.unshift(anMsg); else mainSlot.messages.push(anMsg);
        spend(anMsg.tokens);
      } else {
        var mainAbs = absolutePrompts.findIndex(function (p) { return p.identifier === "main"; });
        if (mainAbs >= 0) {
          var mainP = absolutePrompts[mainAbs];
          absolutePrompts.splice(anPrompt.position === "end" ? mainAbs + 1 : mainAbs, 0,
            Object.assign({}, anPrompt, { role: mainP.role, injection_position: 1, injection_depth: mainP.injection_depth, injection_order: mainP.injection_order }));
        }
      }
    }

    // chat messages (newest first) in the ST shape
    var messages = [];
    for (var ci = coreChat.length - 1; ci >= 0; ci--) {
      var m = coreChat[ci];
      var role = m.is_user ? "user" : (m.is_narrator ? "system" : "assistant");
      var content = str(m.mes).replace(/\r/g, "");
      var nb = num(preset.names_behavior, 0);
      if (nb === 2 && !m.is_narrator) content = str(m.name || (m.is_user ? name1 : name2)) + ": " + content;
      else if (nb === 0 && m.force_avatar && m.name !== name1) content = str(m.name) + ": " + content;
      var row = { role: role, content: content, name: m.name || (m.is_user ? name1 : name2), media: m.images || null };
      if (s.sendReasoning && m.reasoning && !m.is_user) row.reasoning = m.reasoning;
      messages.push(row);
    }
    if (type === "continue" && continuePrefill && messages.length) {
      var cm = messages.shift();
      var cmMsg = makeMessage(cm.role, cm.content, "continuePrefill");
      controlPrompts.push(cmMsg);
      spend(cmMsg.tokens);
    }
    // in-chat injections (absolute prompts + extension prompts at depth)
    var maxDepth = 0;
    absolutePrompts.forEach(function (p) { maxDepth = Math.max(maxDepth, num(p.injection_depth, 4)); });
    Object.keys(extensionPrompts).forEach(function (k) { if (extensionPrompts[k].position === 1) maxDepth = Math.max(maxDepth, extensionPrompts[k].depth); });
    var inserted = 0;
    for (var depth = 0; depth <= maxDepth; depth++) {
      var depthPrompts = absolutePrompts.filter(function (p) { return num(p.injection_depth, 4) === depth && p.content; });
      var roleMessages = [];
      var orderGroups = { "100": [] };
      depthPrompts.forEach(function (p) { var o = String(p.injection_order === undefined ? 100 : p.injection_order); (orderGroups[o] = orderGroups[o] || []).push(p); });
      Object.keys(orderGroups).sort(function (a, b) { return +b - +a; }).forEach(function (order) {
        ROLE_NAMES.forEach(function (roleName, roleIndex) {
          var rolePrompts = orderGroups[order].filter(function (p) { return (p.role || "system") === roleName; }).map(function (p) { return p.content; }).join("\n");
          var extPrompt = order === "100" ? getExtensionPrompt(1, depth, roleIndex) : "";
          var joint = [rolePrompts, extPrompt].filter(Boolean).map(function (x) { return x.trim(); }).join("\n");
          if (joint) roleMessages.push({ role: roleName, content: joint, injected: true });
        });
      });
      if (roleMessages.length) {
        Array.prototype.splice.apply(messages, [depth + inserted, 0].concat(roleMessages));
        inserted += roleMessages.length;
      }
    }
    messages = messages.reverse();

    function populateHistory() {
      if (cIndex("chatHistory") < 0) return;
      var slot = addCollection("chatHistory", [], cIndex("chatHistory"));
      var newChatMsg = makeMessage("system", sub(preset.new_chat_prompt), "newMainChat");
      spend(newChatMsg.tokens);
      var continueMsgs = null;
      if (type === "continue" && cyclePrompt && !continuePrefill) {
        continueMsgs = [];
        var lastIdx = -1;
        for (var q = messages.length - 1; q >= 0; q--) if (!messages[q].injected) { lastIdx = q; break; }
        if (lastIdx >= 0) {
          var cmsg = messages.splice(lastIdx, 1)[0];
          continueMsgs.push(makeMessage(cmsg.role, sub(cmsg.content), "continueMessage"));
        }
        continueMsgs.push(makeMessage("system", sub(preset.continue_nudge_prompt, { extra: { lastChatMessage: str(cyclePrompt).trim() } }), "continueNudge"));
        continueMsgs.forEach(function (x) { spend(x.tokens); });
      }
      var last = messages[messages.length - 1];
      if (last && last.role === "assistant" && preset.send_if_empty) {
        var emptyMsg = makeMessage("user", preset.send_if_empty, "emptyUserMessageReplacement");
        if (canAfford([emptyMsg])) { slot.messages.push(emptyMsg); spend(emptyMsg.tokens); }
      }
      var pool = messages.slice().reverse();
      for (var h = 0; h < pool.length; h++) {
        var item = pool[h];
        var nameField = num(preset.names_behavior, 0) === 1 && item.name ? sanitizeName(item.name) : "";
        var content2 = sub(item.content);
        if (Array.isArray(item.media) && item.media.length && s.inlineImages !== false) {
          content2 = [{ type: "text", text: content2 }].concat(item.media.map(function (url) { return { type: "image_url", image_url: { url: url } }; }));
        }
        var msg = makeMessage(item.role, "", "chatHistory-" + (messages.length - h), nameField);
        msg.content = content2;
        msg.tokens = messageTokens(msg);
        if (item.injected) msg.injected = true;
        if (canAfford([msg])) { slot.messages.unshift(msg); spend(msg.tokens); }
        else { warnings.push("history_trimmed"); break; }
      }
      budget += newChatMsg.tokens;
      if (newChatMsg.content) { slot.messages.unshift(newChatMsg); spend(newChatMsg.tokens); }
      if (continueMsgs) {
        continueMsgs.forEach(function (x) { budget += x.tokens; });
        addCollection("continueNudge", continueMsgs, null);
      }
    }
    function populateExamples() {
      if (cIndex("dialogueExamples") < 0) return;
      var slot = addCollection("dialogueExamples", [], cIndex("dialogueExamples"));
      if (!messageExamples.length) return;
      var newExample = makeMessage("system", sub(preset.new_example_chat_prompt), "newChat");
      for (var d = 0; d < messageExamples.length; d++) {
        var block = messageExamples[d].map(function (p, n) { return makeMessage("system", p.content || "", "dialogueExamples " + d + "-" + n, p.name); });
        if (!canAfford([newExample].concat(block))) { warnings.push("examples_trimmed"); break; }
        if (newExample.content) { slot.messages.push(newExample); spend(newExample.tokens); }
        block.forEach(function (x) { if (x.content) { slot.messages.push(x); spend(x.tokens); } });
      }
    }
    if (s.pinExamples) { populateExamples(); populateHistory(); } else { populateHistory(); populateExamples(); }
    controlPrompts.forEach(function (m) { budget += m.tokens; });
    if (controlPrompts.length) addCollection("controlPrompts", controlPrompts, null);

    // flatten in collection order
    var flat = [];
    var breakdown = [];
    slots.forEach(function (slot) {
      if (!slot) return;
      var t = 0;
      slot.messages.forEach(function (m) { if (m.content && (typeof m.content !== "string" || m.content.length)) { flat.push(m); t += m.tokens; } });
      var src = promptById[slot.identifier];
      breakdown.push({ identifier: slot.identifier, name: src ? src.name : slot.identifier, tokens: t, count: slot.messages.length });
    });
    // squash system messages
    if (preset.squash_system_messages) {
      var exclude = ["newMainChat", "newChat", "groupNudge"];
      var squashed = [], lastSys = null;
      flat.forEach(function (m) {
        if (m.role === "system" && !m.content) return;
        var should = function (x) { return exclude.indexOf(x.identifier) < 0 && x.role === "system" && !x.name && typeof x.content === "string"; };
        if (should(m)) {
          if (lastSys && should(lastSys)) { lastSys.content += "\n" + m.content; lastSys.tokens = messageTokens(lastSys); }
          else { var c = Object.assign({}, m); squashed.push(c); lastSys = c; }
        } else { squashed.push(m); lastSys = m; }
      });
      flat = squashed;
    }
    // names: keep the name field only when asked, otherwise prefix the content (most providers drop `name`)
    var out = flat.map(function (m) {
      var row = { role: m.role, content: m.content };
      if (m.name) {
        var display = m.name === "example_user" ? name1 : m.name === "example_assistant" ? name2 : m.name;
        if (s.nameField) row.name = sanitizeName(m.name);
        else if (typeof row.content === "string" && row.content.indexOf(display + ":") !== 0) row.content = display + ": " + row.content;
      }
      row._id = m.identifier;
      return row;
    });
    // Prompts that end up as nothing but whitespace (toggle blocks of big presets: «\n{{// …}}\n{{trim}}») carry no
    // text; phase 2: «Engine» sent 56 such system messages, and strict providers reject blank messages.
    out = out.filter(function (m) { return typeof m.content !== "string" || /\S/.test(m.content) || m.tool_calls; });
    out = postProcess(out, s.postProcessing === "preset" || !s.postProcessing ? str(preset.custom_prompt_post_processing) : s.postProcessing, name1);
    // assistant prefill
    var prefill = "";
    if (type !== "quiet" && !(type === "continue" && continuePrefill) && s.sendPrefill !== false) {
      prefill = type === "impersonate" ? sub(preset.assistant_impersonation) : sub(preset.assistant_prefill);
      if (prefill) out.push({ role: "assistant", content: prefill, _id: "prefill" });
    }
    var total = 0;
    out.forEach(function (m) { total += messageTokens(m); });
    if (overBudget) warnings.push("over_budget");
    return {
      messages: out, tokens: total + 3, breakdown: breakdown, warnings: warnings, prefill: prefill,
      continueText: isContinue ? cyclePrompt : "", maxContext: maxContext, maxTokens: maxTokens,
      worldInfo: wi.activated, wiBudget: wi.budget, wiOverflow: wi.overflowed, variables: meta.variables,
      params: samplerParams(preset, s)
    };
  }
  function sanitizeName(name) { return str(name).normalize("NFD").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64) || "user"; }

  function postProcess(messages, mode, userName) {
    mode = str(mode);
    if (!mode || mode === "none") return messages;
    var list = messages.map(function (m) { return Object.assign({}, m); });
    function text(m) { return typeof m.content === "string" ? m.content : ""; }
    function merge(arr) {
      var out = [];
      arr.forEach(function (m) {
        var prev = out[out.length - 1];
        if (prev && prev.role === m.role && typeof prev.content === "string" && typeof m.content === "string" && !prev.name && !m.name) {
          prev.content = prev.content + "\n\n" + m.content;
        } else out.push(m);
      });
      return out;
    }
    if (mode === "single") {
      return [{ role: "user", content: list.map(function (m) { return text(m); }).filter(Boolean).join("\n\n") }];
    }
    if (mode === "semi" || mode === "strict") {
      var seenNonSystem = false;
      list.forEach(function (m) {
        if (m.role !== "system") seenNonSystem = true;
        else if (seenNonSystem) m.role = "user";
      });
      list = merge(list);
      if (mode === "strict") {
        var firstNon = list.findIndex(function (m) { return m.role !== "system"; });
        if (firstNon >= 0 && list[firstNon].role === "assistant") list.splice(firstNon, 0, { role: "user", content: "[Start a new chat]" });
        if (firstNon < 0) list.push({ role: "user", content: "[Start a new chat]" });
        list = merge(list);
      }
      return list;
    }
    return merge(list);
  }

  function samplerParams(preset, s) {
    var effort = str(preset.reasoning_effort);
    var map = { min: "minimal", minimum: "minimal", minimal: "minimal", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max", maximum: "max" };
    var params = {
      temperature: num(preset.temperature, 1), top_p: num(preset.top_p, 1),
      max_tokens: num(preset.openai_max_tokens, 2048), prompt_caching: s.promptCaching !== false
    };
    // OpenAI-standard penalties are always passed (the bridge drops neutral values); the non-standard
    // samplers only when asked, like SillyTavern sends them only to backends that understand them.
    params.frequency_penalty = num(preset.frequency_penalty, 0); params.presence_penalty = num(preset.presence_penalty, 0);
    if (s.extendedSamplers) {
      params.top_k = num(preset.top_k, 0); params.min_p = num(preset.min_p, 0); params.top_a = num(preset.top_a, 0);
      params.repetition_penalty = num(preset.repetition_penalty, 1);
    }
    if (num(preset.seed, -1) >= 0) params.seed = num(preset.seed, -1);
    if (Array.isArray(s.stop) && s.stop.length) params.stop = s.stop;
    if (map[effort]) params.effort = map[effort];
    var thinking = str(s.thinking || "auto");
    params.thinking = thinking === "on" ? true : thinking === "off" ? false : !!map[effort] && !!preset.show_thoughts;
    // r33c: «No reasoning» must reach the Proxy as a level — thinking:false alone lets the model decide; the Proxy
    // clamps «off» to the lightest level for a model that cannot switch reasoning off (app/reasoning_levels.py).
    if (thinking === "off") params.effort = "off";
    return params;
  }

  /* ------------------------------------------------------------ leaked control tokens */
  // A provider sometimes returns a model's special tokens as plain text and the model then rambles on as if a new
  // document began. Index of the first such token, or -1. DeepSeek <｜begin▁of▁sentence｜>/<｜end▁of▁sentence｜>,
  // ChatML <|im_end|>/<|im_start|>, Llama <|eot_id|>/<|start_header_id|>, GPT <|endoftext|>, harmony <|end|>/<|return|>/<|start|>/<|channel|>.
  var LEAK_RE = /<[｜|]\s*(?:begin|end)[▁_ ]of[▁_ ]sentence\s*[｜|]>|<\|(?:endoftext|end_of_text|im_end|im_start|eot_id|start_header_id|end_header_id|end|return|start|channel|message)\|>/;
  function findLeakedToken(text) {
    var m = LEAK_RE.exec(str(text));
    return m ? m.index : -1;
  }

  /* ------------------------------------------------------------ reasoning parse */
  function splitReasoning(text, prefix, suffix) {
    var t = str(text);
    prefix = prefix || "<think>"; suffix = suffix || "</think>";
    var lead = t.replace(/^\s+/, "");
    if (lead.indexOf(prefix) !== 0) return { text: t, reasoning: "", open: false };
    var rest = lead.slice(prefix.length);
    var end = rest.indexOf(suffix);
    if (end < 0) return { text: "", reasoning: rest.replace(/^\n+/, ""), open: true };
    return { text: rest.slice(end + suffix.length).replace(/^\s+/, ""), reasoning: rest.slice(0, end).trim(), open: false };
  }

  /* ------------------------------------------------------------ regex scripts */
  function runRegexScripts(text, scripts, placement, opts) {
    var out = str(text);
    opts = opts || {};
    (scripts || []).forEach(function (script) {
      if (!script || script.disabled || !script.findRegex) return;
      if ((script.placement || []).indexOf(placement) < 0) return;
      if (opts.isMarkdown && !script.markdownOnly) return;
      if (opts.isPrompt && !script.promptOnly) return;
      if (!opts.isMarkdown && !opts.isPrompt && (script.markdownOnly || script.promptOnly)) return;
      if (opts.depth !== undefined && opts.depth !== null) {
        if (script.minDepth !== null && script.minDepth !== undefined && script.minDepth !== "" && opts.depth < num(script.minDepth, 0)) return;
        if (script.maxDepth !== null && script.maxDepth !== undefined && script.maxDepth !== "" && opts.depth > num(script.maxDepth, 0)) return;
      }
      var rx = parseRegexFromString(script.findRegex);
      if (!rx) { try { rx = new RegExp(escapeRegex(script.findRegex), "g"); } catch (e) { return; } }
      out = out.replace(rx, function () {
        var args = arguments;
        var match = args[0];
        var replaced = str(script.replaceString).replace(/{{match}}/gi, "$0").replace(/\$(\d+)|\$0/g, function (m, n) {
          var value = n === undefined ? match : args[Number(n)];
          var v = str(value);
          (script.trimStrings || []).forEach(function (trim) { if (trim) v = v.split(trim).join(""); });
          return v;
        });
        return replaced;
      });
    });
    return out;
  }

  /* ------------------------------------------------------------ cards */
  var CRC_TABLE = (function () {
    var table = new Uint32Array(256);
    for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; table[n] = c >>> 0; }
    return table;
  })();
  function crc32(bytes) {
    var c = 0xffffffff;
    for (var i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  function utf8Encode(text) { return new TextEncoder().encode(str(text)); }
  function utf8Decode(bytes) { return new TextDecoder("utf-8").decode(bytes); }
  function latin1Decode(bytes) { var s = ""; for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]); return s; }
  function bytesToBase64(bytes) {
    var s = "", chunk = 0x8000;
    for (var i = 0; i < bytes.length; i += chunk) s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    return btoa(s);
  }
  function base64ToBytes(b64) {
    var s = atob(str(b64).replace(/\s+/g, ""));
    var out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  var PNG_SIG = [137, 80, 78, 71, 13, 10, 26, 10];
  function isPng(bytes) { for (var i = 0; i < 8; i++) if (bytes[i] !== PNG_SIG[i]) return false; return true; }
  function readPngChunks(bytes) {
    if (!isPng(bytes)) throw new Error("not_png");
    var chunks = [], pos = 8;
    var view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    while (pos + 8 <= bytes.length) {
      var len = view.getUint32(pos); var type = latin1Decode(bytes.subarray(pos + 4, pos + 8));
      var data = bytes.subarray(pos + 8, pos + 8 + len);
      chunks.push({ type: type, data: data });
      pos += 12 + len;
      if (type === "IEND") break;
    }
    return chunks;
  }
  function pngTexts(bytes) {
    var texts = {};
    readPngChunks(bytes).forEach(function (c) {
      if (c.type === "tEXt") {
        var zero = c.data.indexOf(0);
        if (zero > 0) texts[latin1Decode(c.data.subarray(0, zero)).toLowerCase()] = c.data.subarray(zero + 1);
      } else if (c.type === "iTXt") {
        var z = c.data.indexOf(0);
        if (z > 0 && c.data[z + 1] === 0) {          // uncompressed iTXt only
          var z2 = c.data.indexOf(0, z + 3); var z3 = z2 >= 0 ? c.data.indexOf(0, z2 + 1) : -1;
          if (z3 >= 0) texts[latin1Decode(c.data.subarray(0, z)).toLowerCase()] = c.data.subarray(z3 + 1);
        }
      }
    });
    return texts;
  }
  function decodeCardText(raw) {
    var text = latin1Decode(raw).trim();
    if (/^[A-Za-z0-9+/=\s]+$/.test(text)) {
      try { return JSON.parse(utf8Decode(base64ToBytes(text))); } catch (e) { /* not base64 JSON */ }
    }
    return JSON.parse(utf8Decode(raw));
  }
  /** { json, chunk: "ccv3"|"chara", chunks: [...] } — ccv3 wins when both are present and readable (V3 spec). */
  function readCardInfo(bytes) {
    var texts = pngTexts(bytes);
    var found = Object.keys(texts).filter(function (k) { return k === "ccv3" || k === "chara"; });
    var order = ["ccv3", "chara"], lastError = null;
    for (var i = 0; i < order.length; i++) {
      if (!texts[order[i]]) continue;
      try { return { json: decodeCardText(texts[order[i]]), chunk: order[i], chunks: found }; }
      catch (e) { lastError = e; }
    }
    if (lastError) throw new Error("bad_card");
    throw new Error("no_card");
  }
  function readCardFromPng(bytes) { return readCardInfo(bytes).json; }
  function makeChunk(type, data) {
    var out = new Uint8Array(12 + data.length);
    var view = new DataView(out.buffer);
    view.setUint32(0, data.length);
    for (var i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
    out.set(data, 8);
    view.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)));
    return out;
  }
  function textChunk(keyword, text) {
    var k = utf8Encode(keyword), t = utf8Encode(text);
    var data = new Uint8Array(k.length + 1 + t.length);
    data.set(k, 0); data[k.length] = 0; data.set(t, k.length + 1);
    return makeChunk("tEXt", data);
  }
  function writeCardToPng(pngBytes, cardV2, cardV3) {
    var chunks = readPngChunks(pngBytes).filter(function (c) {
      if (c.type !== "tEXt" && c.type !== "iTXt") return true;
      var zero = c.data.indexOf(0);
      var key = latin1Decode(c.data.subarray(0, zero)).toLowerCase();
      return key !== "chara" && key !== "ccv3";
    });
    var parts = [new Uint8Array(PNG_SIG)];
    chunks.forEach(function (c) {
      if (c.type === "IEND") {
        parts.push(textChunk("chara", bytesToBase64(utf8Encode(JSON.stringify(cardV2)))));
        if (cardV3) parts.push(textChunk("ccv3", bytesToBase64(utf8Encode(JSON.stringify(cardV3)))));
      }
      parts.push(makeChunk(c.type, c.data));
    });
    var total = parts.reduce(function (a, p) { return a + p.length; }, 0);
    var out = new Uint8Array(total), at = 0;
    parts.forEach(function (p) { out.set(p, at); at += p.length; });
    return out;
  }
  var CARD_FIELDS = ["name", "description", "personality", "scenario", "first_mes", "mes_example", "creator_notes",
    "system_prompt", "post_history_instructions", "creator", "character_version"];
  /** V1 / app-specific top-level keys that are not part of the card's data object. */
  var V1_NOISE = { avatar: 1, chat: 1, create_date: 1, creatorcomment: 1, talkativeness: 1, fav: 1, spec: 1, spec_version: 1,
    json_data: 1, data: 1, char_name: 1, char_persona: 1, char_greeting: 1, world_scenario: 1, example_dialogue: 1,
    metadata: 1, date_added: 1, date_last_chat: 1, chat_size: 1, data_size: 1, shallow: 1 };
  /** Agnai persona -> text, exactly like agnai/common/characters.ts formatCharacter. */
  function formatAgnaiPersona(name, persona) {
    if (!persona || typeof persona !== "object") return "";
    var attrs = persona.attributes && typeof persona.attributes === "object" ? persona.attributes : {};
    function quote(v) { return '"' + str(v) + '"'; }
    function list(v) { return Array.isArray(v) ? v : (v === undefined || v === null ? [] : [v]); }
    switch (persona.kind) {
      case "wpp": return ['[character("' + name + '") {', Object.keys(attrs).map(function (k) { return k + "(" + list(attrs[k]).map(quote).join(" + ") + ")"; }).join("\n"), "}]"].join("\n");
      case "sbf": return '[ character: "' + name + '"; ' + Object.keys(attrs).map(function (k) { return k + ": " + list(attrs[k]).map(quote).join(", "); }).join("; ") + " ]";
      case "boostyle": return [name].concat(Object.keys(attrs).reduce(function (a, k) { return a.concat(list(attrs[k])); }, [])).join(" + ");
      case "attributes": return "Name: " + name + "\n" + Object.keys(attrs).map(function (k) { return k + ": " + list(attrs[k]).join(", "); }).join("\n");
      default: return str(list(attrs.text)[0]);
    }
  }
  /** Agnai's own character export (kind: "character") -> a V2 data object. */
  function fromAgnaiCharacter(json) {
    var name = str(json.name);
    var ext = json.extensions && typeof json.extensions === "object" ? clone(json.extensions) : {};
    if (json.insert && typeof json.insert === "object" && str(json.insert.prompt)) ext.depth_prompt = { prompt: str(json.insert.prompt), depth: num(json.insert.depth, 4), role: "system" };
    ext.agnai = Object.assign({}, ext.agnai || {}, { persona: clone(json.persona), voice: clone(json.voice), appearance: clone(json.appearance), sprite: clone(json.sprite) });
    var data = {
      name: name, description: formatAgnaiPersona(name, json.persona), personality: "", scenario: str(json.scenario),
      first_mes: str(json.greeting), mes_example: str(json.sampleChat), creator_notes: str(json.description),
      system_prompt: str(json.systemPrompt), post_history_instructions: str(json.postHistoryInstructions),
      alternate_greetings: Array.isArray(json.alternateGreetings) ? json.alternateGreetings.map(str) : [],
      tags: Array.isArray(json.tags) ? json.tags.map(str) : [], creator: str(json.creator), character_version: str(json.characterVersion),
      extensions: ext
    };
    if (json.characterBook && Array.isArray(json.characterBook.entries) && json.characterBook.entries.length) {
      data.character_book = toCharacterBook(fromAgnaiMemory(json.characterBook));
      delete data.character_book.extensions;
      data.character_book.extensions = {};
    }
    return data;
  }
  function isAgnaiCharacter(json) {
    return !!json && (json.kind === "character" || (json.persona !== undefined && json.greeting !== undefined && json.sampleChat !== undefined && !json.spec));
  }
  /** Any V1/V2/V3 card (ST, chub, RisuAI, Agnai, Pygmalion/TextGen, JanitorAI…) -> the internal data object (V2 shape).
   *  Every field the card carries is kept (V3-only fields and app extensions included) so it can be written back. */
  function cardToData(json) {
    if (!json || typeof json !== "object") throw new Error("bad_card");
    if (isAgnaiCharacter(json)) json = { spec: "chara_card_v2", spec_version: "2.0", data: fromAgnaiCharacter(json) };
    if (json.character && typeof json.character === "object" && (json.character.data || json.character.spec)) json = json.character;  // Pygmalion export wrapper
    var nested = json.data && typeof json.data === "object" && !Array.isArray(json.data) && (json.spec || json.data.name !== undefined || json.data.first_mes !== undefined);
    var data = nested ? json.data : json;
    var out = {};
    Object.keys(data).forEach(function (k) { if (nested || !V1_NOISE[k]) out[k] = clone(data[k]); });
    CARD_FIELDS.forEach(function (k) { out[k] = str(data[k] !== undefined && data[k] !== null ? data[k] : json[k]); });
    if (!out.name) out.name = str(json.char_name || json.name);
    if (!out.description && json.char_persona) out.description = str(json.char_persona);
    if (!out.scenario && json.world_scenario) out.scenario = str(json.world_scenario);
    if (!out.first_mes && json.char_greeting) out.first_mes = str(json.char_greeting);
    if (!out.mes_example && json.example_dialogue) out.mes_example = str(json.example_dialogue);
    if (!out.creator_notes && json.creatorcomment) out.creator_notes = str(json.creatorcomment);
    out.alternate_greetings = Array.isArray(data.alternate_greetings) ? data.alternate_greetings.map(str) : [];
    if (data.group_only_greetings !== undefined) out.group_only_greetings = Array.isArray(data.group_only_greetings) ? data.group_only_greetings.map(str) : [];
    var tags = data.tags !== undefined ? data.tags : json.tags;
    out.tags = Array.isArray(tags) ? tags.map(str).filter(Boolean) : (typeof tags === "string" ? tags.split(",").map(function (x) { return x.trim(); }).filter(Boolean) : []);
    out.extensions = data.extensions && typeof data.extensions === "object" && !Array.isArray(data.extensions) ? clone(data.extensions) : {};
    if (data.character_book && typeof data.character_book === "object") out.character_book = clone(data.character_book);
    else delete out.character_book;
    if (!out.extensions.depth_prompt && json.depth_prompt) out.extensions.depth_prompt = clone(json.depth_prompt);
    if (out.extensions.talkativeness === undefined && json.talkativeness !== undefined) out.extensions.talkativeness = num(json.talkativeness, 0.5);
    if (out.extensions.fav === undefined && json.fav !== undefined) out.extensions.fav = json.fav === true || json.fav === "true";
    return out;
  }
  /** Which application most likely wrote this card (for the import preview). */
  function cardOrigin(json) {
    if (!json || typeof json !== "object") return "";
    if (isAgnaiCharacter(json)) return "agnai";
    var d = json.data && typeof json.data === "object" ? json.data : json;
    var ext = (d && d.extensions) || {};
    if (ext.risuai || (Array.isArray(d.assets) && d.assets.some(function (a) { return /^(embeded|__asset):/.test(str(a && a.uri)); }))) return "risu";
    if (ext.chub) return "chub";
    if (ext.agnai) return "agnai";
    if (ext.janitor || ext.jannyai) return "janitor";
    if (ext.perchance_data) return "perchance";
    if (json.char_name !== undefined) return "textgen";
    if (json.spec === "chara_card_v3") return "v3";
    if (json.spec === "chara_card_v2") return "v2";
    return "v1";
  }
  var V2_DATA_FIELDS = CARD_FIELDS.concat(["alternate_greetings", "tags", "extensions", "character_book"]);
  /** The V3 data object: every field we hold (unknown ones too), V3 additions filled in. */
  function v3DataOf(data, worldForBook, opts) {
    var d = clone(data) || {};
    opts = opts || {};
    CARD_FIELDS.forEach(function (k) { d[k] = str(d[k]); });
    d.alternate_greetings = Array.isArray(d.alternate_greetings) ? d.alternate_greetings : [];
    d.tags = Array.isArray(d.tags) ? d.tags : [];
    d.extensions = Object.assign({ talkativeness: 0.5, fav: false, world: "", depth_prompt: { prompt: "", depth: 4, role: "system" } }, d.extensions || {});
    var book = worldForBook ? toCharacterBook(worldForBook) : d.character_book;
    if (worldForBook) d.extensions.world = str(worldForBook.name);
    if (book && Array.isArray(book.entries)) d.character_book = book; else delete d.character_book;
    d.group_only_greetings = Array.isArray(d.group_only_greetings) ? d.group_only_greetings : [];
    // Embedded assets (RisuAI `__asset:` / CHARX `embeded://`) live in the source file only: keep links we can resolve.
    var assets = (Array.isArray(d.assets) ? d.assets : []).filter(function (a) {
      return a && typeof a === "object" && !(a.type === "icon" && a.name === "main") && /^(https?:|data:)/i.test(str(a.uri));
    });
    d.assets = [{ type: "icon", uri: "ccdefault:", name: "main", ext: "png" }].concat(assets);
    if (d.nickname === undefined) d.nickname = "";
    if (!d.creator_notes_multilingual || typeof d.creator_notes_multilingual !== "object") d.creator_notes_multilingual = {};
    if (!Array.isArray(d.source)) d.source = [];
    var nowSec = Math.floor((opts.now || Date.now()) / 1000);
    if (!Number.isFinite(Number(d.creation_date)) || d.creation_date === "" || d.creation_date === null) d.creation_date = opts.created ? Math.floor(opts.created / 1000) : nowSec;
    if (!Number.isFinite(Number(d.modification_date)) || d.modification_date === "" || d.modification_date === null) d.modification_date = opts.updated ? Math.floor(opts.updated / 1000) : nowSec;
    return d;
  }
  /** SillyTavern's V2 card: V1 fields at the top for old readers, the V2 data object inside. */
  function dataToV2(data, worldForBook, opts) {
    var full = v3DataOf(data, worldForBook, opts);
    var v2data = {};
    V2_DATA_FIELDS.forEach(function (k) { if (full[k] !== undefined) v2data[k] = full[k]; });
    // A V2 card has no `group_only_greetings`/`assets`: those travel in the ccv3 chunk only.
    return {
      name: v2data.name, description: v2data.description, personality: v2data.personality, scenario: v2data.scenario,
      first_mes: v2data.first_mes, mes_example: v2data.mes_example, creatorcomment: v2data.creator_notes,
      avatar: "none", chat: "", talkativeness: String(v2data.extensions.talkativeness), fav: !!v2data.extensions.fav,
      tags: v2data.tags, spec: "chara_card_v2", spec_version: "2.0", data: v2data,
      create_date: new Date((opts && opts.created) || Date.now()).toISOString()
    };
  }
  function dataToV3(data, worldForBook, opts) {
    var v3data = v3DataOf(data, worldForBook, opts);
    var v2 = dataToV2(data, worldForBook, opts);
    var out = clone(v2);
    out.spec = "chara_card_v3"; out.spec_version = "3.0"; out.data = v3data;
    return out;
  }

  /* ------------------------------------------------------------ SillyTavern chat files (.jsonl) */
  function chatToJsonl(chat, userName, charName) {
    var lines = [JSON.stringify({ user_name: userName, character_name: charName, create_date: new Date(chat.created || Date.now()).toISOString(), chat_metadata: { variables: (chat.meta && chat.meta.variables) || {}, note_prompt: chat.meta && chat.meta.note ? chat.meta.note.text : "" } })];
    (chat.messages || []).forEach(function (m) {
      var row = { name: m.name || (m.role === "user" ? userName : charName), is_user: m.role === "user", is_system: !!m.hidden,
        send_date: new Date(m.date || Date.now()).toISOString(), mes: m.mes, extra: {} };
      if (m.reasoning) row.extra.reasoning = m.reasoning;
      if (m.model) row.extra.model = m.model;
      if (Array.isArray(m.swipes) && m.swipes.length > 1) { row.swipes = m.swipes.slice(); row.swipe_id = m.swipe_id || 0; }
      lines.push(JSON.stringify(row));
    });
    return lines.join("\n");
  }
  function jsonlToChat(text) {
    var lines = str(text).split(/\r?\n/).filter(function (x) { return x.trim(); });
    if (!lines.length) throw new Error("empty");
    var header = JSON.parse(lines[0]);
    var messages = [];
    lines.slice(header.mes !== undefined ? 0 : 1).forEach(function (line) {
      var row; try { row = JSON.parse(line); } catch (e) { return; }
      if (!row || row.mes === undefined) return;
      var swipes = Array.isArray(row.swipes) && row.swipes.length ? row.swipes.map(str) : [str(row.mes)];
      var swipeId = Math.max(0, Math.min(swipes.length - 1, num(row.swipe_id, 0)));
      messages.push({ id: uid("m"), role: row.is_user ? "user" : "assistant", name: str(row.name), mes: str(row.mes),
        swipes: swipes, swipe_id: swipeId, swipe_info: swipes.map(function () { return {}; }),
        reasoning: str(row.extra && row.extra.reasoning), hidden: !!row.is_system,
        date: Date.parse(row.send_date) || Date.now(), model: str(row.extra && row.extra.model) });
    });
    return { header: header, messages: messages };
  }

  return {
    estimateTokens: estimateTokens, setTokenModel: setTokenModel, messageTokens: messageTokens, substitute: substitute,
    parseRegexFromString: parseRegexFromString, checkWorldInfo: checkWorldInfo, normalizeEntry: normalizeEntry,
    normalizeWorld: normalizeWorld, fromCharacterBook: fromCharacterBook, toCharacterBook: toCharacterBook,
    exportWorld: exportWorld, normalizePreset: normalizePreset, exportPreset: exportPreset, presetOrder: presetOrder,
    parseMesExamples: parseMesExamples, parseExampleIntoIndividual: parseExampleIntoIndividual,
    buildPrompt: buildPrompt, postProcess: postProcess, samplerParams: samplerParams, splitReasoning: splitReasoning, findLeakedToken: findLeakedToken,
    runRegexScripts: runRegexScripts, readCardFromPng: readCardFromPng, readCardInfo: readCardInfo, writeCardToPng: writeCardToPng,
    cardToData: cardToData, dataToV2: dataToV2, dataToV3: dataToV3, cardOrigin: cardOrigin, formatAgnaiPersona: formatAgnaiPersona,
    importWorld: importWorld, detectJson: detectJson, toAgnaiMemory: toAgnaiMemory, isBookCard: isBookCard, crc32: crc32, isPng: isPng,
    bytesToBase64: bytesToBase64, base64ToBytes: base64ToBytes, utf8Encode: utf8Encode, utf8Decode: utf8Decode,
    chatToJsonl: chatToJsonl, jsonlToChat: jsonlToChat, uid: uid, clone: clone, hashString: hashString,
    WI_DEFAULTS: WI_DEFAULTS, ENTRY_DEFAULTS: ENTRY_DEFAULTS, PRESET_DEFAULTS: PRESET_DEFAULTS, PRESET_KEYS: PRESET_KEYS,
    DEFAULT_PROMPTS: DEFAULT_PROMPTS, DEFAULT_ORDER: DEFAULT_ORDER, MARKERS: MARKERS, WI_POS: WI_POS
  };
})();
if (typeof module !== "undefined" && module.exports) module.exports = CharEngine;
/* @engine-end */


/* ====================================================================== core UI helpers */
var E = CharEngine;
function t(key, vars) { return Memu.t(key, vars); }
function $(selector, root) { return (root || document).querySelector(selector); }
function $$(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }
function esc(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function h(tag, attrs, children) {
  var node = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (key) {
    var value = attrs[key];
    if (value === undefined || value === null || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "text") node.textContent = value;
    else if (key.indexOf("on") === 0 && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  });
  (Array.isArray(children) ? children : children === undefined ? [] : [children]).forEach(function (child) {
    if (child === null || child === undefined || child === false) return;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return node;
}
function debounce(fn, ms) {
  var timer = null;
  var wrapped = function () { var args = arguments; clearTimeout(timer); timer = setTimeout(function () { timer = null; fn.apply(null, args); }, ms); };
  wrapped.flush = function () { if (timer) { clearTimeout(timer); timer = null; fn(); } };
  wrapped.pending = function () { return !!timer; };
  return wrapped;
}
function fmtNum(n) { return Memu.i18n.number(Math.round(Number(n) || 0)); }
function fmtDate(value) {
  if (!value) return "";
  return Memu.i18n.date(new Date(value), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

var ICONS = {
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.2-2.5 3.9"/><path d="M12 17.2h.01"/>',
  alert: '<path d="M12 3.5 2.8 19.5h18.4z"/><path d="M12 10v4.2"/><path d="M12 17.2h.01"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  upload: '<path d="M12 15V3m0 0 4 4m-4-4L8 7"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
  download: '<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6.3"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  palette: '<circle cx="13.5" cy="6.5" r="1"/><circle cx="17.5" cy="10.5" r="1"/><circle cx="8.5" cy="7.5" r="1"/><circle cx="6.5" cy="12.5" r="1"/><path d="M12 2a10 10 0 0 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.4A5.6 5.6 0 0 0 22 9.8C22 5.5 17.5 2 12 2z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  more: '<circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/>',
  dots: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  left: '<path d="m15 18-6-6 6-6"/>',
  right: '<path d="m9 18 6-6-6-6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m18 15-6-6-6 6"/>',
  star: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
  bookmark: '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
  branch: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7M18 10.5c0 4-6 3-12 5"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10.6 10.6 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3 3.9M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  forward: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  mask: '<path d="M3 7c3-2 6-2 9 0 3-2 6-2 9 0v4c0 5-4 9-9 9s-9-4-9-9z"/><path d="M8 11h.01M16 11h.01M9 15c2 1.3 4 1.3 6 0"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
  note: '<path d="M4 4h16v12l-4 4H4z"/><path d="M16 20v-4h4M8 9h8M8 13h5"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  sidebar: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
  grip: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  brain: '<path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-2.04z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-2.04z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/>',
  attach: '<path d="m21.4 11-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  regex: '<path d="M17 3v10M12.7 5.5l8.6 5M12.7 10.5l8.6-5"/><rect x="3" y="15" width="6" height="6" rx="1"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  pin: '<path d="M12 17v5M9 10.8V4h6v6.8l3 3.2H6z"/>',
  home: '<path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/>',
  flag: '<path d="M4 22V4M4 4h13l-2 4 2 4H4"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  columns: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
  wand: '<path d="m4 20 11-11"/><path d="m14 4 1.2 2.8L18 8l-2.8 1.2L14 12l-1.2-2.8L10 8l2.8-1.2z"/><path d="M19 13v3M17.5 14.5h3"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  dice: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h.01M15 15h.01M15 9h.01M9 15h.01M12 12h.01"/>'
};
function icon(name, size) {
  return '<svg class="ic" width="' + (size || 18) + '" height="' + (size || 18) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
}
function iconBtn(name, titleKey, onClick, extra) {
  var b = h("button", Object.assign({ class: "ch-ib", type: "button", title: t(titleKey), "aria-label": t(titleKey), html: icon(name) }, extra || {}));
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

/* ---------------------------------------------------------------- toasts */
function toast(text, kind) {
  var root = $("#toasts");
  if (!root) return;
  var node = h("div", { class: "ch-toast " + (kind || ""), role: "status" }, [h("span", { text: String(text) })]);
  root.appendChild(node);
  requestAnimationFrame(function () { node.classList.add("in"); });
  setTimeout(function () { node.classList.remove("in"); setTimeout(function () { node.remove(); }, 260); }, kind === "error" ? 6500 : 3200);
}

/* ---------------------------------------------------------------- modals */
var modalStack = [];
function openModal(opts) {
  var root = $("#modal-root");
  var wrap = h("div", { class: "ch-modal-wrap" + (opts.wide ? " wide" : "") + (opts.full ? " full" : "") });
  var box = h("div", { class: "ch-modal", role: "dialog", "aria-modal": "true", "aria-label": opts.title || "" });
  var head = h("div", { class: "ch-modal-head" }, [
    opts.icon ? h("span", { class: "ch-modal-ic", html: icon(opts.icon, 20) }) : null,
    h("div", { class: "ch-modal-title", text: opts.title || "" }),
    h("span", { class: "mu-grow" }),
    opts.headExtra || null,
    iconBtn("x", "close", function () { close(); })
  ]);
  var body = h("div", { class: "ch-modal-body" });
  var foot = h("div", { class: "ch-modal-foot" });
  box.appendChild(head); box.appendChild(body);
  if (opts.buttons && opts.buttons.length) box.appendChild(foot);
  wrap.appendChild(box);
  root.appendChild(wrap);
  var api = { wrap: wrap, box: box, body: body, foot: foot, close: close, onClose: opts.onClose };
  function close(result) {
    if (api.closed) return;
    if (api.beforeClose && api.beforeClose() === false) return;
    api.closed = true;
    wrap.classList.remove("in");
    modalStack = modalStack.filter(function (m) { return m !== api; });
    setTimeout(function () { wrap.remove(); }, 180);
    if (api.onClose) api.onClose(result);
  }
  (opts.buttons || []).forEach(function (b) {
    var btn = h("button", { class: "mu-btn " + (b.kind || ""), type: "button", text: b.label });
    btn.addEventListener("click", function () { var r = b.onClick ? b.onClick(api) : undefined; if (r !== false && b.close !== false) close(b.value); });
    foot.appendChild(btn);
  });
  wrap.addEventListener("mousedown", function (e) { if (e.target === wrap && !opts.sticky) close(); });
  if (typeof opts.content === "function") opts.content(body, api);
  else if (opts.content) body.appendChild(opts.content);
  modalStack.push(api);
  requestAnimationFrame(function () { wrap.classList.add("in"); var f = box.querySelector("[autofocus]"); if (f) f.focus(); });
  return api;
}
function confirmBox(text, opts) {
  opts = opts || {};
  return new Promise(function (resolve) {
    var done = false;
    openModal({
      title: opts.title || t("confirm"), icon: opts.icon || "info",
      content: h("p", { class: "ch-confirm-text", text: text }),
      buttons: [
        { label: t("cancel"), kind: "ghost", onClick: function () { done = true; resolve(false); } },
        { label: opts.ok || t("ok"), kind: opts.danger ? "danger" : "primary", onClick: function () { done = true; resolve(true); } }
      ],
      onClose: function () { if (!done) resolve(false); }
    });
  });
}
function promptBox(title, value, opts) {
  opts = opts || {};
  return new Promise(function (resolve) {
    var done = false;
    var input = opts.multiline ? h("textarea", { class: "mu-textarea", rows: "6", autofocus: true }) : h("input", { class: "mu-input", autofocus: true });
    input.value = value || "";
    var modal = openModal({
      title: title, icon: opts.icon || "edit",
      content: h("div", { class: "ch-form" }, [opts.hint ? h("p", { class: "mu-muted", text: opts.hint }) : null, input]),
      buttons: [
        { label: t("cancel"), kind: "ghost", onClick: function () { done = true; resolve(null); } },
        { label: t("ok"), kind: "primary", onClick: function () { done = true; resolve(input.value); } }
      ],
      onClose: function () { if (!done) resolve(null); }
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (!opts.multiline || e.ctrlKey)) { e.preventDefault(); done = true; resolve(input.value); modal.close(); }
    });
  });
}
/* popover menu: items [{icon, label, onClick, danger, sep}] */
function popMenu(anchor, items) {
  closeMenus();
  var menu = h("div", { class: "ch-menu", role: "menu" });
  items.forEach(function (item) {
    if (!item) return;
    if (item.sep) { menu.appendChild(h("div", { class: "ch-menu-sep" })); return; }
    if (item.head) { menu.appendChild(h("div", { class: "ch-menu-head", text: item.head })); return; }
    var b = h("button", { class: "ch-menu-item" + (item.danger ? " danger" : "") + (item.active ? " active" : ""), type: "button", role: "menuitem" }, [
      h("span", { class: "ch-menu-ic", html: item.icon ? icon(item.icon, 16) : "" }),
      h("span", { class: "ch-menu-label", text: item.label }),
      item.hint ? h("span", { class: "ch-menu-hint", text: item.hint }) : null
    ]);
    b.disabled = !!item.disabled;
    b.addEventListener("click", function (e) { e.stopPropagation(); closeMenus(); if (item.onClick) item.onClick(); });
    menu.appendChild(b);
  });
  document.body.appendChild(menu);
  var r = anchor.getBoundingClientRect();
  var mw = menu.offsetWidth, mh = menu.offsetHeight;
  var x = clamp(r.right - mw, 6, window.innerWidth - mw - 6);
  var y = r.bottom + 6;
  if (y + mh > window.innerHeight - 6) y = Math.max(6, r.top - mh - 6);
  menu.style.left = x + "px"; menu.style.top = y + "px";
  requestAnimationFrame(function () { menu.classList.add("in"); });
  return menu;
}
function closeMenus() { $$(".ch-menu").forEach(function (m) { m.remove(); }); }
document.addEventListener("mousedown", function (e) { if (!e.target.closest(".ch-menu")) closeMenus(); });

/* ---------------------------------------------------------------- files */
var fileInput = null;
function pickFile(accept, multiple) {
  return new Promise(function (resolve) {
    if (fileInput) fileInput.remove();
    fileInput = h("input", { type: "file", accept: accept || "", hidden: true });
    if (multiple) fileInput.multiple = true;
    fileInput.addEventListener("change", function () { resolve(Array.prototype.slice.call(fileInput.files || [])); });
    document.body.appendChild(fileInput);
    fileInput.click();
  });
}
function readAs(file, kind) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () { resolve(reader.result); };
    reader.onerror = function () { reject(reader.error || new Error("read")); };
    if (kind === "buffer") reader.readAsArrayBuffer(file);
    else if (kind === "dataurl") reader.readAsDataURL(file);
    else reader.readAsText(file);
  });
}
function loadImage(src) {
  return new Promise(function (resolve, reject) {
    var img = new Image();
    img.onload = function () { resolve(img); };
    img.onerror = function () { reject(new Error("image")); };
    img.src = src;
  });
}
/* Resize an image data-URL so that it fits maxW x maxH; type: image/webp|image/png|image/jpeg */
function resizeImage(src, maxW, maxH, type, quality) {
  return loadImage(src).then(function (img) {
    var w = img.naturalWidth || img.width, hgt = img.naturalHeight || img.height;
    var scale = Math.min(1, maxW / w, maxH / hgt);
    var canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(w * scale)); canvas.height = Math.max(1, Math.round(hgt * scale));
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    var out = canvas.toDataURL(type || "image/webp", quality || 0.9);
    if (type === "image/webp" && out.indexOf("data:image/webp") !== 0) out = canvas.toDataURL("image/jpeg", quality || 0.9);
    return out;
  });
}
function dataUrlToBytes(url) { return E.base64ToBytes(String(url).split(",")[1] || ""); }
function bytesToDataUrl(bytes, mime) { return "data:" + mime + ";base64," + E.bytesToBase64(bytes); }
function download(name, text, mime) {
  var url = typeof text === "string" && text.indexOf("data:") === 0 ? text
    : "data:" + (mime || "application/json") + ";base64," + E.bytesToBase64(E.utf8Encode(text));
  return Memu.download({ url: url, name: name }).then(function (r) {
    toast(t("saved_to", { path: (r && r.path) || name }), "ok");
    return r;
  }, function (e) { toast(e.message, "error"); });
}
function safeFileName(name, ext) {
  return (String(name || "file").replace(/[\\/:*?"<>|\r\n\t]+/g, "_").trim().slice(0, 80) || "file") + (ext || "");
}
/* Default avatar: an SVG with the initial on a gradient (data URL, no network). */
// r30: the letter is centred on the top 200×200 square (what a round/square avatar shows with object-position:top)
// by the cap height (~0.7em for Latin and Cyrillic capitals) on the alphabetic baseline: «central» centred the em box,
// so capitals sat visibly low.
function defaultAvatar(name, seed) {
  var hue = (E.hashString(seed || name || "x") % 360);
  var letter = String(name || "?").trim().charAt(0).toUpperCase() || "?";
  var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300" viewBox="0 0 200 300"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(' + hue + ',62%,58%)"/><stop offset="1" stop-color="hsl(' + ((hue + 48) % 360) + ',58%,36%)"/></linearGradient></defs><rect width="200" height="300" fill="url(#g)"/><text x="100" y="132" font-family="Segoe UI,system-ui,sans-serif" font-size="92" font-weight="600" fill="rgba(255,255,255,.92)" text-anchor="middle">' + esc(letter) + "</text></svg>";
  return "data:image/svg+xml;base64," + E.bytesToBase64(E.utf8Encode(svg));
}

/* ====================================================================== storage */
var DEFAULT_SETTINGS = {
  version: 1, model: "", presetId: "universal", personaId: "",
  // r37: the AI co-writer (assist.js) — model "" = the chat model.
  assist: { model: "", lang: "auto", buttons: true },
  ui: {
    chatStyle: "bubbles", avatarShape: "circle", avatarSize: 42, font: "sans", fontSize: 100, msgWidth: 860,
    blur: 10, accent: "", quoteColor: "", italicColor: "", userBubble: "", bg: "", bgDim: 45, sidebar: true,
    showTimestamps: true, showTokens: true, showModel: true, showIds: false, reasoningOpen: false,
    enterSends: true, animations: true, gallerySort: "recent", galleryView: "grid", theme: "host", density: "normal",
    swipeAnim: true, hideAvatars: false, centerChat: true,
    // r33: right-click on the swipe arrow — how many new variants to start at once, and whether a left click does the same.
    variantCount: 3, variantLeftClick: false
  },
  gen: {
    streaming: true, sendPrefill: true, nameField: false, postProcessing: "preset", clampContext: true,
    thinking: "auto", autoParseReasoning: true, reasoningPrefix: "<think>", reasoningSuffix: "</think>",
    preferCharPrompt: true, preferCharJailbreak: true, pinExamples: false, extendedSamplers: false,
    promptCaching: true, inlineImages: true, sendReasoning: false, showPrefill: false, autoContinue: false, stop: [],
    wiIncludeNames: true, contextOverride: 0, autoTitle: false, keepSwipes: true
  },
  wi: Object.assign({ global: [] }, E.WI_DEFAULTS),
  regex: [], quickReplies: [], globals: {}, favorites: [], bookmarksOnly: false, lastCharId: "", tagFilter: ""
};
var Store = {
  cache: {},
  get: function (key, fallback) {
    if (Object.prototype.hasOwnProperty.call(Store.cache, key)) {
      var cached = Store.cache[key];
      return Promise.resolve(cached === null || cached === undefined ? fallback : E.clone(cached));
    }
    return Memu.store.get(key, null).then(function (value) {
      Store.cache[key] = value;
      return value === null || value === undefined ? fallback : E.clone(value);
    });
  },
  set: function (key, value) {
    Store.cache[key] = E.clone(value);
    return Memu.store.set(key, value).catch(function (e) { toast(t("save_failed", { message: e.message }), "error"); throw e; });
  },
  remove: function (key) { delete Store.cache[key]; return Memu.store.remove(key); },
  list: function (prefix) { return Memu.store.list(prefix); }
};

/* ====================================================================== state */
var S = {
  settings: E.clone(DEFAULT_SETTINGS),
  index: [],            // character summaries [{id, name, tags, thumb, fav, updated, lastChat, chats}]
  chars: {},            // loaded characters by id
  char: null,           // active character
  chat: null,           // active chat object
  chatList: [],         // chats of the active character [{id, title, updated, count, preview}]
  personas: [],
  worlds: {},           // id -> world {id, name, entries}
  worldIndex: [],       // [{id, name, count}]
  presetIndex: [],      // builtins [{id, name_ru, ...}]
  userPresets: [],      // [{id, name}]
  preset: null,         // active normalized preset
  presetRaw: null,
  models: [],
  busy: false,
  job: null,
  lastPrompt: null,
  drawerTab: "preset",
  drawerOpen: false,
  view: "home",
  search: "",
  editing: null
};
function mergeDefaults(target, defaults) {
  Object.keys(defaults).forEach(function (k) {
    if (target[k] === undefined || target[k] === null) target[k] = E.clone(defaults[k]);
    else if (defaults[k] && typeof defaults[k] === "object" && !Array.isArray(defaults[k]) && typeof target[k] === "object") mergeDefaults(target[k], defaults[k]);
  });
  return target;
}
var saveSettings = debounce(function () { Store.set("settings", S.settings); }, 500);
var saveIndex = debounce(function () { Store.set("index", S.index); }, 400);
var savePersonas = debounce(function () { Store.set("personas", S.personas); }, 400);
var saveChatNow = function () {
  if (!S.chat || !S.char) return Promise.resolve();
  S.chat.updated = Date.now();
  var chat = S.chat;
  var row = S.chatList.find(function (c) { return c.id === chat.id; });
  var summary = chatSummary(chat);
  if (row) Object.assign(row, summary); else S.chatList.unshift(summary);
  var idx = S.index.find(function (c) { return c.id === S.char.id; });
  if (idx) { idx.lastChat = chat.id; idx.updated = chat.updated; idx.chats = S.chatList.length; saveIndex(); }
  Store.set("chatlist/" + S.char.id, S.chatList);
  return Store.set("chats/" + S.char.id + "/" + chat.id, chat);
};
var saveChat = debounce(saveChatNow, 700);
function chatSummary(chat) {
  var last = (chat.messages || []).slice(-1)[0];
  return { id: chat.id, title: chat.title || "", created: chat.created, updated: chat.updated || chat.created,
    count: (chat.messages || []).length, preview: last ? String(last.mes || "").slice(0, 140) : "",
    bookmarks: (chat.messages || []).filter(function (m) { return m.bookmark; }).length, parent: chat.parent || "" };
}
function flushAll() {
  saveSettings.flush(); saveIndex.flush(); savePersonas.flush(); saveChat.flush();
  Object.keys(worldSavers).forEach(function (k) { worldSavers[k].flush(); });
  if (charSaver.pending()) charSaver.flush();
}
window.addEventListener("pagehide", flushAll);
document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flushAll(); });
var worldSavers = {};
function saveWorld(world) {
  S.worlds[world.id] = world;
  var row = S.worldIndex.find(function (w) { return w.id === world.id; });
  var info = { id: world.id, name: world.name, count: Object.keys(world.entries || {}).length };
  if (row) Object.assign(row, info); else S.worldIndex.push(info);
  if (!worldSavers[world.id]) worldSavers[world.id] = debounce(function () {
    Store.set("worlds/" + world.id, S.worlds[world.id]);
    Store.set("worldindex", S.worldIndex);
  }, 600);
  worldSavers[world.id]();
}
var charSaver = debounce(function () {
  if (S.char) saveCharacter(S.char);
}, 600);
function saveCharacter(char) {
  char.updated = Date.now();
  S.chars[char.id] = char;
  var row = S.index.find(function (c) { return c.id === char.id; });
  var info = { id: char.id, name: char.data.name, tags: char.data.tags || [], thumb: char.thumb || "", fav: !!char.fav,
    updated: char.updated, created: char.created, creator: char.data.creator || "", summary: String(char.data.creator_notes || char.data.description || "").slice(0, 220) };
  if (row) Object.assign(row, info); else S.index.unshift(Object.assign({ lastChat: "", chats: 0 }, info));
  saveIndex();
  return Store.set("chars/" + char.id, char);
}


/* ====================================================================== text formatting */
// Dialogue in quotes gets the quote colour. Each opener closes with its own pair and «…» nests, so
// «Say „«Ignis»“ now» and «Say "abracadabra" now» are one quote each (r30 live chat: the inner quote closed
// the outer one and the line split into alternating pieces). Works on escaped HTML, one line at a time.
var QUOTE_PAIRS = { "\u00ab": "\u00bb", "\u201c": "\u201d", "\u201e": "\u201c", "&quot;": "&quot;" };
function highlightQuotes(html) {
  return html.split("\n").map(function (line) {
    var out = "", i = 0, open = /\u00ab|\u201c|\u201e|&quot;/g, match;
    while ((match = open.exec(line))) {
      var opener = match[0], closer = QUOTE_PAIRS[opener], at = match.index + opener.length, end = -1, depth = 1;
      while (at < line.length) {
        if (opener === "\u00ab" && line[at] === "\u00ab") depth += 1;
        else if (line.startsWith(closer, at) && --depth === 0) { end = at + closer.length; break; }
        at += 1;
      }
      if (end < 0) continue;
      out += line.slice(i, match.index) + '<q class="ch-q">' + line.slice(match.index, end) + "</q>";
      i = end;
      open.lastIndex = end;
    }
    return out + line.slice(i);
  }).join("\n");
}
function formatText(text, opts) {
  opts = opts || {};
  var blocks = [];
  var s = String(text || "");
  if (opts.regex !== false) s = E.runRegexScripts(s, S.settings.regex, opts.placement || 2, { isMarkdown: true, depth: opts.depth });
  s = esc(s);
  s = s.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, function (_, lang, code) {
    blocks.push('<pre class="ch-code"><div class="ch-code-head"><span>' + (lang || "text") + '</span><button type="button" class="ch-code-copy" data-copy="1">' + esc(t("copy")) + '</button></div><code>' + code.replace(/\n$/, "") + "</code></pre>");
    return "\u0000" + (blocks.length - 1) + "\u0000";
  });
  s = s.replace(/`([^`\n]+)`/g, function (_, code) { blocks.push("<code>" + code + "</code>"); return "\u0000" + (blocks.length - 1) + "\u0000"; });
  s = s.replace(/^(#{1,4})\s+(.+)$/gm, function (_, hashes, body) { return '<div class="ch-h ch-h' + hashes.length + '">' + body + "</div>"; });
  s = s.replace(/^&gt;\s?(.*)$/gm, '<span class="ch-bq">$1</span>');
  s = s.replace(/^(?:-{3,}|\*{3,})$/gm, '<hr class="ch-hr">');
  s = s.replace(/\*\*\*(?!\s)([^*\n]+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  s = s.replace(/\*\*(?!\s)([^*\n]+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w\\])\*(?!\s)([^*\n]+?)\*(?![\w*])/g, '$1<em class="ch-i">$2</em>');
  s = s.replace(/(^|[^_\w\\])_(?!\s)([^_\n]+?)_(?![\w_])/g, '$1<em class="ch-i">$2</em>');
  s = s.replace(/~~([^~\n]+?)~~/g, "<del>$1</del>");
  s = highlightQuotes(s);
  s = s.replace(/\n/g, "<br>");
  s = s.replace(/\u0000(\d+)\u0000/g, function (_, n) { return blocks[Number(n)]; });
  return s;
}

/* ====================================================================== macro context for display */
function personaOf(id) { return S.personas.find(function (p) { return p.id === id; }) || null; }
function activePersona() {
  var id = (S.chat && S.chat.meta && S.chat.meta.personaId)
    || (S.char && S.settings.charPersona && S.settings.charPersona[S.char.id]) || S.settings.personaId;
  return personaOf(id) || S.personas[0] || { id: "", name: t("default_user_name"), description: "", position: 0, depth: 2, role: 0, avatar: "" };
}
function userName() { return activePersona().name || t("default_user_name"); }
function displaySubst(text) {
  if (!S.char) return text;
  return E.substitute(text, { user: userName(), char: S.char.data.name, lang: Memu.lang, variables: E.clone((S.chat && S.chat.meta && S.chat.meta.variables) || {}), globals: E.clone(S.settings.globals || {}) });
}

// Macros in what the person types ({{roll:1d20}}, {{random::…}}, {{time}}…) are evaluated once, when the message is
// sent, and the result is stored — as in SillyTavern. Phase 2: the raw text was stored, so the chat showed «{{roll:1d6}}»
// while the model saw a number, and every later prompt re-rolled it (the model saw a different value each turn).
function sendSubst(text, messages) {
  if (!S.char || String(text).indexOf("{{") < 0 && !/<(USER|BOT|CHAR)>/i.test(text)) return text;
  var meta = S.chat.meta || (S.chat.meta = {});
  var d = S.char.data || {};
  return E.substitute(text, { user: userName(), char: d.name, lang: Memu.lang, chat: engineChat(messages || []), chatId: S.chat.id,
    description: d.description, personality: d.personality, scenario: d.scenario, persona: activePersona().description || "",
    model: S.settings.model, variables: meta.variables || (meta.variables = {}), globals: S.settings.globals || (S.settings.globals = {}) });
}

/* ====================================================================== characters */
function charAvatar(char) { return (char && (char.avatar || char.thumb)) || defaultAvatar(char && char.data ? char.data.name : "?", char && char.id); }
function thumbOf(row) { return row.thumb || defaultAvatar(row.name, row.id); }
function loadChar(id) {
  if (S.chars[id]) return Promise.resolve(S.chars[id]);
  return Store.get("chars/" + id, null).then(function (char) { if (char) S.chars[id] = char; return char; });
}
function newCharacter(data, avatar) {
  var now = Date.now();
  var char = { id: E.uid("c"), created: now, updated: now, fav: false, avatar: avatar || "", thumb: "", data: Object.assign({
    name: "", description: "", personality: "", scenario: "", first_mes: "", mes_example: "", creator_notes: "",
    system_prompt: "", post_history_instructions: "", creator: "", character_version: "", alternate_greetings: [],
    tags: [], extensions: { talkativeness: 0.5, depth_prompt: { prompt: "", depth: 4, role: "system" } }
  }, data || {}) };
  return char;
}
function makeThumb(avatar) {
  if (!avatar) return Promise.resolve("");
  return resizeImage(avatar, 160, 240, "image/webp", 0.86).catch(function () { return ""; });
}
function openCharacter(id, chatId) {
  if (S.busy) { toast(t("busy_wait"), "warn"); return Promise.resolve(); }
  saveChat.flush();
  return loadChar(id).then(function (char) {
    if (!char) { toast(t("char_missing"), "error"); return; }
    S.char = char;
    S.settings.lastCharId = id; saveSettings();
    return Store.get("chatlist/" + id, []).then(function (list) {
      S.chatList = list || [];
      var row = S.index.find(function (c) { return c.id === id; });
      var target = chatId || (row && row.lastChat) || (S.chatList[0] && S.chatList[0].id);
      if (target) return openChat(target).then(function (ok) { if (!ok) return startNewChat(); });
      return startNewChat();
    });
  }).then(function () { S.view = "chat"; render(); });
}
function greetingMessage(char) {
  var greetings = [char.data.first_mes || ""].concat(char.data.alternate_greetings || []).filter(function (g, i) { return i === 0 || g; });
  if (!greetings.join("").trim()) return null;
  return { id: E.uid("m"), role: "assistant", name: char.data.name, mes: greetings[0], swipes: greetings, swipe_id: 0,
    swipe_info: greetings.map(function () { return { greeting: true }; }), greeting: true, date: Date.now(), reasoning: "" };
}
function startNewChat(silent) {
  var char = S.char;
  var now = Date.now();
  var chat = { id: E.uid("chat"), charId: char.id, title: char.data.name + " — " + Memu.i18n.date(new Date(now), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }),
    created: now, updated: now, messages: [], meta: { variables: {}, timedWorldInfo: {}, note: { text: "", position: "chat", depth: 4, role: 0, interval: 1 } } };
  var g = greetingMessage(char);
  if (g) chat.messages.push(g);
  S.chat = chat;
  S.chatList.unshift(chatSummary(chat));
  return saveChatNow().then(function () { if (!silent) render(); });
}
function openChat(chatId) {
  return Store.get("chats/" + S.char.id + "/" + chatId, null).then(function (chat) {
    if (!chat) { S.chatList = S.chatList.filter(function (c) { return c.id !== chatId; }); return false; }
    chat.meta = chat.meta || {};
    chat.meta.note = Object.assign({ text: "", position: "chat", depth: 4, role: 0, interval: 1 }, chat.meta.note || {});
    dropStalePending(chat);
    S.chat = chat;
    var idx = S.index.find(function (c) { return c.id === S.char.id; });
    if (idx) { idx.lastChat = chat.id; saveIndex(); }
    return true;
  });
}
function goHome() {
  saveChat.flush();
  S.view = "home"; S.char = null; S.chat = null;
  render();
}

/* ====================================================================== active message helpers */
/* An answer that was still streaming when the window was closed or reloaded is saved with `pending`: it showed
   «Thinking…» with typing dots forever. Keep the text that arrived; drop an empty answer or empty new swipe. */
function dropStalePending(chat) {
  chat.messages = (chat.messages || []).filter(function (m) {
    if (!m || !m.pending) return true;
    delete m.pending;
    // r33: a batch of parallel variants interrupted by a reload leaves empty swipes marked pending.
    if (Array.isArray(m.swipes) && Array.isArray(m.swipe_info)) {
      for (var k = m.swipes.length - 1; k >= 0; k--) {
        var inf = m.swipe_info[k];
        if (inf && inf.pending) { delete inf.pending; if (!String(m.swipes[k] || "").trim() && m.swipes.length > 1) { m.swipes.splice(k, 1); m.swipe_info.splice(k, 1); } }
      }
      setSwipe(m, Math.min(m.swipe_id || 0, m.swipes.length - 1));
    }
    if (String(m.mes || "").trim()) return true;
    if (Array.isArray(m.swipes) && m.swipes.length > 1) {
      var i = clamp(m.swipe_id || 0, 0, m.swipes.length - 1);
      m.swipes.splice(i, 1);
      if (Array.isArray(m.swipe_info)) m.swipe_info.splice(i, 1);
      setSwipe(m, m.swipes.length - 1);
      return true;
    }
    return false;
  });
  return chat;
}
function setSwipe(msg, index) {
  if (!msg.swipes || !msg.swipes.length) return;
  msg.swipe_id = clamp(index, 0, msg.swipes.length - 1);
  msg.mes = msg.swipes[msg.swipe_id];
  var info = (msg.swipe_info || [])[msg.swipe_id] || {};
  msg.reasoning = info.reasoning || "";
  msg.model = info.model || msg.model || "";
}
function storeSwipe(msg) {
  if (!msg.swipes) { msg.swipes = [msg.mes]; msg.swipe_id = 0; msg.swipe_info = [{}]; }
  msg.swipes[msg.swipe_id] = msg.mes;
  msg.swipe_info = msg.swipe_info || [];
  msg.swipe_info[msg.swipe_id] = Object.assign(msg.swipe_info[msg.swipe_id] || {}, { reasoning: msg.reasoning || "", model: msg.model || "" });
}
function engineChat(messages) {
  var char = S.char, user = userName();
  return messages.map(function (m) {
    return { is_user: m.role === "user", is_narrator: m.role === "system", is_system: false, hidden: !!m.hidden,
      name: m.role === "user" ? (m.name || user) : (m.name || char.data.name),
      mes: m.greeting ? displaySubst(m.mes) : m.mes, date: m.date, swipes: m.swipes, swipe_id: m.swipe_id,
      images: m.images && m.images.length ? m.images : null, reasoning: m.reasoning };
  });
}

/* ====================================================================== prompt context */
function activeWorlds() {
  var list = [];
  var seen = {};
  function add(id, source) {
    if (!id || seen[id] || !S.worlds[id]) return;
    seen[id] = true;
    list.push({ id: id, name: S.worlds[id].name, entries: S.worlds[id].entries, source: source });
  }
  if (S.chat && S.chat.meta && S.chat.meta.worldId) add(S.chat.meta.worldId, "chat");
  var persona = activePersona();
  if (persona && persona.worldId) add(persona.worldId, "persona");
  if (S.char) {
    var ext = S.char.data.extensions || {};
    add(S.char.worldId || ext.world_id, "char");
    (S.char.extraWorlds || []).forEach(function (id) { add(id, "char"); });
  }
  (S.settings.wi.global || []).forEach(function (id) { add(id, "global"); });
  return list;
}
function modelInfo() { return S.models.find(function (m) { return m.id === S.settings.model; }) || null; }
function buildFor(type, messages, opts) {
  opts = opts || {};
  E.setTokenModel(S.settings.model);
  var persona = activePersona();
  var meta = opts.dryRun ? E.clone(S.chat.meta || {}) : (S.chat.meta || (S.chat.meta = {}));
  var info = modelInfo();
  var gen = S.settings.gen;
  var globals = opts.dryRun ? E.clone(S.settings.globals || {}) : (S.settings.globals || (S.settings.globals = {}));
  var preset = S.preset || E.normalizePreset(null);
  if (gen.contextOverride > 0) preset = Object.assign({}, preset, { openai_max_context: gen.contextOverride });
  var result = E.buildPrompt({
    type: type, preset: preset, char: Object.assign({}, S.char.data), charName: S.char.data.name, user: persona.name || t("default_user_name"),
    persona: { description: persona.description || "", position: persona.position || 0, depth: persona.depth || 2, role: persona.role || 0 },
    chat: engineChat(messages), worlds: activeWorlds(), wiSettings: S.settings.wi, meta: meta, note: meta.note || {},
    settings: { preferCharPrompt: gen.preferCharPrompt, preferCharJailbreak: gen.preferCharJailbreak, pinExamples: gen.pinExamples,
      nameField: gen.nameField, postProcessing: gen.postProcessing, sendPrefill: gen.sendPrefill, clampContext: gen.clampContext,
      modelContext: info && info.context ? info.context : 0, extendedSamplers: gen.extendedSamplers, promptCaching: gen.promptCaching,
      inlineImages: gen.inlineImages, thinking: gen.thinking, stop: gen.stop, sendReasoning: gen.sendReasoning, wiIncludeNames: gen.wiIncludeNames },
    globals: globals, lang: Memu.lang, chatId: S.chat.id, model: S.settings.model, dryRun: !!opts.dryRun,
    quietPrompt: opts.quietPrompt || "", input: opts.input || "",
    regexPrompt: function (text, kind, depth) { return E.runRegexScripts(text, S.settings.regex, kind === "user" ? 1 : 2, { isPrompt: true, depth: depth }); }
  });
  if (!opts.dryRun) saveSettings();
  return result;
}

/* ====================================================================== generation */
function ensureModel() {
  if (S.settings.model && !modelMissing(S.settings.model)) return true;
  var first = fallbackModel();
  if (first) {
    if (S.settings.model) toast(t("model_gone", { old: shortModel(S.settings.model), model: modelTitle(modelRow(first), first) }), "warn");
    S.settings.model = first; saveSettings(); refreshComposerBar(); return true;
  }
  toast(t("choose_model"), "error");
  openDrawer("connection");
  return false;
}
function setBusy(busy) {
  S.busy = busy;
  var app = $("#app"); if (app) app.classList.toggle("busy", busy);
  var send = $("#send"); if (send) { send.innerHTML = icon(busy ? "stop" : "send", 18); send.title = t(busy ? "stop" : "send"); send.classList.toggle("stop", busy); }
}
function stopGeneration() {
  if (S.job && S.job.cancel) { S.job.stopped = true; S.job.cancel(); }
}
function sendMessage() {
  if (S.busy) { stopGeneration(); return; }
  var box = $("#composer-text");
  var text = box ? box.value : "";
  if (/^\/\w+/.test(text.trim()) && runSlash(text.trim())) { box.value = ""; autosize(box); if (S.chat && S.chat.meta) { S.chat.meta.draft = ""; saveChat(); } return; }
  generate("normal", { text: text });
}
function lastMessage() { return S.chat && S.chat.messages.length ? S.chat.messages[S.chat.messages.length - 1] : null; }
// «Continue» glues the new text to the old one. The model's own leading space/newline is the joint; when it sent
// none after a finished sentence, the preset's continue_postfix (or a space) keeps the words apart — r30 live
// chat showed «…чай стынет.Ну, раз уж…». A continuation in the middle of a word or before punctuation stays glued.
function continueJoint(previous, lead, text) {
  if (!previous || !text || /\s$/.test(previous)) return "";
  if (lead) return /\n/.test(lead) ? lead.replace(/[^\n]/g, "") : " ";
  if (!/[.!?…»"*)\]]$/.test(previous) || !/^[\p{L}\p{N}«"*(\[—-]/u.test(text)) return "";
  var postfix = S.preset && typeof S.preset.continue_postfix === "string" ? S.preset.continue_postfix : "";
  return postfix || " ";
}
function generate(type, opts) {
  opts = opts || {};
  if (S.busy || !S.char || !S.chat) return Promise.resolve();
  if (!ensureModel()) return Promise.resolve();
  var chat = S.chat;
  var messages = chat.messages;
  var box = $("#composer-text");
  var target = null, promptMessages, buildType = type, swipeIndex = -1, previousText = "";
  if (type === "normal") {
    var text = String(opts.text || "");
    var images = opts.images || (S.pendingImages || []).slice();
    if (text.trim() || images.length) {
      var persona = activePersona();
      text = sendSubst(E.runRegexScripts(text, S.settings.regex, 1, {}), messages);
      messages.push({ id: E.uid("m"), role: "user", name: persona.name || t("default_user_name"), personaId: persona.id || "", mes: text, date: Date.now(), images: images });
      if (box && opts.fromComposer !== false) { box.value = ""; autosize(box); if (chat.meta) chat.meta.draft = ""; }
      S.pendingImages = []; renderAttachments();
    }
    promptMessages = messages.slice();
    target = { id: E.uid("m"), role: "assistant", name: S.char.data.name, mes: "", swipes: [""], swipe_id: 0, swipe_info: [{}], date: Date.now(), reasoning: "", pending: true };
    messages.push(target);
  } else if (type === "swipe" || type === "regenerate") {
    var last = lastMessage();
    if (!last || last.role !== "assistant") { buildType = "normal"; return generate("normal", { text: "" }); }
    if (!last.swipes) { last.swipes = [last.mes]; last.swipe_id = 0; last.swipe_info = [{}]; }
    storeSwipe(last);
    promptMessages = messages.slice(0, -1);
    target = last;
    target.swipes.push(""); target.swipe_info.push({});
    swipeIndex = target.swipes.length - 1;
    setSwipe(target, swipeIndex);
    target.pending = true; target.greeting = false;
    buildType = type === "regenerate" ? "regenerate" : "swipe";
  } else if (type === "continue") {
    var lm = lastMessage();
    if (!lm) return Promise.resolve();
    target = lm;
    previousText = lm.mes;
    promptMessages = messages.slice();
    target.pending = true;
  } else if (type === "impersonate") {
    promptMessages = messages.slice();
    target = null;
  }
  renderMessages();
  scrollToBottom(true);
  var built;
  try {
    built = buildFor(buildType, promptMessages.filter(function (m) { return !m.pending || m === target && type === "continue"; }).map(function (m) { return m === target && type === "continue" ? Object.assign({}, m, { pending: false }) : m; }));
  } catch (error) {
    finishError(error);
    return Promise.resolve();
  }
  S.lastPrompt = { at: Date.now(), type: type, built: built, model: S.settings.model };
  if (target) target.promptRef = S.lastPrompt.at;
  rememberPrompt(target, built);
  updateTokenBadge(built);
  if (built.warnings.indexOf("over_budget") >= 0) toast(t("over_budget"), "warn");
  var wire = built.messages.map(function (m) { var r = { role: m.role, content: m.content }; if (m.name) r.name = m.name; return r; });
  var raw = "";
  var reasoningRaw = "";
  var started = Date.now();
  var gen = S.settings.gen;
  var stream = gen.streaming !== false;
  var prefill = built.prefill || "";
  setBusy(true);
  function apply(final) {
    var text = raw;
    var reasoning = reasoningRaw;
    if (gen.autoParseReasoning) {
      var split = E.splitReasoning(text, gen.reasoningPrefix, gen.reasoningSuffix);
      if (split.reasoning || split.open) { text = split.text; reasoning = (reasoning ? reasoning + "\n" : "") + split.reasoning; }
    }
    var lead = /^\s*/.exec(text)[0];
    if (final || type === "continue") text = text.replace(/^\s+/, "");
    if (type === "impersonate") {
      if (box) { box.value = text; autosize(box); }
      return;
    }
    if (type === "continue") target.mes = previousText + continueJoint(previousText, lead, text) + text;
    else target.mes = text;
    target.reasoning = type === "continue" ? (target.reasoning || "") + reasoning : reasoning;
    if (stream || final) updateMessageNode(target);
  }
  // Phase 2: one render per batch of deltas, not per token. Every render re-formats the whole answer and
  // reasoning, so rendering each token made a long answer O(n²): after a few thousand tokens the module froze
  // and even «Stop» stopped responding. The pause grows with the cost of the last render.
  var applyTimer = 0, applyCost = 0;
  function scheduleApply() {
    if (!stream || applyTimer) return;
    applyTimer = setTimeout(function () {
      applyTimer = 0;
      var t0 = Date.now();
      apply(false);
      applyCost = Date.now() - t0;
    }, Math.min(800, Math.max(33, applyCost * 3)));
  }
  function flushApply() { if (applyTimer) { clearTimeout(applyTimer); applyTimer = 0; } }
  // Phase 2: some providers leak a model's control tokens as text (DeepSeek on «Continue» answered
  // "…home.<｜begin▁of▁sentence｜># Increasingly, people are using…" — a whole unrelated document). Everything from
  // such a token on is not the answer: cut it and stop the stream.
  var leaked = false;
  function cutLeak() {
    var at = E.findLeakedToken(raw);
    if (at < 0) return false;
    raw = raw.slice(0, at); leaked = true;
    return true;
  }
  var job = Memu.complete({ model: S.settings.model, messages: wire, params: built.params }, {
    onDelta: function (chunk) {
      if (leaked) return;
      raw += chunk;
      if (cutLeak() && job && job.cancel) { job.stopped = true; job.cancel(); }
      scheduleApply();
    },
    onReasoning: function (chunk) { reasoningRaw += chunk; scheduleApply(); },
    onUsage: function (usage) { S.lastUsage = usage; }
  });
  S.job = job;
  return job.then(function (result) {
    if (!leaked && result && typeof result.text === "string" && result.text.length >= raw.length) { raw = result.text; cutLeak(); }
    if (result && result.reasoning && !reasoningRaw) reasoningRaw = result.reasoning;
    finishOk(result || {});
  }, function (error) {
    if (job.stopped) finishOk({ stopped: true });
    else finishError(error);
  });

  function finishOk(result) {
    flushApply();
    apply(true);
    setBusy(false);
    S.job = null;
    if (type === "impersonate") { if (box) box.focus(); return; }
    var finalText = E.runRegexScripts(target.mes, S.settings.regex, 2, {});
    if (gen.showPrefill && prefill && type !== "continue") finalText = prefill + finalText;
    target.mes = finalText;
    delete target.pending;
    if (!target.mes.trim() && !result.stopped && type !== "continue") {
      toast(t("empty_answer"), "warn");
      if (type === "normal") messages.splice(messages.indexOf(target), 1);
      else if (swipeIndex >= 0) { target.swipes.splice(swipeIndex, 1); target.swipe_info.splice(swipeIndex, 1); setSwipe(target, target.swipes.length - 1); }
    } else {
      target.model = S.settings.model;
      target.date = target.date || Date.now();
      storeSwipe(target);
      var info = target.swipe_info[target.swipe_id];
      info.model = S.settings.model; info.date = Date.now(); info.gen_ms = Date.now() - started;
      if (result.usage) info.usage = result.usage;
      if (result.finish_reason) info.finish = result.finish_reason;
      if (!gen.keepSwipes && swipeIndex > 0) {
        target.swipes = [target.mes]; target.swipe_info = [info]; target.swipe_id = 0;
      }
    }
    renderMessages();
    scrollToBottom();
    saveChatNow();
    updateTokenBadge(built, result.usage);
    if (result.finish_reason === "length" && gen.autoContinue && type !== "impersonate") setTimeout(function () { generate("continue"); }, 200);
  }
  function finishError(error) {
    flushApply();
    if (raw || reasoningRaw) apply(true);  // keep the text that arrived before the error
    setBusy(false);
    S.job = null;
    var message = error && error.message ? error.message : String(error);
    if (target) {
      delete target.pending;
      if (type === "normal" && !raw.trim()) messages.splice(messages.indexOf(target), 1);
      else if ((type === "swipe" || type === "regenerate") && !raw.trim()) {
        target.swipes.splice(swipeIndex, 1); target.swipe_info.splice(swipeIndex, 1); setSwipe(target, target.swipes.length - 1);
      } else if (type === "continue") { /* keep what was added */ }
      else storeSwipe(target);
    }
    renderMessages();
    saveChatNow();
    showError(message);
  }
}
var promptMemory = new Map();
function rememberPrompt(msg, built) {
  if (!msg) return;
  promptMemory.set(msg.id + ":" + (msg.swipe_id || 0), built);
  if (promptMemory.size > 60) promptMemory.delete(promptMemory.keys().next().value);
}
function showError(message) {
  var log = $("#messages");
  toast(message, "error");
  if (!log) return;
  var node = h("div", { class: "ch-error" }, [h("span", { html: icon("info", 16) }), h("span", { text: message }),
    h("button", { class: "mu-btn small ghost", type: "button", text: t("retry"), onclick: function () { node.remove(); generate(lastMessage() && lastMessage().role === "assistant" ? "swipe" : "normal", { text: "" }); } })]);
  log.appendChild(node);
  scrollToBottom(true);
}

/* ---------------------------------------------------------------- slash commands (subset) */
function runSlash(text) {
  var m = /^\/(\w+)\s*([\s\S]*)$/.exec(text);
  if (!m) return false;
  var cmd = m[1].toLowerCase(), arg = m[2];
  switch (cmd) {
    case "sys": case "narrator":
      S.chat.messages.push({ id: E.uid("m"), role: "system", name: t("narrator"), mes: arg, date: Date.now() }); renderMessages(); saveChatNow(); return true;
    case "send":
      S.chat.messages.push({ id: E.uid("m"), role: "user", name: userName(), mes: arg, date: Date.now() }); renderMessages(); saveChatNow(); return true;
    case "sendas": case "char":
      S.chat.messages.push({ id: E.uid("m"), role: "assistant", name: S.char.data.name, mes: arg, swipes: [arg], swipe_id: 0, swipe_info: [{}], date: Date.now() }); renderMessages(); saveChatNow(); return true;
    case "continue": case "cont": generate("continue"); return true;
    case "regen": case "regenerate": generate("regenerate"); return true;
    case "swipe": generate("swipe"); return true;
    case "imp": case "impersonate": generate("impersonate"); return true;
    case "newchat": startNewChat(); return true;
    case "note": S.chat.meta.note.text = arg; saveChatNow(); toast(t("note_saved"), "ok"); return true;
    case "setvar": {
      var parts = arg.split("::"); if (parts.length > 1) { S.chat.meta.variables[parts[0].trim()] = parts.slice(1).join("::"); saveChatNow(); toast(t("var_set"), "ok"); }
      return true;
    }
    case "hide": case "unhide": {
      var idx = parseInt(arg, 10); var msg = S.chat.messages[idx];
      if (msg) { msg.hidden = cmd === "hide"; renderMessages(); saveChatNow(); }
      return true;
    }
    case "help":
      openModal({ title: t("slash_help_title"), icon: "info", content: h("pre", { class: "ch-pre", text: t("slash_help") }) });
      return true;
    default: return false;
  }
}

/* ====================================================================== chat rendering */
function avatarFor(msg) {
  if (msg.role === "user") {
    var p = personaOf(msg.personaId) || activePersona();
    return (p && p.avatar) || defaultAvatar(p && p.name || "U", "user" + (p && p.id));
  }
  if (msg.role === "system") return "";
  return charAvatar(S.char);
}
function renderMessages() {
  var log = $("#messages");
  if (!log || !S.chat) return;
  var keepBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  log.innerHTML = "";
  var messages = S.chat.messages;
  var lastAssistant = -1;
  for (var i = messages.length - 1; i >= 0; i--) if (messages[i].role === "assistant") { lastAssistant = i; break; }
  if (!messages.length) {
    log.appendChild(h("div", { class: "ch-chat-empty" }, [
      h("img", { class: "ch-empty-ava", src: charAvatar(S.char), alt: "" }),
      h("div", { class: "ch-empty-name", text: S.char.data.name }),
      h("div", { class: "mu-muted", text: t("chat_empty_hint") })
    ]));
  }
  // Long chats: only the newest messages are rendered; «show earlier» grows the window.
  if (S.chat !== S.renderedChat) { S.renderedChat = S.chat; S.renderLimit = 120; }
  var first = Math.max(0, messages.length - (S.renderLimit || 120));
  if (first > 0) {
    log.appendChild(h("button", { class: "mu-btn small ghost ch-earlier", type: "button", text: t("show_earlier", { count: first }), onclick: function () {
      var before = log.scrollHeight;
      S.renderLimit = (S.renderLimit || 120) + 120;
      renderMessages();
      log.scrollTop = log.scrollHeight - before;
    } }));
  }
  var frag = document.createDocumentFragment();
  for (var mi = first; mi < messages.length; mi++) frag.appendChild(messageNode(messages[mi], mi, mi === messages.length - 1, mi === lastAssistant));
  log.appendChild(frag);
  if (keepBottom) scrollToBottom(true);
  updateChatHeader();
}
function messageNode(msg, index, isLast, isLastAssistant) {
  var ui = S.settings.ui;
  var node = h("article", { class: "ch-msg " + msg.role + (msg.hidden ? " hidden-msg" : "") + (msg.pending ? " pending" : "") + (msg.bookmark ? " marked" : ""), "data-id": msg.id, "data-index": index });
  var avatar = avatarFor(msg);
  if (msg.role !== "system" && !ui.hideAvatars) {
    var ava = h("div", { class: "ch-ava" }, [h("img", { src: avatar, alt: "", loading: "lazy" })]);
    ava.addEventListener("click", function () { if (msg.role === "assistant") openCharacterEditor(S.char.id); else openPersonas(); });
    node.appendChild(ava);
  }
  var body = h("div", { class: "ch-body" });
  var name = msg.role === "user" ? (msg.name || userName()) : msg.role === "system" ? (msg.name || t("narrator")) : (msg.name || S.char.data.name);
  var head = h("div", { class: "ch-head" }, [
    h("span", { class: "ch-name", text: name }),
    ui.showIds ? h("span", { class: "ch-meta", text: "#" + index }) : null,
    ui.showTimestamps && msg.date ? h("span", { class: "ch-meta", text: fmtDate(msg.date) }) : null,
    ui.showModel && msg.role === "assistant" && msg.model ? h("span", { class: "ch-meta ch-model", title: msg.model, text: modelRow(msg.model) ? modelTitle(modelRow(msg.model)) : shortModel(msg.model) }) : null,
    msg.hidden ? h("span", { class: "ch-tag", html: icon("eyeoff", 12) + " " + esc(t("hidden_tag")) }) : null,
    msg.bookmark ? h("span", { class: "ch-tag mark", html: icon("bookmark", 12) + " " + esc(msg.bookmarkName || t("bookmark")) }) : null,
    h("span", { class: "mu-grow" }),
    messageActions(msg, index, isLast, isLastAssistant)
  ]);
  body.appendChild(head);
  if (msg.reasoning || (msg.pending && S.settings.gen.thinking !== "off" && !msg.mes && msg.role === "assistant")) {
    var think = h("details", { class: "ch-think" });
    if (ui.reasoningOpen || (msg.pending && !msg.mes)) think.open = true;
    think.appendChild(h("summary", { html: icon("brain", 14) + "<span>" + esc(msg.pending && !msg.mes ? t("thinking") : t("thoughts")) + "</span>" }));
    think.appendChild(h("div", { class: "ch-think-body", html: formatText(msg.reasoning || "", { placement: 6 }) }));
    body.appendChild(think);
  }
  var text = h("div", { class: "ch-text" });
  var shown = msg.greeting ? displaySubst(msg.mes) : msg.mes;
  if (msg.pending && !msg.mes) text.innerHTML = '<span class="ch-typing"><i></i><i></i><i></i></span>';
  else text.innerHTML = formatText(shown, { placement: msg.role === "user" ? 1 : 2, depth: S.chat.messages.length - 1 - index });
  body.appendChild(text);
  if (msg.images && msg.images.length) {
    body.appendChild(h("div", { class: "ch-images" }, msg.images.map(function (src) {
      var img = h("img", { src: src, alt: "" });
      img.addEventListener("click", function () { openModal({ title: t("image"), wide: true, content: h("img", { class: "ch-image-full", src: src, alt: "" }) }); });
      return img;
    })));
  }
  if (msg.role === "assistant" && !msg.swipes && isLast && !msg.pending) { msg.swipes = [msg.mes]; msg.swipe_id = 0; msg.swipe_info = [{}]; }
  if (msg.role === "assistant" && msg.swipes && (msg.swipes.length > 1 || isLastAssistant && isLast)) {
    var sw = h("div", { class: "ch-swipes" });
    if (msg.swipes.length > 1 || isLast) {
      var leftBtn = iconBtn("left", "swipe_left", function () { swipe(msg, -1); });
      leftBtn.disabled = msg.swipe_id <= 0 || S.busy;
      sw.appendChild(leftBtn);
      sw.appendChild(h("span", { class: "ch-swipe-count", text: (msg.swipe_id + 1) + " / " + msg.swipes.length }));
      if (isLast) {
        var rightBtn = iconBtn("right", "swipe_right", function () { if (msg.swipe_id >= msg.swipes.length - 1) swipeForward(msg); else swipe(msg, 1); });
        rightBtn.disabled = S.busy;
        var tools = canMakeVariants();
        if (tools) {
          // r33: right-click — several new variants in parallel (count remembered).
          rightBtn.title = t("swipe_right_hint");
          rightBtn.setAttribute("aria-label", t("swipe_right_hint"));
          rightBtn.addEventListener("contextmenu", function (e) { if (!S.busy) variantMenu(rightBtn, e); else e.preventDefault(); });
          if (S.settings.ui.variantLeftClick && variantCount() > 1) rightBtn.appendChild(h("span", { class: "ch-swipe-n", text: "×" + variantCount() }));
        }
        sw.appendChild(rightBtn);
      } else {
        var rb = iconBtn("right", "swipe_right", function () { swipe(msg, 1); });
        rb.disabled = msg.swipe_id >= msg.swipes.length - 1;
        sw.appendChild(rb);
      }
    }
    var info = (msg.swipe_info || [])[msg.swipe_id] || {};
    if (S.settings.ui.showTokens && (info.usage || info.gen_ms)) {
      var bits = [];
      if (info.usage && info.usage.completion_tokens) bits.push(fmtNum(info.usage.completion_tokens) + " " + t("tok"));
      if (info.gen_ms) bits.push((info.gen_ms / 1000).toFixed(1) + " s");
      sw.appendChild(h("span", { class: "ch-meta", text: bits.join(" · ") }));
    }
    // r33: «Regenerate» / «Continue» are visible under the newest reply (they used to hide in a hover toolbar).
    if (isLast && canMakeVariants() && !msg.pending) {
      sw.appendChild(h("span", { class: "mu-grow" }));
      var regen = h("button", { class: "ch-swtool", type: "button", title: t("regenerate_title"), "aria-label": t("regenerate_title"),
        html: icon("refresh", 15) + "<span>" + esc(t("retry_reply")) + "</span>", onclick: function () { generate("regenerate"); } });
      regen.disabled = S.busy;
      regen.addEventListener("contextmenu", function (e) { if (!S.busy) variantMenu(regen, e); else e.preventDefault(); });
      var cont = h("button", { class: "ch-swtool", type: "button", title: t("continue_title"), "aria-label": t("continue_title"),
        html: icon("forward", 15) + "<span>" + esc(t("continue")) + "</span>", onclick: function () { generate("continue"); } });
      cont.disabled = S.busy;
      var more = h("button", { class: "ch-swtool icon", type: "button", title: t("variants_menu_title"), "aria-label": t("variants_menu_title"), "aria-haspopup": "dialog",
        html: icon("layers", 15) });
      more.disabled = S.busy;
      more.addEventListener("click", function (e) { variantMenu(more, null); e.stopPropagation(); });
      more.addEventListener("contextmenu", function (e) { if (!S.busy) variantMenu(more, e); else e.preventDefault(); });
      sw.appendChild(regen); sw.appendChild(cont); sw.appendChild(more);
    }
    body.appendChild(sw);
    var strip = variantStrip(msg);
    if (strip) body.appendChild(strip);
  }
  node.appendChild(body);
  text.addEventListener("click", function (e) {
    var copyBtn = e.target.closest("[data-copy]");
    if (copyBtn) { var code = copyBtn.closest("pre").querySelector("code"); Memu.copy(code ? code.textContent : ""); }
  });
  text.addEventListener("dblclick", function () { if (!S.busy && S.settings.ui.dblEdit !== false) startEdit(msg); });
  return node;
}
function shortModel(id) {
  // "vendor/model" → "model"; "gpt://folder/deepseek-v4.1-flash/latest" → "deepseek-v4.1-flash" (a version tag
  // such as latest/rc at the end says nothing about the model).
  var parts = String(id || "").split("/").filter(Boolean);
  while (parts.length > 1 && /^(latest|rc|deprecated|stable|preview)$/i.test(parts[parts.length - 1])) parts.pop();
  return parts.length ? parts[parts.length - 1] : String(id || "");
}
function updateMessageNode(msg) {
  var node = document.querySelector('.ch-msg[data-id="' + msg.id + '"]');
  if (!node) { renderMessages(); return; }
  var text = node.querySelector(".ch-text");
  if (text) text.innerHTML = msg.mes ? formatText(msg.mes) + (msg.pending ? '<span class="ch-caret"></span>' : "") : (msg.pending ? '<span class="ch-typing"><i></i><i></i><i></i></span>' : "");
  if (msg.reasoning) {
    var think = node.querySelector(".ch-think");
    if (!think) {
      think = h("details", { class: "ch-think", open: true }, [h("summary", { html: icon("brain", 14) + "<span>" + esc(t("thinking")) + "</span>" }), h("div", { class: "ch-think-body" })]);
      node.querySelector(".ch-body").insertBefore(think, text);
    }
    think.querySelector(".ch-think-body").innerHTML = formatText(msg.reasoning, { placement: 6 });
    var label = think.querySelector("summary span");
    if (label) label.textContent = msg.mes ? t("thoughts") : t("thinking");
    if (msg.mes && msg.pending && !S.settings.ui.reasoningOpen) think.open = false;
  }
  scrollToBottom();
}
function scrollToBottom(force) {
  var log = $("#messages");
  if (!log) return;
  var near = log.scrollHeight - log.scrollTop - log.clientHeight < 160;
  if (force || near) log.scrollTop = log.scrollHeight;
}
function swipe(msg, dir) {
  if (S.busy) return;
  var messages = S.chat.messages;
  var isLast = messages[messages.length - 1] === msg;
  storeSwipe(msg);
  if (dir > 0 && msg.swipe_id >= msg.swipes.length - 1) {
    if (!isLast) return;
    if (msg.greeting && msg.swipe_info && msg.swipe_info.every(function (i) { return i && i.greeting; }) && messages.length === 1) {
      return; // greetings: only the alternatives the card has
    }
    generate("swipe");
    return;
  }
  setSwipe(msg, msg.swipe_id + dir);
  renderMessages();
  saveChat();
}
function messageActions(msg, index, isLast, isLastAssistant) {
  var wrap = h("div", { class: "ch-actions" });
  wrap.appendChild(iconBtn("copy", "copy", function () { Memu.copy(msg.mes); }));
  wrap.appendChild(iconBtn("edit", "edit", function () { startEdit(msg); }));
  if (isLast && msg.role === "assistant") wrap.appendChild(iconBtn("refresh", "regenerate", function () { generate("regenerate"); }));
  var more = iconBtn("dots", "more", function () {
    popMenu(more, [
      isLast ? { icon: "forward", label: t("continue"), onClick: function () { generate("continue"); } } : null,
      { icon: "branch", label: t("branch_here"), onClick: function () { branchFrom(index); } },
      { icon: "bookmark", label: msg.bookmark ? t("bookmark_remove") : t("bookmark_add"), onClick: function () { toggleBookmark(msg); } },
      { icon: msg.hidden ? "eye" : "eyeoff", label: msg.hidden ? t("unhide") : t("hide_from_prompt"), onClick: function () { msg.hidden = !msg.hidden; renderMessages(); saveChat(); } },
      { icon: "code", label: t("prompt_used"), disabled: !promptMemory.has(msg.id + ":" + (msg.swipe_id || 0)), onClick: function () { openInspector(promptMemory.get(msg.id + ":" + (msg.swipe_id || 0))); } },
      msg.role === "assistant" && msg.swipes && msg.swipes.length > 1 ? { icon: "x", label: t("delete_swipe"), onClick: function () { deleteSwipe(msg); } } : null,
      { icon: "up", label: t("move_up"), disabled: index === 0, onClick: function () { moveMessage(index, -1); } },
      { icon: "down", label: t("move_down"), disabled: isLast, onClick: function () { moveMessage(index, 1); } },
      { sep: true },
      { icon: "trash", label: t("delete"), danger: true, onClick: function () { deleteMessage(index); } },
      { icon: "trash", label: t("delete_after"), danger: true, onClick: function () { deleteFrom(index); } }
    ]);
  });
  wrap.appendChild(more);
  return wrap;
}
function moveMessage(index, dir) {
  var list = S.chat.messages, j = index + dir;
  if (j < 0 || j >= list.length) return;
  var tmp = list[index]; list[index] = list[j]; list[j] = tmp;
  renderMessages(); saveChat();
}
function deleteSwipe(msg) {
  if (!msg.swipes || msg.swipes.length < 2) return;
  msg.swipes.splice(msg.swipe_id, 1);
  (msg.swipe_info || []).splice(msg.swipe_id, 1);
  setSwipe(msg, Math.min(msg.swipe_id, msg.swipes.length - 1));
  renderMessages(); saveChat();
}
function deleteMessage(index) {
  confirmBox(t("delete_message_q"), { danger: true, ok: t("delete") }).then(function (ok) {
    if (!ok) return;
    S.chat.messages.splice(index, 1);
    renderMessages(); saveChat();
  });
}
function deleteFrom(index) {
  var n = S.chat.messages.length - index;
  confirmBox(t("delete_after_q", { count: n }), { danger: true, ok: t("delete") }).then(function (ok) {
    if (!ok) return;
    S.chat.messages.splice(index);
    renderMessages(); saveChat();
  });
}
function toggleBookmark(msg) {
  if (msg.bookmark) { msg.bookmark = false; delete msg.bookmarkName; renderMessages(); saveChat(); return; }
  promptBox(t("bookmark_name"), String(msg.mes || "").slice(0, 40), { icon: "bookmark" }).then(function (name) {
    if (name === null) return;
    msg.bookmark = true; msg.bookmarkName = name || t("bookmark");
    renderMessages(); saveChat();
  });
}
function branchFrom(index) {
  saveChat.flush();
  var source = S.chat;
  var now = Date.now();
  var chat = { id: E.uid("chat"), charId: S.char.id, title: t("branch_title", { title: source.title || "" }), created: now, updated: now,
    parent: source.id, parentIndex: index, messages: E.clone(source.messages.slice(0, index + 1)), meta: E.clone(source.meta || {}) };
  chat.messages.forEach(function (m) { delete m.pending; });
  S.chat = chat;
  S.chatList.unshift(chatSummary(chat));
  saveChatNow().then(function () { render(); toast(t("branch_created"), "ok"); });
}
function startEdit(msg) {
  if (S.busy) return;
  var node = document.querySelector('.ch-msg[data-id="' + msg.id + '"]');
  if (!node) return;
  var text = node.querySelector(".ch-text");
  if (!text || node.classList.contains("editing")) return;
  node.classList.add("editing");
  var area = h("textarea", { class: "mu-textarea ch-edit-area" });
  area.value = msg.mes;
  var reasoningArea = null;
  var wrap = h("div", { class: "ch-edit" }, [area]);
  if (msg.role === "assistant" && msg.reasoning) {
    reasoningArea = h("textarea", { class: "mu-textarea ch-edit-area small", placeholder: t("thoughts") });
    reasoningArea.value = msg.reasoning;
    wrap.insertBefore(h("label", { class: "mu-field", text: t("thoughts") }), area);
    wrap.insertBefore(reasoningArea, area);
  }
  var bar = h("div", { class: "mu-row" }, [
    h("button", { class: "mu-btn small primary", type: "button", html: icon("check", 15) + esc(t("save")), onclick: save }),
    h("button", { class: "mu-btn small ghost", type: "button", text: t("cancel"), onclick: cancel }),
    h("span", { class: "mu-grow" }),
    h("span", { class: "mu-muted ch-small", text: t("edit_hint") })
  ]);
  wrap.appendChild(bar);
  text.replaceWith(wrap);
  autosize(area);
  area.focus();
  area.addEventListener("input", function () { autosize(area); });
  area.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
    if (e.key === "Escape") { e.preventDefault(); cancel(); }
  });
  function save() {
    msg.mes = area.value;
    if (reasoningArea) msg.reasoning = reasoningArea.value;
    if (msg.greeting) msg.greeting = false;
    if (msg.swipes) storeSwipe(msg);
    renderMessages(); saveChat();
  }
  function cancel() { renderMessages(); }
}
function autosize(area) {
  if (!area) return;
  if (area.id === "composer-text") updateDraftTokens();
  area.style.height = "auto";
  area.style.height = Math.min(area.scrollHeight + 2, area.classList.contains("ch-edit-area") ? 600 : 260) + "px";
}

/* ====================================================================== r33: composer bar — model + reasoning level */
function textModels() { return S.models.filter(function (m) { return !m.image && !m.video && !m.audio; }); }
function modelRow(id) { return S.models.find(function (m) { return m.id === id; }) || null; }
// «reform-qwen3.8-flash» under the group «REFORM» reads as «qwen3.8-flash»: the Custom connection's prefix repeats the group.
function modelTitle(m, id) {
  if (!m) return shortModel(id || "");
  var name = m.name || shortModel(m.id);
  var prefix = String(m.provider_label || "").toLowerCase().replace(/\s+/g, "-");
  if (prefix && name.toLowerCase().indexOf(prefix + "-") === 0 && name.length > prefix.length + 1) name = name.slice(prefix.length + 1);
  return name;
}
function providerTitle(m) {
  if (!m) return "";
  if (m.provider_label) return m.provider_label;
  if (m.provider === "custom" || !m.provider) return String(m.id || "").split("-")[0] || "custom";
  return m.provider;
}
function fmtContext(n) {
  n = Number(n) || 0;
  if (!n) return "";
  return n >= 1000000 ? (Math.round(n / 100000) / 10) + "M" : Math.round(n / 1000) + "K";
}
// The chat's model is unusable when the list loaded and it is not there: switched off in MemuAI Proxy, hidden in
// MemuAI, or its provider was removed. The Proxy refuses a disabled model («disabled in the panel»), so the module
// must not keep sending it.
function modelMissing(id) { return !!id && S.modelsLoaded && textModels().length > 0 && !modelRow(id); }
function fallbackModel() {
  var rows = textModels();
  var def = rows.find(function (m) { return m.default; });
  return (def || rows[0] || {}).id || "";
}
function setChatModel(id, quiet) {
  if (!id || id === S.settings.model) { refreshComposerBar(); return; }
  S.settings.model = id;
  saveSettings();
  E.setTokenModel(id);
  refreshTokens();
  refreshComposerBar();
  if (S.drawerOpen && S.drawerTab === "connection") renderDrawer();
  if (!quiet) toast(t("model_switched", { model: modelTitle(modelRow(id), id) }), "ok");
}
function refreshComposerBar() {
  var holder = $("#composer-chips");
  if (!holder) return;
  holder.innerHTML = "";
  var id = S.settings.model;
  var m = modelRow(id);
  var missing = modelMissing(id);
  var chip = h("button", { class: "ch-cchip ch-model-chip" + (missing ? " warn" : "") + (!id ? " empty" : ""), id: "model-chip", type: "button",
    "aria-haspopup": "dialog", title: missing ? t("model_missing_hint", { model: shortModel(id) }) : t("mp_chip_title", { model: id ? modelTitle(m, id) : t("choose_model") }) }, [
    h("span", { class: "ch-cchip-ic", html: icon(missing ? "alert" : "sparkle", 15) }),
    h("span", { class: "ch-cchip-text", text: id ? modelTitle(m, id) : t("choose_model") }),
    h("span", { class: "ch-cchip-caret", html: icon("chevron", 14) })
  ]);
  chip.addEventListener("click", function () {
    openModelPicker(chip, { value: S.settings.model, onPick: function (v) { setChatModel(v); } });
  });
  holder.appendChild(chip);
  if (modelEfforts(m).length) {
    var level = effortValue(m);
    var eff = h("button", { class: "ch-cchip ch-effort-chip" + (level !== "auto" ? " on" : ""), id: "effort-chip", type: "button", "aria-haspopup": "menu",
      title: t("effort_chip_title") + ": " + effortLabel(level) }, [
      h("span", { class: "ch-cchip-ic", html: icon("brain", 15) }),
      h("span", { class: "ch-cchip-text", text: effortLabel(level) }),
      h("span", { class: "ch-cchip-caret", html: icon("chevron", 14) })
    ]);
    eff.addEventListener("click", function () { effortMenu(eff); });
    holder.appendChild(eff);
  }
}
var EFFORTS = ["auto", "off", "min", "low", "medium", "high", "max"];
// r33c: the reasoning levels a model REALLY accepts come with the model list (/api/pg/modules/models →
// reasoning_levels, app/reasoning_levels.py: «auto» first, then «off» / «on» / minimal…max). The preset keeps
// SillyTavern's spelling («min»), the menu and the Proxy speak the canonical one («minimal»).
var DEPTHS = ["minimal", "low", "medium", "high", "xhigh", "max"];
function canonEffort(v) { v = String(v || "auto").toLowerCase(); return v === "min" || v === "minimum" ? "minimal" : v === "maximum" ? "max" : v; }
function storeEffort(v) { return v === "minimal" ? "min" : v; }
// [] — the model has no reasoning control; null — the list carries no per-model levels (an older MemuAI): generic menu.
function modelEfforts(m) {
  if (!m) return [];
  if (Array.isArray(m.reasoning_levels)) {
    var list = m.reasoning_levels.map(canonEffort).filter(function (v) { return v === "auto" || v === "off" || v === "on" || DEPTHS.indexOf(v) >= 0; });
    return list.length > 1 ? list : [];
  }
  return m.reasoning || m.thinking ? EFFORTS.map(canonEffort) : [];
}
// The same choice app/reasoning_levels.clamp makes on the Proxy, so the chip shows what will really be asked for.
function clampEffort(v, options) {
  v = canonEffort(v);
  if (!options.length || v === "auto" || options.indexOf(v) >= 0) return options.indexOf(v) >= 0 ? v : "auto";
  var depths = options.filter(function (x) { return DEPTHS.indexOf(x) >= 0; });
  if (v === "off") return depths[0] || "auto";
  if (v === "on") return depths.indexOf("medium") >= 0 ? "medium" : depths[Math.floor(depths.length / 2)] || "auto";
  if (!depths.length) return options.indexOf("on") >= 0 ? "on" : "auto";
  if (v === "minimal" && options.indexOf("off") >= 0) return "off";
  if (v === "max") return depths[depths.length - 1];
  var i = DEPTHS.indexOf(v);
  return depths.slice().sort(function (x, y) { return (Math.abs(DEPTHS.indexOf(x) - i) - Math.abs(DEPTHS.indexOf(y) - i)) || (DEPTHS.indexOf(y) - DEPTHS.indexOf(x)); })[0];
}
// What the person chose (preset + the «thinking» switch), in canonical spelling.
function effortChoice() {
  var th = S.settings.gen.thinking;
  if (th === "off") return "off";
  var v = canonEffort((S.preset && S.preset.reasoning_effort) || "auto");
  if (th === "on" && v === "auto") return "on";
  return v === "auto" || v === "on" || DEPTHS.indexOf(v) >= 0 ? v : "auto";
}
function effortValue(m) {
  var options = modelEfforts(m === undefined ? modelRow(S.settings.model) : m);
  return options.length ? clampEffort(effortChoice(), options) : effortChoice();
}
function effortLabel(v) { return t("effort_" + (v === "minimal" ? "min" : v)); }
function setEffort(v) {
  var gen = S.settings.gen;
  if (v === "off") gen.thinking = "off";
  else if (v === "on") {
    gen.thinking = "on";
    if (S.preset) { S.preset.reasoning_effort = "auto"; savePreset(); }
  } else {
    if (gen.thinking === "off" || (v === "auto" && gen.thinking === "on")) gen.thinking = "auto";
    if (S.preset) { S.preset.reasoning_effort = storeEffort(v); savePreset(); }
  }
  saveSettings();
  refreshComposerBar();
  if (S.drawerOpen && (S.drawerTab === "connection" || S.drawerTab === "preset")) renderDrawer();
}
function effortMenu(anchor) {
  var m = modelRow(S.settings.model);
  var options = modelEfforts(m);
  var cur = effortValue(m);
  var def = canonEffort(m && m.reasoning_default);
  var menu = popMenu(anchor, [{ head: t("effort_chip_title") }].concat(options.map(function (v) {
    var hint = v === "auto" ? t("effort_auto_hint") : v === "off" ? t("effort_off_hint") : v === def && def !== "auto" ? t("effort_default_hint") : "";
    return { icon: v === cur ? "check" : "", label: effortLabel(v), active: v === cur, hint: hint,
      onClick: function () { setEffort(v); } };
  })));
  placeNear(menu, anchor);
  menuKeys(menu);
}
// popMenu right-aligns to the anchor's right edge; the composer chips sit at the bottom-left — open upwards, left-aligned.
function placeNear(menu, anchor, point) {
  var r = point ? { left: point.x, right: point.x, top: point.y, bottom: point.y } : anchor.getBoundingClientRect();
  var mw = menu.offsetWidth, mh = menu.offsetHeight;
  var x = clamp(r.left, 6, Math.max(6, window.innerWidth - mw - 6));
  var below = window.innerHeight - r.bottom - 6, above = r.top - 6;
  var y = (below >= mh + 6 || below >= above) ? r.bottom + 6 : r.top - mh - 6;
  y = clamp(y, 6, Math.max(6, window.innerHeight - mh - 6));
  menu.style.left = x + "px"; menu.style.top = y + "px";
}
// Arrow keys / Home / End inside a popMenu (the first item gets focus, so Enter works at once).
function menuKeys(menu) {
  var items = function () { return $$(".ch-menu-item:not(:disabled)", menu); };
  var first = items().find(function (b) { return b.classList.contains("active"); }) || items()[0];
  if (first) first.focus();
  menu.addEventListener("keydown", function (e) {
    var list = items(), i = list.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); var n = list[(i + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length]; if (n) n.focus(); }
    else if (e.key === "Home" || e.key === "End") { e.preventDefault(); var m = list[e.key === "Home" ? 0 : list.length - 1]; if (m) m.focus(); }
    else if (e.key === "Tab") closeMenus();
  });
}
function updateDraftTokens() {
  var el = $("#draft-tokens"), box = $("#composer-text");
  if (!el || !box) return;
  var text = box.value.trim();
  if (!text) { el.textContent = ""; el.title = ""; return; }
  E.setTokenModel(S.settings.model);
  var n = E.estimateTokens(text);
  el.textContent = "≈ " + fmtNum(n);
  el.title = t("draft_tokens_title", { count: fmtNum(n) });
}

/* ---------------------------------------------------------------- model picker (search, groups, capabilities) */
function openModelPicker(anchor, opts) {
  opts = opts || {};
  closeMenus();
  var current = opts.value !== undefined ? opts.value : S.settings.model;
  var menu = h("div", { class: "ch-menu ch-mp", role: "dialog", "aria-label": t("mp_title") });
  var search = h("input", { class: "mu-input ch-mp-search", type: "search", placeholder: t("mp_search"), "aria-label": t("mp_search"), autocomplete: "off", spellcheck: "false" });
  var list = h("div", { class: "ch-mp-list", role: "listbox", "aria-label": t("mp_title") });
  var buttons = [], active = 0;
  menu.appendChild(h("div", { class: "ch-mp-top" }, [h("span", { class: "ch-mp-search-ic", html: icon("search", 15) }), search]));
  menu.appendChild(list);
  var foot = h("div", { class: "ch-mp-foot" }, [
    h("button", { class: "ch-mp-link", type: "button", html: icon("refresh", 14) + "<span>" + esc(t("reload_models")) + "</span>", onclick: function (e) {
      e.stopPropagation();
      list.innerHTML = ""; list.appendChild(h("div", { class: "ch-mp-empty", html: '<span class="mu-spinner"></span>' }));
      loadModels().then(function () { draw(); refreshComposerBar(); });
    } }),
    opts.noSettings ? null : h("button", { class: "ch-mp-link", type: "button", html: icon("settings", 14) + "<span>" + esc(t("mp_settings")) + "</span>", onclick: function () { closeMenus(); openDrawer("connection"); } })
  ]);
  menu.appendChild(foot);
  function matches(m, q) {
    if (!q) return true;
    var hay = (m.name + " " + m.id + " " + providerTitle(m)).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }
  function draw() {
    var q = search.value.trim().toLowerCase();
    list.innerHTML = ""; buttons = [];
    var rows = textModels().filter(function (m) { return matches(m, q); });
    var groups = [], byName = {};
    rows.forEach(function (m) { var g = providerTitle(m); if (!byName[g]) { byName[g] = []; groups.push(g); } byName[g].push(m); });
    groups.forEach(function (g) {
      list.appendChild(h("div", { class: "ch-mp-group", role: "presentation", text: g }));
      byName[g].forEach(function (m) {
        var on = m.id === current;
        var b = h("button", { class: "ch-mp-item" + (on ? " current" : ""), type: "button", role: "option", "aria-selected": String(on), title: m.id }, [
          h("span", { class: "ch-mp-check", html: on ? icon("check", 15) : "" }),
          h("span", { class: "ch-mp-text" }, [
            h("span", { class: "ch-mp-name", text: modelTitle(m) }),
            // r33c: the full id beside the readable name, as everywhere in MemuAI.
            modelTitle(m) !== m.id ? h("span", { class: "ch-mp-id", text: m.id }) : null
          ]),
          h("span", { class: "ch-mp-badges" }, [
            (m.badges || []).indexOf("free") >= 0 ? h("span", { class: "ch-mp-tag", text: "free" }) : null,
            m.reasoning || m.thinking ? h("span", { class: "ch-mp-badge", title: t("mp_reasoning"), html: icon("brain", 13) }) : null,
            m.vision ? h("span", { class: "ch-mp-badge", title: t("mp_vision"), html: icon("eye", 13) }) : null,
            m.context ? h("span", { class: "ch-mp-ctx", title: t("mp_context", { count: fmtNum(m.context) }), text: fmtContext(m.context) }) : null
          ])
        ]);
        b.addEventListener("click", function () { closeMenus(); if (opts.onPick) opts.onPick(m.id); });
        b.addEventListener("mousemove", function () { setActive(buttons.indexOf(b), true); });
        buttons.push(b);
        list.appendChild(b);
      });
    });
    if (!rows.length) {
      list.appendChild(h("div", { class: "ch-mp-empty", text: textModels().length ? t("nothing_found") : t("models_unavailable") }));
    }
    var ci = buttons.findIndex(function (b) { return b.classList.contains("current"); });
    setActive(q ? 0 : Math.max(0, ci), !!q);
  }
  function setActive(i, noScroll) {
    if (!buttons.length) return;
    active = clamp(i, 0, buttons.length - 1);
    buttons.forEach(function (b, k) { b.classList.toggle("active", k === active); });
    if (!noScroll) buttons[active].scrollIntoView({ block: "nearest" });
  }
  search.addEventListener("input", draw);
  search.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    else if (e.key === "PageDown") { e.preventDefault(); setActive(active + 8); }
    else if (e.key === "PageUp") { e.preventDefault(); setActive(active - 8); }
    else if (e.key === "Enter") { e.preventDefault(); if (buttons[active]) buttons[active].click(); }
    else if (e.key === "Tab") { closeMenus(); }
  });
  document.body.appendChild(menu);
  draw();
  placeNear(menu, anchor);
  requestAnimationFrame(function () { menu.classList.add("in"); search.focus(); });
  menu.addEventListener("mousedown", function (e) { e.stopPropagation(); });
  return menu;
}

/* ====================================================================== r33: several new variants at once */
function variantCount() { return clamp(parseInt(S.settings.ui.variantCount, 10) || 3, 1, 5); }
function canMakeVariants() {
  if (!S.chat || !S.char) return false;
  var msgs = S.chat.messages, last = msgs[msgs.length - 1];
  return !(last && last.greeting && msgs.length === 1);
}
// Right-click on the «next variant» arrow (or on «Regenerate»): how many new answers to start in parallel.
function variantMenu(anchor, e) {
  if (e) { e.preventDefault(); e.stopPropagation(); }
  closeMenus();
  var count = variantCount();
  var menu = h("div", { class: "ch-menu ch-vmenu", role: "dialog", "aria-label": t("variants_title") });
  menu.appendChild(h("div", { class: "ch-vmenu-head" }, [h("span", { html: icon("layers", 16) }), h("b", { text: t("variants_title") })]));
  var seg = h("div", { class: "ch-vseg", role: "radiogroup", "aria-label": t("variants_count") });
  var go = h("button", { class: "mu-btn primary small ch-vgo", type: "button" });
  function sync() {
    $$("button", seg).forEach(function (b) { var on = Number(b.dataset.n) === count; b.setAttribute("aria-checked", String(on)); b.classList.toggle("on", on); b.tabIndex = on ? 0 : -1; });
    go.innerHTML = icon("sparkle", 15) + "<span>" + esc(t("variants_go", { count: count })) + "</span>";
  }
  function pick(n, focus) {
    count = clamp(n, 1, 5);
    S.settings.ui.variantCount = count; saveSettings();
    sync();
    if (focus) { var b = seg.querySelector('[data-n="' + count + '"]'); if (b) b.focus(); }
  }
  for (var n = 1; n <= 5; n++) {
    (function (k) {
      seg.appendChild(h("button", { class: "ch-vseg-b", type: "button", role: "radio", "data-n": String(k), text: String(k), onclick: function () { pick(k); } }));
    })(n);
  }
  seg.addEventListener("keydown", function (ev) {
    if (ev.key === "ArrowRight" || ev.key === "ArrowUp") { ev.preventDefault(); pick(count + 1, true); }
    else if (ev.key === "ArrowLeft" || ev.key === "ArrowDown") { ev.preventDefault(); pick(count - 1, true); }
    else if (ev.key === "Enter") { ev.preventDefault(); go.click(); }
  });
  go.addEventListener("click", function () { closeMenus(); generateVariants(count); });
  menu.appendChild(h("div", { class: "ch-vmenu-row" }, [h("span", { class: "ch-vmenu-label", text: t("variants_count") }), seg]));
  menu.appendChild(go);
  menu.appendChild(h("div", { class: "ch-vmenu-hint", text: t("variants_hint") }));
  var lc = h("input", { type: "checkbox" });
  lc.checked = !!S.settings.ui.variantLeftClick;
  lc.addEventListener("change", function () { S.settings.ui.variantLeftClick = lc.checked; saveSettings(); renderMessages(); });
  menu.appendChild(h("label", { class: "ch-vmenu-check" }, [lc, h("span", { text: t("variants_left_click") })]));
  var last = lastMessage();
  if (last && last.role === "assistant" && last.swipes && last.swipes.length > 1) {
    menu.appendChild(h("div", { class: "ch-menu-sep" }));
    menu.appendChild(h("button", { class: "ch-menu-item", type: "button", onclick: function () { closeMenus(); openVariantCompare(last); } }, [
      h("span", { class: "ch-menu-ic", html: icon("columns", 16) }), h("span", { class: "ch-menu-label", text: t("variants_compare") }),
      h("span", { class: "ch-menu-hint", text: String(last.swipes.length) })]));
  }
  // Digits 1–5 pick the count from anywhere in the menu; Enter starts.
  menu.addEventListener("keydown", function (ev) {
    if (/^[1-5]$/.test(ev.key)) { ev.preventDefault(); pick(Number(ev.key), true); }
    else if (ev.key === "Tab") { /* stay inside: the menu is small */ }
  });
  sync();
  document.body.appendChild(menu);
  placeNear(menu, anchor, e && e.clientX ? { x: e.clientX, y: e.clientY } : null);
  requestAnimationFrame(function () { menu.classList.add("in"); var b = seg.querySelector('[aria-checked="true"]'); if (b) b.focus(); });
  menu.addEventListener("mousedown", function (ev) { ev.stopPropagation(); });
  return menu;
}
// Left click on the arrow at the newest variant: one new answer — or the remembered count when the person asked so.
function swipeForward(msg) {
  if (S.settings.ui.variantLeftClick && variantCount() > 1) generateVariants(variantCount());
  else swipe(msg, 1);
}
function parseAnswer(raw, reasoningRaw, final) {
  var gen = S.settings.gen, text = raw, reasoning = reasoningRaw;
  if (gen.autoParseReasoning) {
    var split = E.splitReasoning(text, gen.reasoningPrefix, gen.reasoningSuffix);
    if (split.reasoning || split.open) { text = split.text; reasoning = (reasoning ? reasoning + "\n" : "") + split.reasoning; }
  }
  if (final) text = text.replace(/^\s+/, "");
  return { text: text, reasoning: reasoning };
}
/* N answers to the same prompt, streamed in parallel into new swipes of the last reply (a new reply when the chat
   ends with the person's message). One prompt is built once; a fixed preset seed gets +i per variant, otherwise the
   variants would be identical. Stop (button, Esc) cancels all; a single variant can be stopped from its pill. */
function generateVariants(n) {
  n = clamp(parseInt(n, 10) || 1, 1, 5);
  if (S.busy || !S.char || !S.chat) return Promise.resolve();
  if (!canMakeVariants()) { toast(t("variants_greeting"), "warn"); return Promise.resolve(); }
  if (n === 1) {
    var lm = lastMessage();
    return generate(lm && lm.role === "assistant" ? "swipe" : "normal", { text: "" });
  }
  if (!ensureModel()) return Promise.resolve();
  var chat = S.chat, messages = chat.messages, last = lastMessage();
  var target, promptMessages, created = false;
  if (last && last.role === "assistant") {
    if (!last.swipes) { last.swipes = [last.mes]; last.swipe_id = 0; last.swipe_info = [{}]; }
    storeSwipe(last);
    target = last;
    promptMessages = messages.slice(0, -1);
  } else {
    promptMessages = messages.slice();
    target = { id: E.uid("m"), role: "assistant", name: S.char.data.name, mes: "", swipes: [], swipe_id: 0, swipe_info: [], date: Date.now(), reasoning: "" };
    messages.push(target);
    created = true;
  }
  var base = target.swipes.length;
  var model = S.settings.model;
  for (var i = 0; i < n; i++) { target.swipes.push(""); target.swipe_info.push({ pending: true, model: model }); }
  setSwipe(target, base);
  target.pending = true; target.greeting = false;
  var built;
  try {
    built = buildFor("swipe", promptMessages.filter(function (m) { return !m.pending; }));
  } catch (error) {
    target.swipes.splice(base, n); target.swipe_info.splice(base, n);
    delete target.pending;
    if (created) messages.splice(messages.indexOf(target), 1); else setSwipe(target, base - 1);
    renderMessages(); showError(error && error.message ? error.message : String(error));
    return Promise.resolve();
  }
  S.lastPrompt = { at: Date.now(), type: "swipe", built: built, model: model };
  target.promptRef = S.lastPrompt.at;
  updateTokenBadge(built);
  if (built.warnings.indexOf("over_budget") >= 0) toast(t("over_budget"), "warn");
  var wire = built.messages.map(function (m) { var r = { role: m.role, content: m.content }; if (m.name) r.name = m.name; return r; });
  var gen = S.settings.gen;
  var prefill = built.prefill || "";
  var job = { kind: "variants", target: target, base: base, n: n, slots: [], stopped: false };
  job.cancel = function () { job.slots.forEach(function (sl) { if (sl.job && sl.state !== "done" && sl.state !== "error") { sl.stopped = true; sl.job.cancel(); } }); };
  S.job = job; S.variantJob = job;
  setBusy(true);
  renderMessages();
  scrollToBottom(true);
  var timer = 0;
  function paint() {
    timer = 0;
    job.slots.forEach(function (sl, k) {
      var parsed = parseAnswer(sl.raw, sl.reasoning, false);
      target.swipes[base + k] = parsed.text;
      var info = target.swipe_info[base + k] || (target.swipe_info[base + k] = {});
      info.reasoning = parsed.reasoning;
    });
    var shown = target.swipe_id - base;
    if (shown >= 0 && shown < n) {
      target.mes = target.swipes[target.swipe_id];
      target.reasoning = (target.swipe_info[target.swipe_id] || {}).reasoning || "";
      updateMessageNode(target);
    }
    updateVariantStrip();
  }
  function schedule() { if (!timer) timer = setTimeout(paint, 90); }
  var runs = [];
  for (var k = 0; k < n; k++) {
    (function (k) {
      var sl = { index: k, raw: "", reasoning: "", state: "wait", error: "", started: Date.now(), leaked: false, stopped: false };
      job.slots.push(sl);
      var params = Object.assign({}, built.params);
      if (typeof params.seed === "number" && params.seed >= 0) params.seed = params.seed + k;
      sl.job = Memu.complete({ model: model, messages: wire, params: params }, {
        onDelta: function (chunk) {
          if (sl.leaked) return;
          sl.raw += chunk; sl.state = "stream";
          var at = E.findLeakedToken(sl.raw);
          if (at >= 0) { sl.raw = sl.raw.slice(0, at); sl.leaked = true; sl.stopped = true; if (sl.job && sl.job.cancel) sl.job.cancel(); }
          schedule();
        },
        onReasoning: function (chunk) { sl.reasoning += chunk; sl.state = "stream"; schedule(); },
        onUsage: function (usage) { sl.usage = usage; }
      });
      runs.push(sl.job.then(function (result) {
        result = result || {};
        if (!sl.leaked && typeof result.text === "string" && result.text.length >= sl.raw.length) {
          sl.raw = result.text;
          var at = E.findLeakedToken(sl.raw); if (at >= 0) sl.raw = sl.raw.slice(0, at);
        }
        if (result.reasoning && !sl.reasoning) sl.reasoning = result.reasoning;
        if (result.usage) sl.usage = result.usage;
        sl.finish = result.finish_reason || "";
        sl.state = "done"; sl.ms = Date.now() - sl.started;
        schedule();
      }, function (error) {
        sl.ms = Date.now() - sl.started;
        if (sl.stopped || job.stopped) sl.state = "done";
        else { sl.state = "error"; sl.error = error && error.message ? error.message : String(error); }
        schedule();
      }));
    })(k);
  }
  return Promise.all(runs).then(function () {
    if (timer) { clearTimeout(timer); timer = 0; }
    var shownBefore = target.swipe_id - base;
    var keep = [], errors = [];
    job.slots.forEach(function (sl, k) {
      var parsed = parseAnswer(sl.raw, sl.reasoning, true);
      var text = E.runRegexScripts(parsed.text, S.settings.regex, 2, {});
      if (gen.showPrefill && prefill && text) text = prefill + text;
      if (!text.trim()) { if (sl.state === "error") errors.push(sl.error); return; }
      var info = { model: model, date: Date.now(), gen_ms: sl.ms, reasoning: parsed.reasoning };
      if (sl.usage) info.usage = sl.usage;
      if (sl.finish) info.finish = sl.finish;
      if (sl.state === "error") info.partial = true;
      keep.push({ k: k, text: text, info: info });
    });
    target.swipes.splice(base, n); target.swipe_info.splice(base, n);
    keep.forEach(function (row) { target.swipes.push(row.text); target.swipe_info.push(row.info); });
    delete target.pending;
    S.job = null; S.variantJob = null;
    setBusy(false);
    if (!keep.length) {
      if (created && !target.swipes.length) messages.splice(messages.indexOf(target), 1);
      else setSwipe(target, Math.max(0, base - 1));
      renderMessages(); saveChatNow();
      if (errors.length) showError(errors[0]); else if (!job.stopped) toast(t("empty_answer"), "warn");
      return;
    }
    // Stay on the variant the person was looking at (if it survived), else the first new one.
    var at = keep.findIndex(function (row) { return row.k === shownBefore; });
    setSwipe(target, base + Math.max(0, at));
    target.model = model;
    rememberPrompt(target, built);
    renderMessages(); scrollToBottom(); saveChatNow();
    updateTokenBadge(built, keep[0].info.usage);
    if (keep.length < n && !job.stopped) toast(t("variants_partial", { done: keep.length, count: n }) + (errors[0] ? " — " + errors[0] : ""), "warn");
  });
}
function viewVariant(msg, index) {
  if (!msg.swipes || index < 0 || index >= msg.swipes.length) return;
  msg.swipe_id = index;
  msg.mes = msg.swipes[index];
  msg.reasoning = ((msg.swipe_info || [])[index] || {}).reasoning || "";
  if (S.busy) { var node = document.querySelector('.ch-msg[data-id="' + msg.id + '"]'); if (node) { var c = node.querySelector(".ch-swipe-count"); if (c) c.textContent = (index + 1) + " / " + msg.swipes.length; } updateMessageNode(msg); updateVariantStrip(); }
  else { renderMessages(); saveChat(); }
}
// Progress of a running batch, under the reply: one pill per variant (waiting / writing · tokens / done / error).
function variantStrip(msg) {
  var job = S.variantJob;
  if (!job || job.target !== msg) return null;
  var strip = h("div", { class: "ch-vprog", id: "variant-strip", role: "group", "aria-label": t("variants_title") });
  fillVariantStrip(strip, job);
  return strip;
}
function fillVariantStrip(strip, job) {
  strip.innerHTML = "";
  var msg = job.target;
  var done = job.slots.filter(function (sl) { return sl.state === "done" || sl.state === "error"; }).length;
  strip.appendChild(h("span", { class: "ch-vprog-label", html: icon("layers", 14) + "<span>" + esc(t("variants_progress", { done: done, count: job.n })) + "</span>" }));
  job.slots.forEach(function (sl, k) {
    var idx = job.base + k;
    E.setTokenModel(S.settings.model);
    var tokens = sl.raw ? E.estimateTokens(sl.raw) : 0;
    var state = sl.state;
    var label = state === "wait" ? t("variant_wait") : state === "stream" ? (sl.raw ? "≈ " + fmtNum(tokens) : t("variant_thinking")) : state === "error" ? t("variant_error") : (sl.raw.trim() ? fmtNum(tokens) + " " + t("tok") : t("variant_empty"));
    var pill = h("button", { class: "ch-vpill " + state + (msg.swipe_id === idx ? " shown" : ""), type: "button",
      title: state === "error" ? sl.error : t("variant_show", { n: idx + 1 }) }, [
      h("span", { class: "ch-vpill-dot" }),
      h("b", { text: "#" + (idx + 1) }),
      h("span", { class: "ch-vpill-meta", text: label })
    ]);
    pill.addEventListener("click", function () { viewVariant(msg, idx); });
    strip.appendChild(pill);
    if (state === "wait" || state === "stream") {
      var x = h("button", { class: "ch-vpill-stop", type: "button", title: t("variant_stop", { n: idx + 1 }), "aria-label": t("variant_stop", { n: idx + 1 }), html: icon("x", 12) });
      x.addEventListener("click", function (e) { e.stopPropagation(); sl.stopped = true; if (sl.job && sl.job.cancel) sl.job.cancel(); });
      pill.appendChild(x);
    }
  });
  strip.appendChild(h("span", { class: "mu-grow" }));
  strip.appendChild(h("button", { class: "mu-btn small ghost ch-vprog-stop", type: "button", html: icon("stop", 14) + "<span>" + esc(t("stop_all")) + "</span>", onclick: function () { stopGeneration(); } }));
}
function updateVariantStrip() {
  var strip = $("#variant-strip");
  if (strip && S.variantJob) fillVariantStrip(strip, S.variantJob);
}
// All variants of a reply side by side: pick one or drop the weak ones.
function openVariantCompare(msg) {
  if (!msg || !msg.swipes || msg.swipes.length < 2) return;
  var modal = openModal({ title: t("variants_compare_title", { count: msg.swipes.length }), icon: "columns", full: true, content: function (body) { draw(body); } });
  function draw(body) {
    body.innerHTML = "";
    body.classList.add("ch-vcmp-body");
    var grid = h("div", { class: "ch-vcmp" });
    msg.swipes.forEach(function (text, i) {
      var info = (msg.swipe_info || [])[i] || {};
      var meta = [info.greeting ? t("greeting") : (info.model ? (modelRow(info.model) ? modelTitle(modelRow(info.model)) : shortModel(info.model)) : "")];
      if (info.usage && info.usage.completion_tokens) meta.push(fmtNum(info.usage.completion_tokens) + " " + t("tok"));
      else if (text) meta.push("≈ " + fmtNum(E.estimateTokens(text)) + " " + t("tok"));
      if (info.gen_ms) meta.push((info.gen_ms / 1000).toFixed(1) + " s");
      var card = h("div", { class: "ch-vcard" + (i === msg.swipe_id ? " current" : "") }, [
        h("div", { class: "ch-vcard-head" }, [h("b", { text: "#" + (i + 1) }), h("span", { class: "mu-muted ch-small", text: meta.filter(Boolean).join(" · ") }),
          h("span", { class: "mu-grow" }), i === msg.swipe_id ? h("span", { class: "ch-tag", text: t("variant_current") }) : null]),
        h("div", { class: "ch-vcard-text ch-text", html: formatText(msg.greeting ? displaySubst(text) : text, { placement: 2 }) }),
        h("div", { class: "ch-vcard-foot" }, [
          h("button", { class: "mu-btn small " + (i === msg.swipe_id ? "" : "primary"), type: "button", html: icon("check", 14) + "<span>" + esc(t("variant_pick")) + "</span>",
            onclick: function () { setSwipe(msg, i); renderMessages(); saveChat(); modal.close(); } }),
          h("button", { class: "mu-btn small ghost", type: "button", html: icon("copy", 14) + "<span>" + esc(t("copy")) + "</span>", onclick: function () { Memu.copy(text); } }),
          h("span", { class: "mu-grow" }),
          msg.swipes.length > 1 && !S.busy ? h("button", { class: "ch-ib danger", type: "button", title: t("delete_swipe"), "aria-label": t("delete_swipe"), html: icon("trash", 15),
            onclick: function () {
              msg.swipes.splice(i, 1); (msg.swipe_info || []).splice(i, 1);
              setSwipe(msg, Math.min(msg.swipe_id > i ? msg.swipe_id - 1 : msg.swipe_id, msg.swipes.length - 1));
              renderMessages(); saveChat();
              if (msg.swipes.length < 2) modal.close(); else draw(body);
            } }) : null
        ])
      ]);
      grid.appendChild(card);
    });
    body.appendChild(grid);
  }
}

/* ---------------------------------------------------------------- tokens badge */
var refreshTokens = debounce(function () {
  refreshComposerBar();  // r33: a model / reasoning change made in the settings panel shows in the composer too
  if (!S.char || !S.chat || S.busy) return;
  try { updateTokenBadge(buildFor("normal", S.chat.messages, { dryRun: true })); } catch (e) { /* ignore */ }
}, 900);
function updateTokenBadge(built, usage) {
  var badge = $("#token-badge");
  if (built && hostBar()) {
    S.lastBuilt = built;
    S.lastUsage = usage || null;
    syncToolbar();
    return;
  }
  if (!badge || !built) return;
  var used = usage && usage.prompt_tokens ? usage.prompt_tokens : built.tokens;
  var max = built.maxContext;
  var pct = max ? Math.min(100, used / max * 100) : 0;
  badge.innerHTML = '<span class="ch-tk-bar"><i style="width:' + pct.toFixed(1) + '%"></i></span><span>' + (usage && usage.prompt_tokens ? "" : "≈") + fmtNum(used) + " / " + fmtNum(max) + "</span>";
  badge.title = t("tokens_title", { used: fmtNum(used), max: fmtNum(max) });
  S.lastBuilt = built;
}


/* ====================================================================== layout */
function applyAppearance() {
  var ui = S.settings.ui;
  var root = document.documentElement;
  root.dataset.chatStyle = ui.chatStyle;
  root.dataset.avatarShape = ui.avatarShape;
  root.dataset.font = ui.font;
  root.dataset.anim = ui.animations ? "on" : "off";
  root.dataset.density = ui.density || "normal";
  root.style.setProperty("--ch-ava", clamp(Number(ui.avatarSize) || 42, 24, 120) + "px");
  root.style.setProperty("--ch-width", clamp(Number(ui.msgWidth) || 860, 480, 2400) + "px");
  root.style.setProperty("--ch-blur", clamp(Number(ui.blur) || 0, 0, 40) + "px");
  root.style.setProperty("--ch-font-scale", clamp(Number(ui.fontSize) || 100, 70, 160) / 100);
  root.style.setProperty("--ch-dim", clamp(Number(ui.bgDim) || 0, 0, 95) / 100);
  ["accent", "quoteColor", "italicColor", "userBubble"].forEach(function (k) {
    var v = /^#[0-9a-fA-F]{3,8}$/.test(String(ui[k] || "")) ? ui[k] : "";
    var name = { accent: "--ch-accent", quoteColor: "--ch-quote", italicColor: "--ch-italic", userBubble: "--ch-user" }[k];
    if (v) root.style.setProperty(name, v); else root.style.removeProperty(name);
  });
  if (ui.accent && /^#[0-9a-fA-F]{3,8}$/.test(ui.accent)) {
    root.style.setProperty("--accent", ui.accent);
    root.style.setProperty("--accent-soft", "color-mix(in srgb," + ui.accent + " 15%,transparent)");
  }
  var bg = (S.chat && S.chat.meta && S.chat.meta.background) || (S.char && S.char.background) || ui.bg || "";
  var layer = $("#bg-layer");
  if (layer) {
    layer.style.backgroundImage = bg ? 'url("' + bg + '")' : "";
    layer.classList.toggle("on", !!bg);
  }
  $("#app").classList.toggle("no-side", !ui.sidebar);
}
function render() {
  applyAppearance();
  E.setTokenModel(S.settings.model);
  renderSidebar();
  if (S.view === "chat" && S.char && S.chat) renderChatView(); else renderHome();
  if (S.drawerOpen) renderDrawer();
  syncToolbar();
}

/* ---------------------------------------------------------------- one header (Round 30) */
// In the MemuAI window the module has no top bar of its own: its actions go to the window's single title row
// (Memu.toolbar). Elsewhere (test harness, Telegram, an older MemuAI) the old in-module header is drawn.
function hostBar() { return !!(window.Memu && Memu.toolbar && Memu.toolbar.supported && Memu.toolbar.supported()); }
function svgOf(name) { return '<svg viewBox="0 0 24 24">' + (ICONS[name] || "") + "</svg>"; }
function toggleSidebar() { S.settings.ui.sidebar = !S.settings.ui.sidebar; saveSettings(); applyAppearance(); syncToolbar(); }
function chatMenuEntries() {
  return [
    { icon: "mask", label: t("impersonate"), onClick: function () { generate("impersonate"); } },
    { icon: "forward", label: t("continue"), onClick: function () { generate("continue"); } },
    { icon: "refresh", label: t("regenerate"), onClick: function () { generate("regenerate"); } },
    { sep: true },
    { icon: "user", label: t("edit_character_full"), hostOnly: true, onClick: function () { openCharacterEditor(S.char.id); } },
    { icon: "edit", label: t("rename_chat"), onClick: function () { renameChat(S.chat.id); } },
    { icon: "user", label: t("chat_persona"), onClick: function () { openPersonas(true); } },
    { icon: "globe", label: t("chat_lorebook"), onClick: function () { chooseChatWorld(); } },
    { icon: "image", label: t("chat_background"), onClick: function () { chooseBackground("chat"); } },
    { icon: "bookmark", label: t("bookmarks"), onClick: function () { openSearch(true); } },
    { icon: "list", label: t("variables"), onClick: function () { openVariables(); } },
    { sep: true },
    { icon: "download", label: t("export_chat_jsonl"), onClick: function () { exportChat(S.chat, "jsonl"); } },
    { icon: "file", label: t("export_chat_txt"), onClick: function () { exportChat(S.chat, "txt"); } },
    { icon: "upload", label: t("import_chat"), onClick: function () { importChatFile(); } },
    { sep: true },
    { icon: "trash", label: t("clear_chat"), danger: true, onClick: function () { clearChat(); } }
  ];
}
function tokenMeter() {
  var built = S.lastBuilt;
  var used = S.lastUsage && S.lastUsage.prompt_tokens ? S.lastUsage.prompt_tokens : built.tokens;
  var max = built.maxContext || 0;
  var pct = max ? Math.min(1, used / max) : 0;
  return { id: "tokens", kind: "meter", value: pct, tone: pct > 0.95 ? "danger" : pct > 0.8 ? "warn" : "",
    label: (S.lastUsage && S.lastUsage.prompt_tokens ? "" : "≈") + fmtNum(used) + " / " + fmtNum(max),
    title: t("tokens_title", { used: fmtNum(used), max: fmtNum(max) }), onClick: function () { openInspector(); } };
}
function syncToolbar() {
  if (!hostBar()) return;
  var ui = S.settings.ui;
  var sidebar = { id: "sidebar", kind: "toggle", icon: svgOf("sidebar"), label: t("toggle_sidebar"), pressed: !!ui.sidebar, onClick: toggleSidebar };
  var drawer = { id: "drawer", kind: "toggle", icon: svgOf("panel"), label: t("settings_panel"), pressed: !!S.drawerOpen, pin: true, onClick: function () { toggleDrawer(); } };
  if (S.view === "chat" && S.char && S.chat) {
    var note = S.chat.meta && S.chat.meta.note && S.chat.meta.note.text;
    Memu.toolbar.set({
      // A default chat title already starts with the character's name («Alice — Oct 10, 19:43»).
      title: S.chat.title && S.chat.title.indexOf(S.char.data.name) === 0 ? S.chat.title : S.char.data.name + (S.chat.title ? " — " + S.chat.title : ""),
      items: [
        S.lastBuilt ? tokenMeter() : null,
        { id: "chats", icon: svgOf("chat"), label: t("chats"), onClick: function () { openChatManager(); } },
        { id: "new-chat", icon: svgOf("plus"), label: t("new_chat"), onClick: function () { startNewChat(); } },
        { id: "note", icon: svgOf("note"), label: t("authors_note"), dot: !!note, onClick: function () { openAuthorsNote(); } },
        { id: "search", icon: svgOf("search"), label: t("search_chat"), onClick: function () { openSearch(); } },
        { id: "more", icon: svgOf("more"), label: t("more"), pin: true, menu: chatMenuEntries().map(function (e, i) {
          return e.sep ? { sep: true } : { id: "m" + i, label: e.label, icon: svgOf(e.icon), danger: !!e.danger, onClick: e.onClick };
        }) },
        { kind: "separator" },
        sidebar, drawer
      ]
    });
  } else {
    Memu.toolbar.set({ title: "", items: [sidebar, drawer] });
  }
}

/* ---------------------------------------------------------------- sidebar */
function renderSidebar() {
  var side = $("#side");
  if (!side) return;
  side.innerHTML = "";
  var search = h("input", { class: "mu-input ch-search", type: "search", placeholder: hostBar() ? t("search_short") : t("search_chars"), "aria-label": t("search_chars") });
  search.value = S.search;
  search.addEventListener("input", function () { S.search = search.value; renderCharList(); if (S.view === "home") renderGallery(); });
  var searchWrap = h("div", { class: "ch-search-wrap", html: icon("search", 15) }, [search]);
  // The window title already says «Characters»: under the host header the search moves up into the head row,
  // and «Home» is the first chip.
  var head = h("div", { class: "ch-side-head" + (hostBar() ? " compact" : "") }, [
    hostBar() ? h("button", { class: "ch-ib", type: "button", title: t("home"), "aria-label": t("home"), html: icon("home"), onclick: goHome })
      : h("button", { class: "ch-brand", type: "button", onclick: goHome, title: t("home") }, [h("span", { class: "ch-brand-ic", html: icon("users", 18) }), h("span", { text: t("title") })]),
    hostBar() ? searchWrap : h("span", { class: "mu-grow" }),
    iconBtn("upload", "import", function () { importFlow(); }),
    iconBtn("plus", "new_character", function () { openCharacterEditor(null); })
  ]);
  side.appendChild(head);
  if (!hostBar()) side.appendChild(searchWrap);
  var tags = {};
  S.index.forEach(function (c) { (c.tags || []).forEach(function (tag) { tags[tag] = (tags[tag] || 0) + 1; }); });
  var tagNames = Object.keys(tags).sort(function (a, b) { return tags[b] - tags[a]; }).slice(0, 24);
  var tagBar = h("div", { class: "ch-tagbar" });
  tagBar.appendChild(h("button", { class: "ch-chip" + (!S.settings.tagFilter ? " on" : ""), type: "button", text: t("all"), onclick: function () { S.settings.tagFilter = ""; saveSettings(); render(); } }));
  tagBar.appendChild(h("button", { class: "ch-chip" + (S.settings.tagFilter === "\u2605" ? " on" : ""), type: "button", html: icon("star", 12) + esc(t("favorites")), onclick: function () { S.settings.tagFilter = "\u2605"; saveSettings(); render(); } }));
  tagNames.forEach(function (tag) {
    tagBar.appendChild(h("button", { class: "ch-chip" + (S.settings.tagFilter === tag ? " on" : ""), type: "button", text: tag, onclick: function () { S.settings.tagFilter = S.settings.tagFilter === tag ? "" : tag; saveSettings(); render(); } }));
  });
  side.appendChild(tagBar);
  fitTagBar(tagBar);
  side.appendChild(h("div", { class: "ch-list", id: "charlist" }));
  renderCharList();
  var persona = activePersona();
  var foot = h("div", { class: "ch-side-foot" }, [
    h("button", { class: "ch-persona-chip", type: "button", title: t("personas"), onclick: function () { openPersonas(); } }, [
      h("img", { src: persona.avatar || defaultAvatar(persona.name || "U", "user" + persona.id), alt: "" }),
      h("span", { class: "ch-persona-name", text: persona.name || t("default_user_name") })
    ]),
    h("span", { class: "mu-grow" }),
    iconBtn("book", "lorebooks", function () { openDrawer("worlds"); }),
    iconBtn("sliders", "presets", function () { openDrawer("preset"); }),
    iconBtn("settings", "settings", function () { openDrawer("appearance"); })
  ]);
  side.appendChild(foot);
}
// Round 30: the tags show two rows and a «+N» chip instead of a scrolling box with its own scrollbar; «+N» opens
// all of them (and «Less» folds them back). Re-fitted when the sidebar changes width.
function fitTagBar(bar) {
  if (!bar) return;
  var chips = Array.prototype.slice.call(bar.querySelectorAll(".ch-chip:not(.ch-tag-more)"));
  var old = bar.querySelector(".ch-tag-more");
  if (old) old.remove();
  chips.forEach(function (c) { c.hidden = false; });
  if (!bar.isConnected || !chips.length || !bar.clientWidth) return;
  var tops = [];
  chips.forEach(function (c) { if (tops.indexOf(c.offsetTop) < 0) tops.push(c.offsetTop); });
  if (tops.length <= 2 && !S.tagsOpen) return;
  var more = h("button", { class: "ch-chip ch-tag-more", type: "button" });
  more.addEventListener("click", function () { S.tagsOpen = !S.tagsOpen; fitTagBar(bar); });
  if (S.tagsOpen) {
    if (tops.length <= 2) { S.tagsOpen = false; return; }
    more.textContent = t("tags_less");
    bar.appendChild(more);
    return;
  }
  bar.appendChild(more);
  var limit = tops.sort(function (a, b) { return a - b; })[1];
  var fits = function () {
    return more.offsetTop <= limit && chips.every(function (c) { return c.hidden || c.offsetTop <= limit; });
  };
  var hidden = 0;
  for (var i = chips.length - 1; i >= 0; i--) {
    more.textContent = "+" + Math.max(1, hidden);
    if (hidden && fits()) break;
    if (chips[i].classList.contains("on")) continue; // the active filter stays visible
    chips[i].hidden = true;
    hidden++;
  }
  more.textContent = "+" + hidden;
  more.title = t("tags_more", { count: hidden });
  if (!hidden) more.remove();
}
if (typeof ResizeObserver !== "undefined") {
  var tagObserverWidth = 0;
  new ResizeObserver(function () {
    var side = document.getElementById("side");
    if (!side || side.clientWidth === tagObserverWidth) return;
    tagObserverWidth = side.clientWidth;
    fitTagBar(side.querySelector(".ch-tagbar"));
  }).observe(document.documentElement);
}
function filteredIndex() {
  var q = S.search.trim().toLowerCase();
  var tag = S.settings.tagFilter;
  var rows = S.index.filter(function (c) {
    if (tag === "\u2605" && !c.fav) return false;
    if (tag && tag !== "\u2605" && (c.tags || []).indexOf(tag) < 0) return false;
    if (!q) return true;
    return (c.name + " " + (c.tags || []).join(" ") + " " + (c.creator || "") + " " + (c.summary || "")).toLowerCase().indexOf(q) >= 0;
  });
  var sort = S.settings.ui.gallerySort;
  rows.sort(function (a, b) {
    if (a.fav !== b.fav) return a.fav ? -1 : 1;
    if (sort === "name") return String(a.name).localeCompare(String(b.name));
    if (sort === "created") return (b.created || 0) - (a.created || 0);
    return (b.updated || 0) - (a.updated || 0);
  });
  return rows;
}
function renderCharList() {
  var list = $("#charlist");
  if (!list) return;
  list.innerHTML = "";
  var rows = filteredIndex();
  if (!rows.length) list.appendChild(h("div", { class: "ch-list-empty mu-muted", text: S.index.length ? t("nothing_found") : t("no_characters") }));
  rows.forEach(function (row) {
    var item = h("button", { class: "ch-list-item" + (S.char && S.char.id === row.id ? " active" : ""), type: "button" }, [
      h("img", { src: thumbOf(row), alt: "", loading: "lazy" }),
      h("span", { class: "ch-li-text" }, [
        h("span", { class: "ch-li-name", text: row.name || t("unnamed") }),
        h("span", { class: "ch-li-sub", text: row.chats ? t("chats_count", { count: row.chats }) : (row.tags || []).slice(0, 3).join(", ") })
      ]),
      row.fav ? h("span", { class: "ch-li-fav", html: icon("star", 13) }) : null
    ]);
    item.addEventListener("click", function () { openCharacter(row.id); });
    item.addEventListener("contextmenu", function (e) { e.preventDefault(); charMenu(item, row); });
    list.appendChild(item);
  });
}
function charMenu(anchor, row) {
  popMenu(anchor, [
    { icon: "chat", label: t("open_chat"), onClick: function () { openCharacter(row.id); } },
    { icon: "edit", label: t("edit"), onClick: function () { openCharacterEditor(row.id); } },
    { icon: "star", label: row.fav ? t("unfavorite") : t("favorite"), onClick: function () { toggleFav(row.id); } },
    { icon: "copy", label: t("duplicate"), onClick: function () { duplicateCharacter(row.id); } },
    { sep: true },
    { icon: "image", label: t("export_png"), onClick: function () { exportCharacter(row.id, "png"); } },
    { icon: "file", label: t("export_json"), onClick: function () { exportCharacter(row.id, "json"); } },
    { icon: "file", label: t("export_json_v2"), onClick: function () { exportCharacter(row.id, "json2"); } },
    { sep: true },
    { icon: "trash", label: t("delete"), danger: true, onClick: function () { deleteCharacter(row.id); } }
  ]);
}

/* ---------------------------------------------------------------- home / gallery */
function renderHome() {
  var main = $("#main");
  main.innerHTML = "";
  main.className = "ch-main home";
  var hero = h("div", { class: "ch-hero" }, [
    h("div", { class: "ch-hero-text" }, [
      h("div", { class: "ch-hero-title", text: t("home_title") }),
      h("div", { class: "ch-hero-sub", text: t("home_sub") })
    ]),
    h("div", { class: "ch-hero-actions" }, [
      h("button", { class: "mu-btn primary", type: "button", html: icon("plus", 16) + esc(t("new_character")), onclick: function () { openCharacterEditor(null); } }),
      h("button", { class: "mu-btn ghost ch-hero-ai", type: "button", title: t("ai_char_btn_title"), "aria-label": t("ai_char_btn"), html: icon("wand", 16) + "<span>" + esc(t("ai_char_btn")) + "</span>", onclick: function () { openAiCreator("character"); } }),
      h("button", { class: "mu-btn", type: "button", html: icon("upload", 16) + esc(t("import")), onclick: function () { importFlow(); } }),
      hostBar() ? null : iconBtn("sidebar", "toggle_sidebar", toggleSidebar)
    ])
  ]);
  main.appendChild(hero);
  var tools = h("div", { class: "ch-gallery-tools" }, [
    h("span", { class: "mu-muted", text: t("characters_count", { count: S.index.length }) }),
    h("span", { class: "mu-grow" }),
    selectEl([["recent", t("sort_recent")], ["name", t("sort_name")], ["created", t("sort_created")]], S.settings.ui.gallerySort, function (v) { S.settings.ui.gallerySort = v; saveSettings(); renderGallery(); renderCharList(); })
  ]);
  main.appendChild(tools);
  main.appendChild(h("div", { class: "ch-gallery", id: "gallery" }));
  renderGallery();
  main.addEventListener("dragover", function (e) { e.preventDefault(); main.classList.add("drop"); });
  main.addEventListener("dragleave", function () { main.classList.remove("drop"); });
  main.addEventListener("drop", function (e) { e.preventDefault(); main.classList.remove("drop"); importFiles(Array.prototype.slice.call(e.dataTransfer.files || [])); });
}
function renderGallery() {
  var g = $("#gallery");
  if (!g) return;
  g.innerHTML = "";
  var rows = filteredIndex();
  if (!rows.length) {
    g.appendChild(h("div", { class: "ch-gallery-empty" }, [
      h("div", { class: "ch-empty-ic", html: icon("users", 40) }),
      h("div", { class: "ch-empty-title", text: S.index.length ? t("nothing_found") : t("no_characters") }),
      h("div", { class: "mu-muted", text: t("no_characters_hint") })
    ]));
    return;
  }
  rows.forEach(function (row) {
    var card = h("div", { class: "ch-card", tabindex: "0", role: "button" }, [
      h("div", { class: "ch-card-img" }, [h("img", { src: thumbOf(row), alt: "", loading: "lazy" })]),
      h("div", { class: "ch-card-body" }, [
        h("div", { class: "ch-card-name", text: row.name || t("unnamed") }),
        h("div", { class: "ch-card-sum", text: String(row.summary || "").replace(/\{\{(char|user)\}\}/gi, function (m, w) { return w.toLowerCase() === "char" ? row.name : userName(); }) }),
        h("div", { class: "ch-card-tags" }, (row.tags || []).slice(0, 4).map(function (tag) { return h("span", { class: "ch-chip small", text: tag }); }))
      ]),
      h("button", { class: "ch-card-fav" + (row.fav ? " on" : ""), type: "button", title: t("favorite"), html: icon("star", 15), onclick: function (e) { e.stopPropagation(); toggleFav(row.id); } }),
      h("button", { class: "ch-card-more", type: "button", title: t("more"), html: icon("dots", 16), onclick: function (e) { e.stopPropagation(); charMenu(e.currentTarget, row); } })
    ]);
    card.addEventListener("click", function () { openCharacter(row.id); });
    card.addEventListener("keydown", function (e) { if (e.key === "Enter") openCharacter(row.id); });
    g.appendChild(card);
  });
}
function selectEl(options, value, onChange, cls) {
  var sel = h("select", { class: "mu-select " + (cls || "") });
  options.forEach(function (o) { var opt = h("option", { value: o[0], text: o[1] }); if (String(o[0]) === String(value)) opt.selected = true; sel.appendChild(opt); });
  sel.addEventListener("change", function () { onChange(sel.value); });
  return sel;
}

/* ---------------------------------------------------------------- chat view */
function renderChatView() {
  var main = $("#main");
  main.innerHTML = "";
  main.className = "ch-main chat";
  var char = S.char;
  if (hostBar()) { S.lastBuilt = null; S.lastUsage = null; }
  var header = hostBar() ? null : h("header", { class: "ch-chat-head" }, [
    iconBtn("sidebar", "toggle_sidebar", toggleSidebar),
    h("button", { class: "ch-head-char", type: "button", title: t("edit_character"), onclick: function () { openCharacterEditor(char.id); } }, [
      h("img", { src: charAvatar(char), alt: "" }),
      h("span", { class: "ch-head-text" }, [
        h("span", { class: "ch-head-name", text: char.data.name }),
        h("span", { class: "ch-head-chat", id: "chat-title", text: S.chat.title || "" })
      ])
    ]),
    h("span", { class: "mu-grow" }),
    h("button", { class: "ch-token-badge", id: "token-badge", type: "button", title: t("prompt_inspector"), onclick: function () { openInspector(); } }),
    iconBtn("chat", "chats", function () { openChatManager(); }),
    iconBtn("plus", "new_chat", function () { startNewChat(); }),
    iconBtn("note", "authors_note", function () { openAuthorsNote(); }, { class: "ch-ib" + (S.chat.meta.note && S.chat.meta.note.text ? " dot" : "") }),
    iconBtn("search", "search_chat", function () { openSearch(); }),
    iconBtn("more", "more", function (e) { chatMenu(e.currentTarget); }),
    iconBtn("panel", "settings_panel", function () { toggleDrawer(); })
  ]);
  if (header) main.appendChild(header);
  var log = h("div", { class: "ch-messages", id: "messages", role: "log", "aria-live": "polite" });
  main.appendChild(log);
  var jump = h("button", { class: "ch-jump", type: "button", title: t("scroll_down"), html: icon("down", 18), onclick: function () { scrollToBottom(true); } });
  main.appendChild(jump);
  log.addEventListener("scroll", function () { jump.classList.toggle("show", log.scrollHeight - log.scrollTop - log.clientHeight > 400); });
  main.appendChild(composer());
  renderMessages();
  scrollToBottom(true);
  refreshTokens();
  main.addEventListener("dragover", function (e) { if (e.dataTransfer && Array.prototype.some.call(e.dataTransfer.types || [], function (x) { return x === "Files"; })) { e.preventDefault(); main.classList.add("drop"); } });
  main.addEventListener("dragleave", function (e) { if (e.target === main) main.classList.remove("drop"); });
  main.addEventListener("drop", function (e) { e.preventDefault(); main.classList.remove("drop"); attachFiles(Array.prototype.slice.call(e.dataTransfer.files || [])); });
}
function updateChatHeader() { var title = $("#chat-title"); if (title && S.chat) title.textContent = S.chat.title || ""; syncToolbar(); }
function chatMenu(anchor) {
  // The avatar button in the old header opens the character; the shared list adds that entry for the host menu.
  popMenu(anchor, chatMenuEntries().filter(function (e) { return !e.hostOnly; }));
}
function composer() {
  var wrap = h("div", { class: "ch-composer-wrap" });
  var qr = h("div", { class: "ch-qr", id: "qr-bar" });
  (S.settings.quickReplies || []).filter(function (q) { return q && q.label && !q.hidden; }).forEach(function (q) {
    qr.appendChild(h("button", { class: "ch-chip", type: "button", text: q.label, title: q.text, onclick: function () { runQuickReply(q); } }));
  });
  if (qr.childNodes.length) wrap.appendChild(qr);
  wrap.appendChild(h("div", { class: "ch-attach-row", id: "attach-row" }));
  // r33: the composer is two rows — the text on top, the tools under it: «+», the model and the reasoning level on
  // the left (switch them without opening the settings panel), the draft's token estimate and «Send» on the right.
  var box = h("div", { class: "ch-composer" });
  var tools = iconBtn("plus", "actions", function () {
    popMenu(tools, [
      { icon: "attach", label: t("attach_image"), onClick: function () { pickFile("image/*", true).then(attachFiles); } },
      { icon: "mask", label: t("impersonate"), onClick: function () { generate("impersonate"); } },
      { icon: "forward", label: t("continue"), onClick: function () { generate("continue"); } },
      { icon: "refresh", label: t("regenerate"), hint: "Alt+R", onClick: function () { generate("regenerate"); } },
      { icon: "layers", label: t("variants_n", { count: variantCount() }), hint: "Alt+Shift+R", onClick: function () { generateVariants(variantCount()); } },
      { icon: "note", label: t("authors_note"), onClick: function () { openAuthorsNote(); } },
      { icon: "zap", label: t("quick_replies"), onClick: function () { openDrawer("extras"); } },
      { icon: "info", label: t("slash_help_title"), onClick: function () { runSlash("/help"); } }
    ]);
  });
  var area = h("textarea", { class: "ch-input", id: "composer-text", rows: "1", placeholder: t("composer_placeholder", { name: S.char.data.name }), title: t("composer_hint") });
  if (S.chat.meta && S.chat.meta.draft) area.value = S.chat.meta.draft;
  area.addEventListener("input", function () { autosize(area); S.chat.meta.draft = area.value; saveChat(); updateDraftTokens(); });
  area.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && S.settings.ui.enterSends) { e.preventDefault(); sendMessage(); }
    else if (e.key === "Enter" && e.ctrlKey && !S.settings.ui.enterSends) { e.preventDefault(); sendMessage(); }
    else if (e.key === "ArrowUp" && !area.value) {
      var last = null;
      for (var i = S.chat.messages.length - 1; i >= 0; i--) if (S.chat.messages[i].role === "user") { last = S.chat.messages[i]; break; }
      if (last) { e.preventDefault(); startEdit(last); }
    } else if (e.key === "Escape" && S.busy) { e.preventDefault(); stopGeneration(); }
  });
  area.addEventListener("paste", function (e) {
    var files = Array.prototype.slice.call((e.clipboardData && e.clipboardData.files) || []).filter(function (f) { return /^image\//.test(f.type); });
    if (files.length) { e.preventDefault(); attachFiles(files); }
  });
  var send = h("button", { class: "ch-send", id: "send", type: "button", title: t("send"), html: icon("send", 18), onclick: sendMessage });
  var bar = h("div", { class: "ch-cbar", id: "composer-bar" }, [tools, h("span", { class: "ch-cbar-chips", id: "composer-chips" }),
    h("span", { class: "mu-grow" }), h("span", { class: "ch-draft-tk", id: "draft-tokens", "aria-live": "off" }), send]);
  box.appendChild(area); box.appendChild(bar);
  wrap.appendChild(box);
  setTimeout(function () { autosize(area); renderAttachments(); setBusy(S.busy); refreshComposerBar(); updateDraftTokens(); }, 0);
  return wrap;
}
function attachFiles(files) {
  var images = (files || []).filter(function (f) { return /^image\//.test(f.type); }).slice(0, 6);
  if (!images.length) return;
  Promise.all(images.map(function (f) { return readAs(f, "dataurl").then(function (url) { return resizeImage(url, 1600, 1600, "image/webp", 0.88); }); }))
    .then(function (urls) { S.pendingImages = (S.pendingImages || []).concat(urls).slice(0, 6); renderAttachments(); });
}
function renderAttachments() {
  var row = $("#attach-row");
  if (!row) return;
  row.innerHTML = "";
  (S.pendingImages || []).forEach(function (src, i) {
    row.appendChild(h("div", { class: "ch-attach" }, [h("img", { src: src, alt: "" }),
      h("button", { type: "button", html: icon("x", 12), title: t("remove"), onclick: function () { S.pendingImages.splice(i, 1); renderAttachments(); } })]));
  });
}
function runQuickReply(q) {
  var text = displaySubst(q.text || "");
  var box = $("#composer-text");
  if (q.mode === "insert") { box.value = (box.value ? box.value + " " : "") + text; autosize(box); box.focus(); return; }
  if (q.mode === "slash" || /^\//.test(text)) { if (runSlash(text)) return; }
  generate("normal", { text: text, fromComposer: false });
}
function clearChat() {
  confirmBox(t("clear_chat_q"), { danger: true, ok: t("clear") }).then(function (ok) {
    if (!ok) return;
    S.chat.messages = [];
    var g = greetingMessage(S.char);
    if (g) S.chat.messages.push(g);
    S.chat.meta.variables = {}; S.chat.meta.timedWorldInfo = {};
    renderMessages(); saveChatNow();
  });
}

/* ---------------------------------------------------------------- drawer */
function openDrawer(tab) { S.drawerTab = tab || S.drawerTab; S.drawerOpen = true; renderDrawer(); }
function toggleDrawer() { S.drawerOpen = !S.drawerOpen; renderDrawer(); }
var DRAWER_TABS = [["preset", "sliders", "tab_preset"], ["connection", "zap", "tab_connection"], ["worlds", "book", "tab_worlds"],
  ["personas", "user", "tab_personas"], ["appearance", "palette", "tab_appearance"], ["extras", "regex", "tab_extras"]];
function renderDrawer() {
  var drawer = $("#drawer");
  var app = $("#app");
  app.classList.toggle("drawer-open", S.drawerOpen);
  drawer.hidden = !S.drawerOpen;
  syncToolbar();
  if (!S.drawerOpen) return;
  drawer.innerHTML = "";
  var tabs = h("div", { class: "ch-drawer-tabs", role: "tablist" });
  DRAWER_TABS.forEach(function (tab) {
    var b = h("button", { class: "ch-dtab", type: "button", role: "tab", "aria-selected": String(S.drawerTab === tab[0]), title: t(tab[2]), html: icon(tab[1], 17) + "<span>" + esc(t(tab[2])) + "</span>" });
    b.addEventListener("click", function () { S.drawerTab = tab[0]; renderDrawer(); });
    tabs.appendChild(b);
  });
  tabs.appendChild(h("span", { class: "mu-grow" }));
  tabs.appendChild(iconBtn("x", "close", function () { S.drawerOpen = false; renderDrawer(); }));
  drawer.appendChild(tabs);
  var body = h("div", { class: "ch-drawer-body" });
  drawer.appendChild(body);
  var fn = { preset: drawPresetPanel, connection: drawConnectionPanel, worlds: drawWorldsPanel, personas: drawPersonasPanel, appearance: drawAppearancePanel, extras: drawExtrasPanel }[S.drawerTab];
  if (fn) fn(body);
}
/* small form helpers */
function section(title, iconName, children, opts) {
  opts = opts || {};
  var det = h("details", { class: "ch-sec" });
  if (opts.open !== false) det.open = true;
  det.appendChild(h("summary", { class: "ch-sec-head", html: (iconName ? icon(iconName, 15) : "") + "<span>" + esc(title) + "</span>" }));
  var body = h("div", { class: "ch-sec-body" }, children);
  det.appendChild(body);
  return det;
}
function field(labelKey, control, hintKey) {
  return h("label", { class: "ch-field" }, [h("span", { class: "ch-label", text: t(labelKey) }), control, hintKey ? h("span", { class: "ch-hint", text: t(hintKey) }) : null]);
}
function slider(labelKey, value, min, max, step, onChange, hintKey) {
  var range = h("input", { type: "range", min: String(min), max: String(max), step: String(step), class: "ch-range" });
  var num = h("input", { type: "number", min: String(min), max: String(max), step: String(step), class: "mu-input ch-num" });
  range.value = value; num.value = value;
  function set(v) { var n = Number(v); if (!Number.isFinite(n)) return; range.value = n; num.value = n; onChange(n); }
  range.addEventListener("input", function () { set(range.value); });
  num.addEventListener("change", function () { set(num.value); });
  return h("div", { class: "ch-slider" }, [h("div", { class: "ch-slider-top" }, [h("span", { class: "ch-label", text: t(labelKey) }), num]), range, hintKey ? h("span", { class: "ch-hint", text: t(hintKey) }) : null]);
}
function toggle(labelKey, value, onChange, hintKey) {
  var input = h("input", { type: "checkbox" });
  input.checked = !!value;
  input.addEventListener("change", function () { onChange(input.checked); });
  return h("label", { class: "ch-toggle" }, [input, h("span", { class: "ch-switch" }), h("span", { class: "ch-toggle-text" }, [h("span", { text: t(labelKey) }), hintKey ? h("span", { class: "ch-hint", text: t(hintKey) }) : null])]);
}
function textInput(value, onChange, opts) {
  opts = opts || {};
  var el = opts.multiline ? h("textarea", { class: "mu-textarea" + (opts.mono ? " mono" : ""), rows: String(opts.rows || 3), placeholder: opts.placeholder || "" })
    : h("input", { class: "mu-input", type: opts.type || "text", placeholder: opts.placeholder || "" });
  el.value = value === undefined || value === null ? "" : value;
  el.addEventListener(opts.multiline || opts.live ? "input" : "change", function () { onChange(el.value); });
  if (opts.multiline) el.addEventListener("change", function () { onChange(el.value); });
  return el;
}


/* ====================================================================== character editor */
function openCharacterEditor(id) {
  var loading = id ? loadChar(id) : Promise.resolve(null);
  loading.then(function (existing) {
    var char = existing ? E.clone(existing) : newCharacter({});
    var isNew = !existing;
    var d = char.data;
    d.extensions = d.extensions || {};
    d.extensions.depth_prompt = Object.assign({ prompt: "", depth: 4, role: "system" }, d.extensions.depth_prompt || {});
    var dirty = false;
    function mark() { dirty = true; updateTokens(); }
    var tokenLine = h("span", { class: "mu-muted ch-small" });
    function updateTokens() {
      var perm = E.estimateTokens(d.description) + E.estimateTokens(d.personality) + E.estimateTokens(d.scenario);
      var all = perm + E.estimateTokens(d.first_mes) + E.estimateTokens(d.mes_example);
      tokenLine.textContent = t("char_tokens", { total: fmtNum(all), perm: fmtNum(perm) });
    }
    var avatarImg = h("img", { class: "ch-ed-avatar-img", src: char.avatar || defaultAvatar(d.name || "?", char.id), alt: "" });
    var avatarBox = h("div", { class: "ch-ed-avatar" }, [avatarImg,
      h("div", { class: "ch-ed-avatar-actions" }, [
        h("button", { class: "mu-btn small", type: "button", html: icon("image", 14) + esc(t("change_avatar")), onclick: function () {
          pickFile("image/*").then(function (files) {
            if (!files[0]) return;
            readAs(files[0], "dataurl").then(function (url) { return resizeImage(url, 1024, 1536, "image/png"); }).then(function (url) { char.avatar = url; avatarImg.src = url; mark(); });
          });
        } }),
        char.avatar ? h("button", { class: "mu-btn small ghost", type: "button", text: t("remove"), onclick: function () { char.avatar = ""; avatarImg.src = defaultAvatar(d.name || "?", char.id); mark(); } }) : null
      ])
    ]);
    function fieldFor(key, labelKey, opts) {
      opts = opts || {};
      var el = textInput(d[key], function (v) { d[key] = v; mark(); if (key === "name" && !char.avatar) avatarImg.src = defaultAvatar(v || "?", char.id); }, Object.assign({ live: true }, opts));
      var row = h("div", { class: "ch-field" }, [h("div", { class: "ch-label-row" }, [h("span", { class: "ch-label", text: t(labelKey) }),
        opts.multiline ? h("span", { class: "ch-tok", text: "" }) : null,
        assistButton(el, assistSpec(key, t(labelKey))),
        opts.expand !== false && opts.multiline ? h("button", { class: "ch-mini", type: "button", title: t("expand"), html: icon("panel", 13), onclick: function () {
          promptBox(t(labelKey), el.value, { multiline: true }).then(function (v) { if (v !== null) { el.value = v; d[key] = v; mark(); } });
        } }) : null]), el, opts.hint ? h("span", { class: "ch-hint", text: t(opts.hint) }) : null]);
      if (opts.multiline) {
        var tok = row.querySelector(".ch-tok");
        var upd = function () { tok.textContent = "≈" + fmtNum(E.estimateTokens(el.value)) + " " + t("tok"); };
        el.addEventListener("input", upd); upd();
        setTimeout(function () { autosizeField(el); }, 0);
        el.addEventListener("input", function () { autosizeField(el); });
      }
      return row;
    }
    // greetings
    var greetWrap = h("div", { class: "ch-greetings" });
    function drawGreetings() {
      greetWrap.innerHTML = "";
      (d.alternate_greetings || []).forEach(function (g, i) {
        var ta = textInput(g, function (v) { d.alternate_greetings[i] = v; mark(); }, { multiline: true, rows: 3, live: true });
        greetWrap.appendChild(h("div", { class: "ch-greet ch-field" }, [h("div", { class: "ch-label-row" }, [h("span", { class: "ch-label", text: t("alt_greeting_n", { n: i + 1 }) }),
          assistButton(ta, assistSpec("alternate_greeting:" + i, t("alt_greeting_n", { n: i + 1 }), "alternate_greeting")),
          h("button", { class: "ch-mini", type: "button", title: t("delete"), html: icon("trash", 13), onclick: function () { d.alternate_greetings.splice(i, 1); drawGreetings(); mark(); } })]), ta]));
      });
      greetWrap.appendChild(h("div", { class: "mu-row" }, [
        h("button", { class: "mu-btn small", type: "button", html: icon("plus", 14) + esc(t("add_greeting")), onclick: function () { d.alternate_greetings = d.alternate_greetings || []; d.alternate_greetings.push(""); drawGreetings(); mark(); } }),
        // r37: a new greeting drafted by the co-writer from the card (previewed, nothing is kept until «Accept»).
        assistEnabled() ? h("button", { class: "mu-btn small ghost", type: "button", html: icon("sparkle", 14) + esc(t("as_suggest_greeting")), onclick: function () {
          d.alternate_greetings = d.alternate_greetings || []; d.alternate_greetings.push(""); drawGreetings(); mark();
          var tas = $$(".ch-greet textarea", greetWrap); var ta = tas[tas.length - 1];
          if (ta) assistPreview(ta, ta.__assist, { action: "fill", instruction: "Write a different opening situation from the first message and the other greetings." });
        } }) : null]));
    }
    drawGreetings();
    var tagsInput = textInput((d.tags || []).join(", "), function (v) { d.tags = v.split(",").map(function (x) { return x.trim(); }).filter(Boolean); mark(); }, { placeholder: t("tags_placeholder") });
    // r37: the co-writer. Each field registers its spec; the side panel reaches fields on other tabs through reveal().
    var FIELD_TABS = { basic: ["name", "description", "first_mes", "scenario", "alternate_greeting"], persona: ["personality", "mes_example", "creator_notes", "tags", "creator", "character_version"],
      advanced: ["system_prompt", "post_history_instructions", "depth_prompt"] };
    var FIELD_LABELS = { name: "f_name", description: "f_description", first_mes: "f_first_mes", scenario: "f_scenario", personality: "f_personality", mes_example: "f_mes_example",
      creator_notes: "f_creator_notes", tags: "f_tags", creator: "f_creator", character_version: "f_version", system_prompt: "f_system_prompt", post_history_instructions: "f_post_history", depth_prompt: "f_depth_prompt" };
    var assistHost = { kind: "card", box: null, fieldKeys: Object.keys(FIELD_LABELS).concat(["alternate_greeting:N"]),
      card: function () { return d; }, world: function () { return cardWorld(char); },
      label: function (key) { return FIELD_LABELS[key] ? t(FIELD_LABELS[key]) : ""; },
      reveal: function (key) {
        var g = /^alternate_greeting:(\d+)$/.exec(key);
        if (g) { d.alternate_greetings = d.alternate_greetings || []; while (d.alternate_greetings.length <= Number(g[1])) d.alternate_greetings.push(""); drawGreetings(); }
        var tab = Object.keys(FIELD_TABS).find(function (k) { return FIELD_TABS[k].indexOf(g ? "alternate_greeting" : key) >= 0; });
        if (!tab) return null;
        if (current !== tab || g) { current = tab; drawTab(); }
        return pane.querySelector('[data-assist="' + key + '"]');
      },
      addEntryTo: function () { return cardWorld(char) || assistPickWorld(document.querySelector(".ch-as-ent-acts .primary") || pane, assistSpec("description", "")); },
      onEntryAdded: function (e, world) { toast(t("as_entry_added", { name: e.comment, book: world.name }), "ok"); } };
    function assistSpec(key, label, field) {
      return { field: field || key, label: label, host: assistHost, ctx: function () { return { card: d, world: cardWorld(char) }; },
        linkWorld: function (world) { char.worldId = world.id; worldSelect.appendChild(h("option", { value: world.id, text: world.name })); worldSelect.value = world.id; mark(); },
        onEntryAdded: assistHost.onEntryAdded };
    }
    var worldSelect = selectEl([["", t("none")]].concat(S.worldIndex.map(function (w) { return [w.id, w.name]; })), char.worldId || "", function (v) { char.worldId = v; mark(); });
    var tabs = [
      ["basic", t("ed_basic"), function () {
        return h("div", { class: "ch-ed-grid" }, [
          avatarBox,
          h("div", { class: "ch-ed-col" }, [
            fieldFor("name", "f_name", { placeholder: t("f_name_ph") }),
            fieldFor("description", "f_description", { multiline: true, rows: 8, hint: "f_description_hint" }),
            fieldFor("first_mes", "f_first_mes", { multiline: true, rows: 5, hint: "f_first_mes_hint" }),
            greetWrap,
            fieldFor("scenario", "f_scenario", { multiline: true, rows: 3 })
          ])
        ]);
      }],
      ["persona", t("ed_personality"), function () {
        return h("div", { class: "ch-ed-col" }, [
          fieldFor("personality", "f_personality", { multiline: true, rows: 4 }),
          fieldFor("mes_example", "f_mes_example", { multiline: true, rows: 10, hint: "f_mes_example_hint", mono: true }),
          fieldFor("creator_notes", "f_creator_notes", { multiline: true, rows: 4, hint: "f_creator_notes_hint" }),
          h("div", { class: "ch-field" }, [h("div", { class: "ch-label-row" }, [h("span", { class: "ch-label", text: t("f_tags") }), assistButton(tagsInput, assistSpec("tags", t("f_tags")))]), tagsInput]),
          h("div", { class: "ch-two" }, [fieldFor("creator", "f_creator"), fieldFor("character_version", "f_version")])
        ]);
      }],
      ["advanced", t("ed_advanced"), function () {
        var dp = d.extensions.depth_prompt;
        return h("div", { class: "ch-ed-col" }, [
          h("div", { class: "mu-note", text: t("ed_advanced_note") }),
          fieldFor("system_prompt", "f_system_prompt", { multiline: true, rows: 4, hint: "f_system_prompt_hint" }),
          fieldFor("post_history_instructions", "f_post_history", { multiline: true, rows: 4, hint: "f_post_history_hint" }),
          (function () {
            var dpInput = textInput(dp.prompt, function (v) { dp.prompt = v; mark(); }, { multiline: true, rows: 3, live: true });
            return h("div", { class: "ch-field" }, [h("div", { class: "ch-label-row" }, [h("span", { class: "ch-label", text: t("f_depth_prompt") }), assistButton(dpInput, assistSpec("depth_prompt", t("f_depth_prompt")))]),
            dpInput,
            h("div", { class: "ch-two" }, [
              field("depth", textInput(dp.depth, function (v) { dp.depth = clamp(parseInt(v, 10) || 0, 0, 999); mark(); }, { type: "number" })),
              field("role", selectEl([["system", t("role_system")], ["user", t("role_user")], ["assistant", t("role_assistant")]], dp.role, function (v) { dp.role = v; mark(); }))
            ]), h("span", { class: "ch-hint", text: t("f_depth_prompt_hint") })]);
          })(),
          slider("f_talkativeness", d.extensions.talkativeness === undefined ? 0.5 : d.extensions.talkativeness, 0, 1, 0.05, function (v) { d.extensions.talkativeness = v; mark(); }),
          field("f_lorebook", worldSelect, "f_lorebook_hint"),
          (function () {
            // A card imported without its lorebook keeps the book embedded (it is exported back as it was);
            // here it can still become a real lorebook linked to the character.
            var book = d.character_book;
            if (!book || !(book.entries || []).length) return null;
            var box = h("div", { class: "ch-embedded" }, [h("span", { html: icon("book", 15) + esc(t("embedded_book_note", { count: book.entries.length })) })]);
            box.appendChild(h("button", { class: "mu-btn small", type: "button", text: t("embedded_book_import"), onclick: function () {
              var world = E.importWorld(book, book.name || d.name).world;
              world.id = E.uid("w");
              world.name = String(book.name || "").trim() || t("char_book_name", { name: d.name });
              saveWorld(world);
              delete d.character_book;
              char.worldId = world.id;
              worldSelect.appendChild(h("option", { value: world.id, text: world.name }));
              worldSelect.value = world.id;
              box.remove(); mark();
              toast(t("lorebook_imported", { name: world.name, count: Object.keys(world.entries).length }), "ok");
            } }));
            return box;
          })(),
          h("div", { class: "mu-row" }, [
            h("button", { class: "mu-btn small", type: "button", html: icon("image", 14) + esc(t("char_background")), onclick: function () {
              pickFile("image/*").then(function (files) { if (!files[0]) return; readAs(files[0], "dataurl").then(function (u) { return resizeImage(u, 2400, 1600, "image/webp", 0.85); }).then(function (u) { char.background = u; mark(); toast(t("background_set"), "ok"); }); });
            } }),
            char.background ? h("button", { class: "mu-btn small ghost", type: "button", text: t("remove_background"), onclick: function () { char.background = ""; mark(); } }) : null
          ])
        ]);
      }]
    ];
    var current = "basic";
    var tabBar = h("div", { class: "mu-tabs" });
    var pane = h("div", { class: "ch-ed-pane" });
    function drawTab() {
      tabBar.innerHTML = "";
      tabs.forEach(function (tab) {
        var b = h("button", { class: "mu-tab", type: "button", "aria-selected": String(current === tab[0]), text: tab[1] });
        b.addEventListener("click", function () { current = tab[0]; drawTab(); });
        tabBar.appendChild(b);
      });
      pane.innerHTML = "";
      pane.appendChild(tabs.find(function (x) { return x[0] === current; })[2]());
    }
    drawTab();
    updateTokens();
    var modal = openModal({
      title: isNew ? t("new_character") : t("edit_character"), icon: "user", full: true, sticky: true,
      headExtra: h("div", { class: "mu-row" }, [tokenLine,
        h("button", { class: "mu-btn small ch-as-open", type: "button", title: t("as_panel_title"), html: icon("sparkle", 14) + "<span>" + esc(t("as_panel")) + "</span>", onclick: function () { assistPanel(assistHost); } }),
        !isNew ? iconBtn("image", "export_png", function () { exportCharacter(char.id, "png"); }) : null,
        !isNew ? iconBtn("file", "export_json", function () { exportCharacter(char.id, "json"); }) : null]),
      content: h("div", { class: "ch-editor" }, [tabBar, pane]),
      buttons: [
        !isNew ? { label: t("delete"), kind: "danger ghost", close: false, onClick: function (m) { deleteCharacter(char.id).then(function (ok) { if (ok) { dirty = false; m.close(); } }); } } : null,
        { label: t("cancel"), kind: "ghost" },
        { label: isNew ? t("create") : t("save"), kind: "primary", close: false, onClick: function (m) {
          if (!d.name.trim()) { toast(t("name_required"), "error"); return; }
          d.name = d.name.trim();
          makeThumb(char.avatar).then(function (thumb) {
            char.thumb = thumb;
            return saveCharacter(char);
          }).then(function () {
            dirty = false;
            m.close();
            if (S.char && S.char.id === char.id) { S.char = char; render(); }
            else if (isNew) openCharacter(char.id);
            else render();
            toast(t("saved"), "ok");
          });
        } }
      ].filter(Boolean)
    });
    assistHost.box = modal.box;
    modal.beforeClose = function () {
      if (!dirty) return true;
      confirmBox(t("discard_changes_q"), { danger: true, ok: t("discard") }).then(function (ok) { if (ok) { dirty = false; modal.close(); } });
      return false;
    };
  });
}
function autosizeField(el) { if (!el || el.tagName !== "TEXTAREA") return; el.style.height = "auto"; el.style.height = Math.min(Math.max(el.scrollHeight + 2, 70), 520) + "px"; }
function toggleFav(id) {
  loadChar(id).then(function (char) {
    if (!char) return;
    char.fav = !char.fav;
    char.data.extensions = char.data.extensions || {};
    char.data.extensions.fav = char.fav;
    saveCharacter(char).then(render);
  });
}
function duplicateCharacter(id) {
  loadChar(id).then(function (char) {
    var copy = E.clone(char);
    copy.id = E.uid("c"); copy.created = copy.updated = Date.now();
    copy.data.name = char.data.name + " " + t("copy_suffix");
    saveCharacter(copy).then(function () { render(); toast(t("duplicated"), "ok"); });
  });
}
function deleteCharacter(id) {
  var row = S.index.find(function (c) { return c.id === id; });
  return confirmBox(t("delete_char_q", { name: row ? row.name : "" }), { danger: true, ok: t("delete") }).then(function (ok) {
    if (!ok) return false;
    return Store.list("chats/" + id + "/").then(function (keys) {
      return Promise.all(keys.map(function (k) { return Store.remove(k.key); }));
    }).then(function () {
      Store.remove("chatlist/" + id);
      Store.remove("chars/" + id);
      delete S.chars[id];
      S.index = S.index.filter(function (c) { return c.id !== id; });
      saveIndex(); saveIndex.flush();
      if (S.char && S.char.id === id) { S.char = null; S.chat = null; S.view = "home"; }
      render();
      toast(t("deleted"), "ok");
      return true;
    });
  });
}

/* ---------------------------------------------------------------- import / export */
/* Round 29 phase 2: one «Import» dialog for files and links. Files (PNG/JSON cards of SillyTavern, chub, RisuAI,
 * Agnai, JanitorAI, Pygmalion, TextGen…, lorebooks of ST/chub/NovelAI/Agnai/RisuAI, presets, chats) and links
 * (chub, JanitorAI, Pygmalion, AICharacterCards, RisuRealm, Perchance, a direct PNG/JSON link) are first READ
 * into preview cards; nothing is saved until «Import» is pressed. Links go through Memu.fetchImport (MemuAI
 * downloads, the module never touches the network). */
var IMPORT_SOURCES = {
  chub: "Chub", "chub-lorebook": "Chub", janitor: "JanitorAI", pygmalion: "Pygmalion.chat", aicc: "AICharacterCards",
  risu: "RisuRealm", perchance: "Perchance", generic: ""
};
var ORIGIN_KEYS = { risu: "RisuAI", chub: "Chub", agnai: "Agnai", janitor: "JanitorAI", perchance: "Perchance",
  textgen: "TextGen / Pygmalion", v3: "Character Card V3", v2: "Character Card V2", v1: "Character Card V1" };
var BOOK_FORMATS = { sillytavern: "SillyTavern World Info", character_book: "Character Book (V2/V3)", chub: "Chub",
  card: "Character Book (V2/V3)", novelai: "NovelAI", agnai: "Agnai", risu: "RisuAI", lorebook_v3: "Lorebook V3" };
function importFlow() { openImportDialog({}); }
function importFiles(files) { openImportDialog({ files: files }); }
function errText(e) {
  var m = e && e.message ? e.message : String(e);
  return { no_card: t("err_no_card"), not_png: t("err_not_png"), bad_card: t("err_bad_card"), bad_lorebook: t("err_bad_lorebook"),
    bad_json: t("err_bad_json"), unsupported: t("err_unsupported") }[m] || m;
}
function bytesToText(bytes) { return new TextDecoder("utf-8").decode(bytes).replace(/^\ufeff/, ""); }
/** Read one file (or downloaded bytes) into a preview item. Never saves anything. */
function analyzeBytes(bytes, fileName, mime, extra) {
  extra = extra || {};
  var item = { id: E.uid("i"), fileName: fileName || "", selected: true, source: extra.source || "", page: extra.page || "",
    importBook: true, makeGlobal: false };
  var lower = String(fileName || "").toLowerCase();
  return Promise.resolve().then(function () {
    if (E.isPng(bytes)) {
      var info = E.readCardInfo(bytes);
      item.kind = "character"; item.json = info.json; item.chunk = info.chunk;
      item.avatar = bytesToDataUrl(bytes, "image/png");
      return item;
    }
    if (/\.jsonl$/.test(lower)) { item.kind = "chat"; item.text = bytesToText(bytes); return item; }
    if (/\.png$/.test(lower)) throw new Error("not_png");
    if (/^image\//.test(mime || "") || /\.(webp|jpe?g|gif)$/.test(lower)) throw new Error("no_card");
    var json;
    try { json = JSON.parse(bytesToText(bytes)); } catch (e) { throw new Error(/\.json$/.test(lower) || !lower ? "bad_json" : "unsupported"); }
    var kind = extra.kind === "lorebook" ? "lorebook" : E.detectJson(json);
    if (kind === "character" && E.isBookCard(json) && extra.kind !== "character") kind = "lorebook";
    if (!kind) throw new Error("unsupported");
    item.kind = kind; item.json = json;
    if (extra.avatar) item.avatar = "data:" + (extra.avatar.mime || "image/png") + ";base64," + extra.avatar.base64;
    return item;
  }).then(function (it) {
    if (it.kind === "character") {
      it.data = E.cardToData(it.json);
      if (!it.data.name) throw new Error("bad_card");
      it.name = it.data.name;
      it.origin = E.cardOrigin(it.json);
      it.spec = it.json.spec === "chara_card_v3" || it.chunk === "ccv3" ? "V3" : it.json.spec === "chara_card_v2" ? "V2" : it.origin === "agnai" ? "Agnai" : "V1";
      var book = it.data.character_book;
      it.bookCount = book && Array.isArray(book.entries) ? book.entries.length : 0;
      it.tokens = E.estimateTokens([it.data.description, it.data.personality, it.data.scenario, it.data.first_mes, it.data.mes_example, it.data.system_prompt].join("\n"));
    } else if (it.kind === "lorebook") {
      var fallback = extra.name || String(fileName || "").replace(/\.(json|png)$/i, "") || t("lorebook");
      var r = E.importWorld(it.json, fallback);
      it.world = r.world; it.format = r.format; it.count = r.count;
      if (extra.name && (r.format === "sillytavern" || !String(r.world.name || "").trim() || /^Data:?\s*$/.test(r.world.name))) it.world.name = extra.name;
      it.name = it.world.name || fallback;
    } else if (it.kind === "preset") {
      it.name = String(fileName || "").replace(/\.json$/i, "") || t("presets");
    } else if (it.kind === "chat") {
      it.name = fileName;
    }
    return it;
  });
}
function analyzeFile(file) {
  return readAs(file, "buffer").then(function (buf) { return analyzeBytes(new Uint8Array(buf), file.name, file.type); })
    .catch(function (e) { return { id: E.uid("i"), fileName: file.name, name: file.name, kind: "error", error: errText(e) }; });
}
/** The source a typed link/id points to, recognised locally for instant feedback (MemuAI decides for real). */
function guessSource(text) {
  var s = String(text || "").trim();
  if (!s) return "";
  var host = "";
  var m = /^https?:\/\/([^\/?#]+)/i.exec(s);
  if (m) host = m[1].toLowerCase();
  if (host) {
    if (/pygmalion\.chat$/.test(host)) return "pygmalion";
    if (/janitorai|jannyai\.com$/.test(host)) return "janitor";
    if (/aicharactercards\.com$/.test(host)) return "aicc";
    if (/(^|\.)chub\.ai$|(^|\.)characterhub\.org$/.test(host)) return /\/lorebooks\//i.test(s) ? "chub-lorebook" : "chub";
    if (host === "realm.risuai.net") return "risu";
    if (/perchance\.org$/.test(host)) return "perchance";
    return "generic";
  }
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_character/i.test(s)) return "janitor";
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return "pygmalion";
  if (/^AICC\//i.test(s)) return "aicc";
  if (/^\w+~[a-f0-9]{32}\.gz$/i.test(s)) return "perchance";
  if (/^lorebooks\/[^\/\s]+\/[^\/\s]+$/i.test(s)) return "chub-lorebook";
  if (/^[^\/\s]+\/[^\/\s]+$/.test(s)) return "chub";
  return "";
}
function sourceName(key) { return key === "generic" ? t("src_direct") : (IMPORT_SOURCES[key] || key); }
function netImportAvailable() { return (Memu.manifest && (Memu.manifest.permissions || []).indexOf("net-import") >= 0) && typeof Memu.fetchImport === "function"; }

function openImportDialog(opts) {
  opts = opts || {};
  var mode = opts.mode === "lorebook" ? "lorebook" : "all";
  var items = [];
  var busy = 0;
  var modal = openModal({ title: t(mode === "lorebook" ? "import_lorebook_title" : "import_title"), icon: "upload", wide: true,
    headExtra: iconBtn("help", "import_help", function () { openImportHelp(); }), content: function (body, api) { modal = api; build(body); } });
  var drop, list, urlInput, urlChip, urlBtn, urlStatus, footBtn, footInfo;
  function build(body) {
    body.classList.add("ch-imp");
    var accept = mode === "lorebook" ? ".json,.png,application/json,image/png" : ".png,.json,.jsonl,image/png,application/json";
    drop = h("button", { class: "ch-imp-drop", type: "button", onclick: function () { pickFile(accept, true).then(addFiles); } }, [
      h("span", { class: "ch-imp-drop-ic", html: icon("upload", 26) }),
      h("b", { text: t(mode === "lorebook" ? "imp_drop_lorebook" : "imp_drop") }),
      h("span", { class: "mu-muted ch-small", text: t(mode === "lorebook" ? "imp_drop_lorebook_hint" : "imp_drop_hint") })
    ]);
    ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("over"); });
    drop.addEventListener("drop", function (e) { e.preventDefault(); drop.classList.remove("over"); addFiles(Array.prototype.slice.call(e.dataTransfer.files || [])); });
    body.appendChild(drop);
    if (netImportAvailable()) {
      urlInput = h("input", { class: "mu-input ch-imp-url", type: "text", spellcheck: "false", autocomplete: "off",
        placeholder: t(mode === "lorebook" ? "imp_url_lorebook_ph" : "imp_url_ph"), "aria-label": t("imp_url_label") });
      urlChip = h("span", { class: "ch-imp-chip", hidden: true });
      urlBtn = h("button", { class: "mu-btn primary", type: "button", html: icon("download", 15) + esc(t("imp_fetch")), onclick: fetchUrl });
      urlStatus = h("div", { class: "ch-imp-status", role: "status" });
      urlInput.addEventListener("input", function () { updateChip(); urlStatus.textContent = ""; urlStatus.className = "ch-imp-status"; });
      urlInput.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); fetchUrl(); } });
      urlInput.addEventListener("paste", function () { setTimeout(updateChip, 0); });
      body.appendChild(h("div", { class: "ch-imp-or" }, [h("span", { text: t("imp_or_link") })]));
      body.appendChild(h("div", { class: "ch-imp-urlrow" }, [h("div", { class: "ch-imp-urlbox" }, [h("span", { class: "ch-imp-url-ic", html: icon("link", 15) }), urlInput, urlChip]), urlBtn]));
      body.appendChild(urlStatus);
      var ex = mode === "lorebook" ? [["chub-lorebook", "lorebooks/bartleby/example-lorebook"]]
        : [["chub", "Anonymous/example-character"], ["janitor", "ddd1498a-a370-4136-b138-a8cd9461fdfe_character-aqua-the-useless-goddess"],
          ["pygmalion", "a7ca95a1-0c88-4e23-91b3-149db1e78ab9"], ["aicc", "AICC/aicharcards/the-game-master"],
          ["risu", "https://realm.risuai.net/character/3ca54c71-6efe-46a2-b9d0-4f62df23d712"],
          ["perchance", "Loreena~cb3a1b531477378db7cad0148ba62d71.gz"], ["generic", "https://files.catbox.moe/…png"]];
      body.appendChild(h("details", { class: "ch-imp-sources" }, [
        h("summary", { text: t("imp_sources") }),
        h("div", { class: "ch-imp-srclist" }, ex.map(function (row) {
          return h("button", { class: "ch-imp-src", type: "button", title: t("imp_use_example"), onclick: function () {
            if (row[1].indexOf("…") >= 0) { urlInput.value = "https://files.catbox.moe/"; } else urlInput.value = row[1];
            urlInput.focus(); updateChip();
          } }, [h("b", { text: sourceName(row[0]) }), h("code", { text: row[1] })]);
        })),
        h("p", { class: "mu-muted ch-small", text: t("imp_sources_hint") })
      ]));
    }
    list = h("div", { class: "ch-imp-list" });
    body.appendChild(list);
    footInfo = h("span", { class: "mu-muted ch-small ch-imp-footinfo" });
    footBtn = h("button", { class: "mu-btn primary", type: "button", onclick: commit });
    modal.foot.appendChild(footInfo);
    modal.foot.appendChild(h("span", { class: "mu-grow" }));
    modal.foot.appendChild(h("button", { class: "mu-btn", type: "button", text: t("cancel"), onclick: function () { modal.close(); } }));
    modal.foot.appendChild(footBtn);
    modal.box.appendChild(modal.foot);
    drawList();
    if (opts.files && opts.files.length) addFiles(opts.files);
    if (opts.url && urlInput) { urlInput.value = opts.url; updateChip(); }
    setTimeout(function () { (urlInput || drop).focus(); }, 60);
  }
  function updateChip() {
    if (!urlChip) return;
    var key = guessSource(urlInput.value);
    urlChip.hidden = !key;
    urlChip.textContent = key ? sourceName(key) + (key === "chub-lorebook" ? " · " + t("lorebook") : "") : "";
    urlChip.className = "ch-imp-chip src-" + key;
  }
  function setStatus(text, kind) { if (urlStatus) { urlStatus.textContent = text || ""; urlStatus.className = "ch-imp-status" + (kind ? " " + kind : ""); } }
  function fetchUrl() {
    var input = urlInput.value.trim();
    if (!input) { urlInput.focus(); return; }
    var key = guessSource(input);
    busy++; urlBtn.disabled = true; urlBtn.classList.add("loading");
    setStatus(t("imp_downloading", { source: sourceName(key) || input }), "busy");
    Memu.fetchImport(input, { kind: mode === "lorebook" ? "lorebook" : "auto" }).then(function (res) {
      var file = res.file || {};
      var bytes = E.base64ToBytes(file.base64 || "");
      return analyzeBytes(bytes, file.name || res.name, file.mime, { source: res.source, page: res.page, avatar: res.avatar,
        kind: res.type === "lorebook" ? "lorebook" : (mode === "lorebook" ? "lorebook" : "character"), name: res.type === "lorebook" ? res.name : "" });
    }).then(function (item) {
      items.push(item); urlInput.value = ""; updateChip();
      setStatus(t("imp_ready", { name: item.name }), "ok");
      drawList(item);
    }).catch(function (e) {
      setStatus(errText(e), "error");
    }).then(function () { busy--; urlBtn.disabled = false; urlBtn.classList.remove("loading"); });
  }
  function addFiles(files) {
    files = (files || []).filter(Boolean);
    if (!files.length) return;
    busy++; drawList();
    Promise.all(files.map(analyzeFile)).then(function (got) {
      got.forEach(function (it) {
        if (mode === "lorebook" && it.kind === "character" && it.data && it.data.character_book) {
          var r = E.importWorld({ data: { character_book: it.data.character_book, name: it.data.name } }, it.data.name);
          it = { id: it.id, kind: "lorebook", fileName: it.fileName, world: r.world, format: r.format, count: r.count, name: r.world.name || it.data.name, selected: true };
        } else if (mode === "lorebook" && it.kind !== "lorebook" && it.kind !== "error") {
          it = { id: it.id, kind: "error", fileName: it.fileName, name: it.fileName, error: t("err_not_lorebook") };
        }
        items.push(it);
      });
    }).then(function () { busy--; drawList(items[items.length - 1]); });
  }
  function drawList(focusItem) {
    list.innerHTML = "";
    drop.classList.toggle("compact", items.length > 0);
    if (busy && !items.length) list.appendChild(h("div", { class: "ch-imp-loading", text: t("imp_reading") }));
    var focusNode = null;
    items.forEach(function (it) { var node = previewCard(it); if (it === focusItem) focusNode = node; list.appendChild(node); });
    if (focusNode) setTimeout(function () {
      // Scroll the dialog body only (scrollIntoView would also shift the module page).
      var body = modal.body, top = focusNode.offsetTop - body.offsetTop - 8;
      if (top + focusNode.offsetHeight > body.scrollTop + body.clientHeight || top < body.scrollTop) body.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }, 30);
    var n = items.filter(function (x) { return x.selected && x.kind !== "error"; }).length;
    footBtn.disabled = !n;
    footBtn.textContent = n > 1 ? t("imp_import_n", { count: n }) : t("import");
    footInfo.textContent = items.length ? t("imp_selected", { count: n, total: items.length }) : t("imp_nothing_saved");
  }
  function remove(it) { items = items.filter(function (x) { return x !== it; }); drawList(); }
  function previewCard(it) {
    var check = h("input", { type: "checkbox", "aria-label": t("imp_include") });
    check.checked = !!it.selected && it.kind !== "error"; check.disabled = it.kind === "error";
    check.addEventListener("change", function () { it.selected = check.checked; drawList(); });
    var close = iconBtn("x", "imp_remove", function () { remove(it); });
    if (it.kind === "error") {
      return h("div", { class: "ch-imp-card error" }, [h("span", { class: "ch-imp-err-ic", html: icon("alert", 18) }),
        h("div", { class: "ch-imp-main" }, [h("b", { text: it.fileName || it.name }), h("span", { class: "ch-small", text: it.error })]), close]);
    }
    var chips = [];
    function chip(text, cls) { if (text) chips.push(h("span", { class: "ch-imp-tag" + (cls ? " " + cls : ""), text: text })); }
    var main = h("div", { class: "ch-imp-main" });
    var media;
    if (it.kind === "character") {
      var d = it.data;
      media = h("img", { class: "ch-imp-av", alt: "", src: it.avatar || defaultAvatar(d.name, it.id) });
      // The app that wrote the card (or the site it came from), then the card version — never the same word twice.
      var from = it.source ? sourceName(it.source) : (/^v\d$/.test(it.origin) ? "" : ORIGIN_KEYS[it.origin] || "");
      chip(from, "src");
      chip(it.spec === "Agnai" ? "" : t("imp_card", { version: it.spec }));
      chip(t("imp_tokens", { count: it.tokens }));
      if ((d.alternate_greetings || []).length) chip(t("imp_greetings", { count: d.alternate_greetings.length + 1 }));
      if (!it.avatar) chip(t("imp_no_avatar"), "warn");
      main.appendChild(h("div", { class: "ch-imp-name" }, [h("b", { text: d.name }), d.creator ? h("span", { class: "mu-muted ch-small", text: t("imp_by", { name: d.creator }) }) : null]));
      main.appendChild(h("div", { class: "ch-imp-tags" }, chips));
      // the preview reads like the chat will: {{char}}/{{user}} filled in (phase 2: raw «*{{char}} wipes the counter.*»)
      var prevSubst = function (text) { return text ? E.substitute(String(text), { char: d.name || "", user: userName(), lang: Memu.lang, variables: {}, globals: {} }) : ""; };
      var blurb = prevSubst(d.creator_notes || d.description).replace(/\s+/g, " ").trim();
      if (blurb) main.appendChild(h("p", { class: "ch-imp-blurb", text: blurb.length > 260 ? blurb.slice(0, 257) + "…" : blurb }));
      var firstMes = prevSubst(d.first_mes);
      if (firstMes) main.appendChild(h("p", { class: "ch-imp-first" }, [h("span", { class: "mu-muted", text: t("imp_first") + " " }), document.createTextNode(firstMes.replace(/\s+/g, " ").slice(0, 180) + (firstMes.length > 180 ? "…" : ""))]));
      if ((d.tags || []).length) main.appendChild(h("div", { class: "ch-imp-ctags" }, d.tags.slice(0, 10).map(function (x) { return h("span", { class: "ch-tag", text: x }); })));
      if (it.bookCount) {
        var bk = h("input", { type: "checkbox" }); bk.checked = it.importBook !== false;
        bk.addEventListener("change", function () { it.importBook = bk.checked; });
        main.appendChild(h("label", { class: "ch-imp-opt" }, [bk, h("span", { html: icon("book", 14) + esc(t("imp_with_book", { entries: t("entries_n", { count: it.bookCount }) })) })]));
        main.appendChild(h("div", { class: "ch-hint", text: t("imp_with_book_hint") }));
      }
    } else if (it.kind === "lorebook") {
      media = h("div", { class: "ch-imp-ic", html: icon("book", 26) });
      chip(it.source ? sourceName(it.source) : "", "src");
      chip(BOOK_FORMATS[it.format] || it.format);
      chip(t("entries_n", { count: it.count }));
      var nameInput = h("input", { class: "mu-input ch-imp-bookname", type: "text", value: it.world.name || it.name, "aria-label": t("f_name") });
      nameInput.addEventListener("input", function () { it.world.name = nameInput.value; it.name = nameInput.value; });
      main.appendChild(nameInput);
      main.appendChild(h("div", { class: "ch-imp-tags" }, chips));
      var sample = Object.keys(it.world.entries).slice(0, 6).map(function (k) {
        var e = it.world.entries[k];
        return h("li", {}, [h("b", { text: (e.comment || (e.key || []).slice(0, 3).join(", ") || "#" + e.uid) }), h("span", { class: "mu-muted", text: " — " + String(e.content || "").replace(/\s+/g, " ").slice(0, 90) })]);
      });
      if (sample.length) main.appendChild(h("ul", { class: "ch-imp-entries" }, sample));
      var gl = h("input", { type: "checkbox" }); gl.checked = !!it.makeGlobal;
      gl.addEventListener("change", function () { it.makeGlobal = gl.checked; });
      main.appendChild(h("label", { class: "ch-imp-opt" }, [gl, h("span", { text: t("imp_make_global") })]));
      if (S.char) {
        var ln = h("input", { type: "checkbox" }); ln.checked = !!it.linkChar;
        ln.addEventListener("change", function () { it.linkChar = ln.checked; });
        main.appendChild(h("label", { class: "ch-imp-opt" }, [ln, h("span", { text: t("imp_link_char", { name: S.char.data.name }) })]));
      }
    } else if (it.kind === "preset") {
      media = h("div", { class: "ch-imp-ic", html: icon("sliders", 26) });
      main.appendChild(h("b", { text: it.name })); main.appendChild(h("div", { class: "ch-small mu-muted", text: t("imp_kind_preset") }));
    } else {
      media = h("div", { class: "ch-imp-ic", html: icon("chat", 26) });
      main.appendChild(h("b", { text: it.name }));
      main.appendChild(h("div", { class: "ch-small mu-muted", text: S.char ? t("imp_kind_chat", { name: S.char.data.name }) : t("imp_chat_needs_char") }));
      if (!S.char) { it.selected = false; check.checked = false; check.disabled = true; }
    }
    var cls = "ch-imp-card kind-" + it.kind + (it.selected ? "" : " off");
    return h("div", { class: cls }, [h("label", { class: "ch-imp-check" }, [check]), media, main, close]);
  }
  function commit() {
    var chosen = items.filter(function (x) { return x.selected && x.kind !== "error"; });
    if (!chosen.length) return;
    footBtn.disabled = true;
    var chain = Promise.resolve(), done = 0, firstChar = null;
    chosen.forEach(function (it) {
      chain = chain.then(function () {
        if (it.kind === "character") return createFromCard(it.json, it.avatar || "", { data: it.data, importBook: it.importBook !== false, source: it.source, page: it.page })
          .then(function (char) { done++; if (!firstChar) firstChar = char; });
        if (it.kind === "lorebook") return saveImportedWorld(it).then(function () { done++; });
        if (it.kind === "preset") return importPresetJson(it.json, it.fileName).then(function () { done++; });
        if (it.kind === "chat" && S.char) return Promise.resolve(importChatText(it.text, it.fileName)).then(function () { done++; });
      }).catch(function (e) { toast(t("import_failed", { name: it.name || it.fileName, message: errText(e) }), "error"); });
    });
    chain.then(function () {
      if (done) toast(t("imported_items", { count: done }), "ok");
      modal.close();
      render();
      if (S.drawerOpen) renderDrawer();
      if (firstChar && chosen.length === 1 && !S.char) openCharacter(firstChar.id);
    });
  }
}
function saveImportedWorld(it) {
  var world = it.world;
  world.id = E.uid("w");
  world.name = String(world.name || it.name || t("lorebook")).trim() || t("lorebook");
  saveWorld(world);
  if (it.makeGlobal) { var g = S.settings.wi.global || (S.settings.wi.global = []); if (g.indexOf(world.id) < 0) g.push(world.id); saveSettings(); }
  if (it.linkChar && S.char) { S.char.worldId = world.id; saveCharacter(S.char); }
  if (typeof refreshTokens === "function") refreshTokens();
  return Promise.resolve(world);
}
function openImportHelp() {
  openModal({ title: t("import_help"), icon: "help", wide: true, content: function (body) {
    body.classList.add("ch-help");
    ["imp_help_files", "imp_help_links", "imp_help_lorebooks", "imp_help_export"].forEach(function (k) {
      body.appendChild(h("h4", { text: t(k + "_t") }));
      body.appendChild(h("p", { text: t(k) }));
    });
  }, buttons: [{ label: t("ok"), kind: "primary" }] });
}
function createFromCard(json, avatar, opts) {
  opts = opts || {};
  var data = opts.data ? E.clone(opts.data) : E.cardToData(json);
  if (!data.name) throw new Error("bad_card");
  var char = newCharacter(data, "");
  char.fav = !!(data.extensions && data.extensions.fav);
  if (opts.source) char.importedFrom = { source: opts.source, page: opts.page || "", at: Date.now() };
  var avatarStep = avatar ? resizeImage(avatar, 1024, 1536, "image/png").catch(function () { return avatar; }) : Promise.resolve("");
  return avatarStep.then(function (url) {
    char.avatar = url;
    return makeThumb(url);
  }).then(function (thumb) {
    char.thumb = thumb;
    var book = data.character_book;
    if (opts.importBook !== false && book && (book.entries || []).length) {
      var world = E.importWorld(book, book.name || data.name).world;
      world.id = E.uid("w");
      world.name = String(book.name || "").trim() || t("char_book_name", { name: data.name });
      saveWorld(world);
      char.worldId = world.id;
      // The book now lives in the linked lorebook (originalData keeps every field for the export).
      delete char.data.character_book;
    }
    return saveCharacter(char);
  }).then(function () { return char; });
}
function importEmbeddedBook(char) {
  var book = char && char.data && char.data.character_book;
  if (!book || !(book.entries || []).length) return Promise.resolve(null);
  var world = E.importWorld(book, book.name || char.data.name).world;
  world.id = E.uid("w");
  world.name = String(book.name || "").trim() || t("char_book_name", { name: char.data.name });
  saveWorld(world);
  char.worldId = world.id;
  delete char.data.character_book;
  return saveCharacter(char).then(function () { toast(t("lorebook_imported", { name: world.name, count: Object.keys(world.entries).length }), "ok"); return world; });
}
function cardWorld(char) { return char.worldId && S.worlds[char.worldId] ? S.worlds[char.worldId] : null; }
function exportCharacter(id, kind) {
  var char;
  loadChar(id).then(function (c) {
    char = c;
    if (!char) return null;
    return char.worldId ? loadWorld(char.worldId) : null;
  }).then(function (world) {
    if (!char) return;
    var stamp = { created: char.created, updated: char.updated };
    var v2 = E.dataToV2(char.data, world || null, stamp);
    var v3 = E.dataToV3(char.data, world || null, stamp);
    if (kind === "json") return download(safeFileName(char.data.name, ".json"), JSON.stringify(v3, null, 2));
    if (kind === "json2") return download(safeFileName(char.data.name, ".json"), JSON.stringify(v2, null, 2));
    var src = char.avatar || defaultAvatar(char.data.name, char.id);
    return loadImage(src).then(function (img) {
      var canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || 400; canvas.height = img.naturalHeight || 600;
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      var png = dataUrlToBytes(canvas.toDataURL("image/png"));
      var out = E.writeCardToPng(png, v2, v3);
      return download(safeFileName(char.data.name, ".png"), bytesToDataUrl(out, "image/png"));
    });
  }).catch(function (e) { toast(errText(e), "error"); });
}

/* ---------------------------------------------------------------- chats manager */
function openChatManager() {
  saveChat.flush();
  var modal = openModal({ title: t("chats_of", { name: S.char.data.name }), icon: "chat", wide: true, content: function (body) {
    var bar = h("div", { class: "mu-row" }, [
      h("button", { class: "mu-btn small primary", type: "button", html: icon("plus", 14) + esc(t("new_chat")), onclick: function () { modal.close(); startNewChat(); } }),
      h("button", { class: "mu-btn small", type: "button", html: icon("upload", 14) + esc(t("import_chat")), onclick: function () { modal.close(); importChatFile(); } })
    ]);
    body.appendChild(bar);
    var list = h("div", { class: "ch-chatlist" });
    body.appendChild(list);
    function draw() {
      list.innerHTML = "";
      var rows = S.chatList.slice().sort(function (a, b) { return (b.updated || 0) - (a.updated || 0); });
      if (!rows.length) list.appendChild(h("div", { class: "mu-empty", text: t("no_chats") }));
      rows.forEach(function (row) {
        var item = h("div", { class: "ch-chatrow" + (S.chat && S.chat.id === row.id ? " active" : "") }, [
          h("div", { class: "ch-chatrow-main" }, [
            h("div", { class: "ch-chatrow-title" }, [row.parent ? h("span", { class: "ch-tag", html: icon("branch", 12) + esc(t("branch")) }) : null, h("span", { text: row.title || t("untitled_chat") })]),
            h("div", { class: "ch-chatrow-prev", text: row.preview || "" }),
            h("div", { class: "ch-chatrow-meta", text: t("chat_meta", { count: row.count || 0, date: fmtDate(row.updated) }) + (row.bookmarks ? " · " + t("bookmarks_n", { count: row.bookmarks }) : "") })
          ]),
          h("div", { class: "ch-chatrow-actions" }, [
            iconBtn("edit", "rename", function (e) { e.stopPropagation(); renameChat(row.id).then(draw); }),
            iconBtn("copy", "duplicate", function (e) { e.stopPropagation(); duplicateChat(row.id).then(draw); }),
            iconBtn("download", "export", function (e) { e.stopPropagation(); Store.get("chats/" + S.char.id + "/" + row.id, null).then(function (c) { if (c) exportChat(c, "jsonl"); }); }),
            iconBtn("trash", "delete", function (e) { e.stopPropagation(); deleteChat(row.id).then(draw); })
          ])
        ]);
        item.addEventListener("click", function () { openChat(row.id).then(function () { modal.close(); render(); }); });
        list.appendChild(item);
      });
    }
    draw();
  } });
}
function renameChat(id) {
  var row = S.chatList.find(function (c) { return c.id === id; });
  return promptBox(t("rename_chat"), row ? row.title : "", { icon: "edit" }).then(function (name) {
    if (name === null) return;
    return Store.get("chats/" + S.char.id + "/" + id, null).then(function (chat) {
      if (!chat) return;
      chat.title = name.trim() || chat.title;
      if (S.chat && S.chat.id === id) { S.chat.title = chat.title; updateChatHeader(); return saveChatNow(); }
      if (row) row.title = chat.title;
      Store.set("chatlist/" + S.char.id, S.chatList);
      return Store.set("chats/" + S.char.id + "/" + id, chat);
    });
  });
}
function duplicateChat(id) {
  return Store.get("chats/" + S.char.id + "/" + id, null).then(function (chat) {
    if (!chat) return;
    var copy = E.clone(chat);
    copy.id = E.uid("chat"); copy.title = chat.title + " " + t("copy_suffix"); copy.created = copy.updated = Date.now();
    S.chatList.unshift(chatSummary(copy));
    Store.set("chatlist/" + S.char.id, S.chatList);
    return Store.set("chats/" + S.char.id + "/" + copy.id, copy);
  });
}
function deleteChat(id) {
  return confirmBox(t("delete_chat_q"), { danger: true, ok: t("delete") }).then(function (ok) {
    if (!ok) return;
    S.chatList = S.chatList.filter(function (c) { return c.id !== id; });
    Store.set("chatlist/" + S.char.id, S.chatList);
    var idx = S.index.find(function (c) { return c.id === S.char.id; });
    if (idx) { idx.chats = S.chatList.length; saveIndex(); }
    return Store.remove("chats/" + S.char.id + "/" + id).then(function () {
      if (S.chat && S.chat.id === id) {
        if (S.chatList[0]) return openChat(S.chatList[0].id).then(render);
        return startNewChat();
      }
    });
  });
}
function exportChat(chat, kind) {
  var user = userName(), name = S.char.data.name;
  if (kind === "txt") {
    var text = chat.messages.filter(function (m) { return !m.hidden; }).map(function (m) {
      return (m.role === "user" ? (m.name || user) : m.role === "system" ? t("narrator") : (m.name || name)) + ": " + (m.greeting ? displaySubst(m.mes) : m.mes);
    }).join("\n\n");
    return download(safeFileName(chat.title || name, ".txt"), text, "text/plain");
  }
  return download(safeFileName(chat.title || name, ".jsonl"), E.chatToJsonl(chat, user, name), "application/jsonl");
}
function importChatFile() {
  pickFile(".jsonl,.json").then(function (files) {
    if (!files[0]) return;
    readAs(files[0], "text").then(function (text) { return importChatText(text, files[0].name); }).catch(function (e) { toast(errText(e), "error"); });
  });
}
function importChatText(text, fileName) {
  if (!S.char) { toast(t("open_char_first"), "error"); return Promise.resolve(0); }
  var parsed = E.jsonlToChat(text);
  var now = Date.now();
  var chat = { id: E.uid("chat"), charId: S.char.id, title: String(fileName || "").replace(/\.jsonl?$/i, "") || t("imported_chat"), created: now, updated: now,
    messages: parsed.messages, meta: { variables: (parsed.header.chat_metadata && parsed.header.chat_metadata.variables) || {}, timedWorldInfo: {},
      note: { text: (parsed.header.chat_metadata && parsed.header.chat_metadata.note_prompt) || "", position: "chat", depth: 4, role: 0, interval: 1 } } };
  chat.messages.forEach(function (m) { if (m.role === "assistant" && !m.name) m.name = S.char.data.name; });
  S.chat = chat;
  S.chatList.unshift(chatSummary(chat));
  return saveChatNow().then(function () { render(); toast(t("chat_imported", { count: chat.messages.length }), "ok"); return 0; });
}

/* ---------------------------------------------------------------- author's note, variables, chat world, background */
function openAuthorsNote() {
  var note = S.chat.meta.note;
  openModal({ title: t("authors_note"), icon: "note", content: function (body) {
    body.appendChild(h("p", { class: "mu-muted", text: t("authors_note_hint") }));
    var ta = textInput(note.text, function (v) { note.text = v; saveChat(); }, { multiline: true, rows: 7, live: true });
    body.appendChild(ta);
    body.appendChild(h("div", { class: "ch-two" }, [
      field("position", selectEl([["chat", t("an_in_chat")], ["after", t("an_after")], ["before", t("an_before")], ["off", t("off")]], note.position, function (v) { note.position = v; saveChat(); })),
      field("depth", textInput(note.depth, function (v) { note.depth = clamp(parseInt(v, 10) || 0, 0, 999); saveChat(); }, { type: "number" }))
    ]));
    body.appendChild(h("div", { class: "ch-two" }, [
      field("role", selectEl([["0", t("role_system")], ["1", t("role_user")], ["2", t("role_assistant")]], String(note.role || 0), function (v) { note.role = Number(v); saveChat(); })),
      field("an_interval", textInput(note.interval, function (v) { note.interval = clamp(parseInt(v, 10) || 0, 0, 999); saveChat(); }, { type: "number" }), "an_interval_hint")
    ]));
    var charNote = h("div", { class: "mu-note", text: t("an_char_default_hint") });
    body.appendChild(charNote);
  }, buttons: [{ label: t("done"), kind: "primary" }], onClose: function () { renderChatView(); } });
}
function openVariables() {
  var vars = S.chat.meta.variables || (S.chat.meta.variables = {});
  openModal({ title: t("variables"), icon: "list", wide: true, content: function (body) {
    function draw() {
      body.innerHTML = "";
      body.appendChild(h("p", { class: "mu-muted", text: t("variables_hint") }));
      var keys = Object.keys(vars).sort();
      if (!keys.length) body.appendChild(h("div", { class: "mu-empty", text: t("no_variables") }));
      keys.forEach(function (k) {
        body.appendChild(h("div", { class: "ch-var" }, [h("code", { text: k }),
          textInput(vars[k], function (v) { vars[k] = v; saveChat(); }, { multiline: String(vars[k]).length > 60, rows: 3 }),
          iconBtn("trash", "delete", function () { delete vars[k]; saveChat(); draw(); })]));
      });
      body.appendChild(h("button", { class: "mu-btn small", type: "button", html: icon("plus", 14) + esc(t("add_variable")), onclick: function () {
        promptBox(t("variable_name"), "").then(function (name) { if (name && name.trim()) { vars[name.trim()] = ""; saveChat(); draw(); } });
      } }));
    }
    draw();
  } });
}
function chooseChatWorld() {
  var opts = [["", t("none")]].concat(S.worldIndex.map(function (w) { return [w.id, w.name]; }));
  openModal({ title: t("chat_lorebook"), icon: "globe", content: h("div", { class: "ch-form" }, [
    h("p", { class: "mu-muted", text: t("chat_lorebook_hint") }),
    selectEl(opts, S.chat.meta.worldId || "", function (v) { S.chat.meta.worldId = v; saveChat(); refreshTokens(); })
  ]), buttons: [{ label: t("done"), kind: "primary" }] });
}
function chooseBackground(scope) {
  pickFile("image/*").then(function (files) {
    if (!files[0]) return;
    readAs(files[0], "dataurl").then(function (u) { return resizeImage(u, 2400, 1600, "image/webp", 0.85); }).then(function (url) {
      if (scope === "chat") { S.chat.meta.background = url; saveChat(); }
      else { S.settings.ui.bg = url; saveSettings(); }
      applyAppearance();
    });
  });
}

/* ---------------------------------------------------------------- search & bookmarks */
function openSearch(bookmarksOnly) {
  openModal({ title: bookmarksOnly ? t("bookmarks") : t("search_chat"), icon: bookmarksOnly ? "bookmark" : "search", wide: true, content: function (body, modal) {
    var input = h("input", { class: "mu-input", type: "search", placeholder: t("search_placeholder"), autofocus: true });
    var scope = selectEl([["chat", t("scope_chat")], ["char", t("scope_char")]], "chat", function () { run(); });
    body.appendChild(h("div", { class: "ch-two wide" }, [input, scope]));
    var results = h("div", { class: "ch-results" });
    body.appendChild(results);
    var run = debounce(function () {
      var q = input.value.trim().toLowerCase();
      results.innerHTML = "";
      var sources = scope.value === "char" ? Promise.all(S.chatList.map(function (row) { return Store.get("chats/" + S.char.id + "/" + row.id, null); })) : Promise.resolve([S.chat]);
      sources.then(function (chats) {
        var hits = 0;
        chats.filter(Boolean).forEach(function (chat) {
          chat.messages.forEach(function (m, index) {
            if (bookmarksOnly && !m.bookmark) return;
            var text = String(m.mes || "");
            if (q && text.toLowerCase().indexOf(q) < 0 && String(m.bookmarkName || "").toLowerCase().indexOf(q) < 0) return;
            if (!q && !bookmarksOnly) return;
            if (++hits > 200) return;
            var at = q ? text.toLowerCase().indexOf(q) : 0;
            var snippet = text.slice(Math.max(0, at - 60), at + 140);
            var row = h("button", { class: "ch-result", type: "button" }, [
              h("div", { class: "ch-result-head" }, [h("b", { text: m.role === "user" ? (m.name || userName()) : (m.name || S.char.data.name) }),
                m.bookmark ? h("span", { class: "ch-tag mark", text: m.bookmarkName || t("bookmark") }) : null,
                h("span", { class: "mu-muted", text: (chat.id !== S.chat.id ? (chat.title || "") + " · " : "") + "#" + index })]),
              h("div", { class: "ch-result-text", html: q ? esc(snippet).replace(new RegExp(E.clone(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), function (x) { return "<mark>" + x + "</mark>"; }) : esc(snippet) })
            ]);
            row.addEventListener("click", function () {
              var go = chat.id === S.chat.id ? Promise.resolve() : openChat(chat.id).then(render);
              go.then(function () {
                modal.close();
                if (S.chat.messages.length - index > (S.renderLimit || 120)) { S.renderLimit = S.chat.messages.length - index + 20; renderMessages(); }
                var node = document.querySelector('.ch-msg[data-index="' + index + '"]');
                if (node) { node.scrollIntoView({ block: "center" }); node.classList.add("flash"); setTimeout(function () { node.classList.remove("flash"); }, 1600); }
              });
            });
            results.appendChild(row);
          });
        });
        if (!hits && (q || bookmarksOnly)) results.appendChild(h("div", { class: "mu-empty", text: t("nothing_found") }));
      });
    }, 220);
    input.addEventListener("input", run);
    run();
  } });
}

/* ---------------------------------------------------------------- prompt inspector */
function openInspector(built) {
  if (!built) {
    try { built = buildFor("normal", S.chat.messages, { dryRun: true }); } catch (e) { toast(e.message, "error"); return; }
  }
  openModal({ title: t("prompt_inspector"), icon: "code", full: true, content: function (body) {
    var stats = h("div", { class: "ch-insp-stats" }, [
      stat(t("insp_tokens"), "≈" + fmtNum(built.tokens)),
      stat(t("insp_context"), fmtNum(built.maxContext)),
      stat(t("insp_response"), fmtNum(built.maxTokens)),
      stat(t("insp_messages"), String(built.messages.length)),
      stat(t("insp_wi"), String((built.worldInfo || []).length))
    ]);
    body.appendChild(stats);
    if (built.warnings && built.warnings.length) body.appendChild(h("div", { class: "mu-note warn", text: built.warnings.map(function (w) { return t("warn_" + w); }).join(" · ") }));
    var grid = h("div", { class: "ch-insp" });
    var left = h("div", { class: "ch-insp-col" });
    left.appendChild(h("div", { class: "ch-insp-title", text: t("insp_breakdown") }));
    var total = built.breakdown.reduce(function (a, b) { return a + b.tokens; }, 0) || 1;
    built.breakdown.filter(function (b) { return b.tokens > 0; }).forEach(function (b) {
      left.appendChild(h("div", { class: "ch-insp-row" }, [h("span", { class: "ch-insp-name", text: b.name }), h("span", { class: "ch-insp-bar" }, [h("i", { style: { width: (b.tokens / total * 100).toFixed(1) + "%" } })]), h("span", { class: "mu-muted", text: fmtNum(b.tokens) })]));
    });
    if ((built.worldInfo || []).length) {
      left.appendChild(h("div", { class: "ch-insp-title", text: t("insp_wi") }));
      built.worldInfo.forEach(function (w) { left.appendChild(h("div", { class: "ch-insp-row" }, [h("span", { class: "ch-insp-name", text: (w.comment || "#" + w.uid) + " — " + w.world }), h("span", { class: "mu-muted", text: fmtNum(w.tokens) })])); });
    }
    left.appendChild(h("div", { class: "ch-insp-title", text: t("insp_params") }));
    left.appendChild(h("pre", { class: "ch-pre small", text: JSON.stringify(built.params, null, 1) }));
    var right = h("div", { class: "ch-insp-col wide" });
    right.appendChild(h("div", { class: "mu-row" }, [h("div", { class: "ch-insp-title", text: t("insp_sent") }), h("span", { class: "mu-grow" }),
      h("button", { class: "mu-btn small", type: "button", html: icon("copy", 14) + esc(t("copy_json")), onclick: function () {
        Memu.copy(JSON.stringify(built.messages.map(function (m) { return { role: m.role, content: m.content }; }), null, 2));
      } })]));
    built.messages.forEach(function (m) {
      var content = typeof m.content === "string" ? m.content : m.content.map(function (p) { return p.type === "text" ? p.text : "[image]"; }).join("\n");
      var det = h("details", { class: "ch-insp-msg " + m.role });
      det.appendChild(h("summary", {}, [h("span", { class: "ch-role " + m.role, text: m.role }), h("span", { class: "ch-insp-id", text: m._id || "" }), h("span", { class: "mu-grow" }), h("span", { class: "mu-muted", text: "≈" + fmtNum(E.messageTokens(m)) })]));
      det.appendChild(h("pre", { class: "ch-pre", text: content }));
      if (content.length < 1600) det.open = true;
      right.appendChild(det);
    });
    grid.appendChild(left); grid.appendChild(right);
    body.appendChild(grid);
  } });
  function stat(label, value) { return h("div", { class: "ch-stat" }, [h("div", { class: "ch-stat-v", text: value }), h("div", { class: "ch-stat-l", text: label })]); }
}

/* ---------------------------------------------------------------- personas */
function openPersonas(forChat) {
  openModal({ title: t("personas"), icon: "user", wide: true, content: function (body, modal) {
    drawPersonaManager(body, function () { modal.close(); render(); }, forChat);
  } });
}
function newPersona() {
  var p = { id: E.uid("p"), name: t("default_user_name"), description: "", avatar: "", position: 0, depth: 2, role: 0, worldId: "" };
  S.personas.push(p);
  savePersonas();
  return p;
}
function drawPersonaManager(body, onPick, forChat) {
  body.innerHTML = "";
  var current = activePersona();
  var bar = h("div", { class: "mu-row" }, [
    h("button", { class: "mu-btn small primary", type: "button", html: icon("plus", 14) + esc(t("new_persona")), onclick: function () { var p = newPersona(); editPersona(p, function () { drawPersonaManager(body, onPick, forChat); }); } }),
    h("span", { class: "mu-grow" }),
    forChat && S.chat ? h("span", { class: "mu-muted ch-small", text: t("persona_for_chat_hint") }) : h("span", { class: "mu-muted ch-small", text: t("persona_default_hint") })
  ]);
  body.appendChild(bar);
  var grid = h("div", { class: "ch-personas" });
  body.appendChild(grid);
  if (!S.personas.length) grid.appendChild(h("div", { class: "mu-empty", text: t("no_personas") }));
  S.personas.forEach(function (p) {
    var isDefault = S.settings.personaId === p.id;
    var lockedChar = S.char && S.settings.charPersona && S.settings.charPersona[S.char.id] === p.id;
    var lockedChat = S.chat && S.chat.meta && S.chat.meta.personaId === p.id;
    var card = h("div", { class: "ch-persona" + (current.id === p.id ? " active" : "") }, [
      h("img", { src: p.avatar || defaultAvatar(p.name || "U", "user" + p.id), alt: "" }),
      h("div", { class: "ch-persona-body" }, [
        h("div", { class: "ch-persona-title" }, [h("b", { text: p.name || t("default_user_name") }),
          isDefault ? h("span", { class: "ch-tag", text: t("default") }) : null,
          lockedChar ? h("span", { class: "ch-tag", html: icon("link", 11) + esc(t("locked_char")) }) : null,
          lockedChat ? h("span", { class: "ch-tag", html: icon("pin", 11) + esc(t("locked_chat")) }) : null]),
        h("div", { class: "ch-persona-desc", text: p.description || t("no_description") }),
        h("div", { class: "mu-row" }, [
          h("button", { class: "mu-btn small primary", type: "button", text: t("use"), onclick: function () {
            if (forChat && S.chat) { S.chat.meta.personaId = p.id; saveChat(); }
            else { S.settings.personaId = p.id; saveSettings(); }
            onPick();
          } }),
          iconBtn("edit", "edit", function () { editPersona(p, function () { drawPersonaManager(body, onPick, forChat); }); }),
          iconBtn("star", "make_default", function () { S.settings.personaId = p.id; saveSettings(); drawPersonaManager(body, onPick, forChat); }),
          S.char ? iconBtn("link", "lock_to_char", function () {
            S.settings.charPersona = S.settings.charPersona || {};
            if (S.settings.charPersona[S.char.id] === p.id) delete S.settings.charPersona[S.char.id]; else S.settings.charPersona[S.char.id] = p.id;
            saveSettings(); drawPersonaManager(body, onPick, forChat);
          }) : null,
          S.chat ? iconBtn("pin", "lock_to_chat", function () {
            S.chat.meta.personaId = S.chat.meta.personaId === p.id ? "" : p.id; saveChat(); drawPersonaManager(body, onPick, forChat);
          }) : null,
          iconBtn("trash", "delete", function () {
            confirmBox(t("delete_persona_q"), { danger: true, ok: t("delete") }).then(function (ok) {
              if (!ok) return;
              S.personas = S.personas.filter(function (x) { return x.id !== p.id; });
              savePersonas(); drawPersonaManager(body, onPick, forChat);
            });
          })
        ])
      ])
    ]);
    grid.appendChild(card);
  });
}
function editPersona(p, done) {
  var draft = E.clone(p);
  var img = h("img", { class: "ch-ed-avatar-img round", src: draft.avatar || defaultAvatar(draft.name || "U", "user" + draft.id), alt: "" });
  openModal({ title: t("edit_persona"), icon: "user", wide: true, content: h("div", { class: "ch-ed-grid" }, [
    h("div", { class: "ch-ed-avatar" }, [img, h("button", { class: "mu-btn small", type: "button", html: icon("image", 14) + esc(t("change_avatar")), onclick: function () {
      pickFile("image/*").then(function (files) { if (!files[0]) return; readAs(files[0], "dataurl").then(function (u) { return resizeImage(u, 512, 512, "image/webp", 0.9); }).then(function (u) { draft.avatar = u; img.src = u; }); });
    } })]),
    h("div", { class: "ch-ed-col" }, [
      field("f_name", textInput(draft.name, function (v) { draft.name = v; }, { live: true })),
      field("f_persona_description", textInput(draft.description, function (v) { draft.description = v; }, { multiline: true, rows: 7, live: true }), "f_persona_description_hint"),
      h("div", { class: "ch-two" }, [
        field("position", selectEl([["0", t("pd_in_prompt")], ["2", t("pd_top_an")], ["3", t("pd_bottom_an")], ["4", t("pd_at_depth")], ["9", t("pd_none")]], String(draft.position || 0), function (v) { draft.position = Number(v); })),
        field("depth", textInput(draft.depth, function (v) { draft.depth = clamp(parseInt(v, 10) || 0, 0, 999); }, { type: "number" }))
      ]),
      h("div", { class: "ch-two" }, [
        field("role", selectEl([["0", t("role_system")], ["1", t("role_user")], ["2", t("role_assistant")]], String(draft.role || 0), function (v) { draft.role = Number(v); })),
        field("f_persona_lorebook", selectEl([["", t("none")]].concat(S.worldIndex.map(function (w) { return [w.id, w.name]; })), draft.worldId || "", function (v) { draft.worldId = v; }))
      ])
    ])
  ]), buttons: [{ label: t("cancel"), kind: "ghost" }, { label: t("save"), kind: "primary", onClick: function () {
    Object.assign(p, draft); savePersonas(); renderSidebar(); if (done) done();
  } }] });
}


/* ====================================================================== presets */
var presetCache = {};
function presetName(row) { return row ? (Memu.lang === "ru" ? row.name_ru || row.name_en || row.name : row.name_en || row.name || row.name_ru) || row.id : ""; }
function loadPresetById(id) {
  return Store.get("presets/" + id, null).then(function (user) {
    if (user) return user;
    if (presetCache[id]) return E.clone(presetCache[id]);
    var builtin = S.presetIndex.find(function (p) { return p.id === id; });
    if (!builtin) return null;
    return Memu.asset("presets/" + id + ".json").then(function (json) { presetCache[id] = json; return E.clone(json); });
  });
}
// A shipped preset stays byte-faithful to its author (e.g. «Universal» keeps temperature 2), but a value that breaks
// most models gets a recommended effective value from index.json `recommended`. It is applied only to the untouched
// built-in copy, never saved by itself, and shown in the preset panel with a one-click way back to the preset value.
function applyPresetRecommended(id, raw, preset) {
  var info = S.presetIndex.find(function (x) { return x.id === id; });
  if (!info || !info.recommended || (raw.memu && raw.memu.edited)) return null;
  var out = {};
  Object.keys(info.recommended).forEach(function (k) {
    var to = info.recommended[k];
    if (preset[k] !== undefined && preset[k] !== to) { out[k] = { from: preset[k], to: to }; preset[k] = to; }
  });
  return Object.keys(out).length ? out : null;
}
var TEMP_RISKY = 1.5;
function fmtTemp(v) { var n = Number(v); return Number.isFinite(n) ? (Math.round(n * 100) / 100).toFixed(n * 10 % 1 ? 2 : 1) : String(v); }
function temperatureNote(p, rerender) {
  var ov = S.presetOverride && S.presetOverride.temperature;
  if (ov && p.temperature === ov.to) {
    return h("div", { class: "ch-temp-note", role: "note" }, [
      h("span", { class: "ch-temp-note-text", html: icon("alert", 14) + esc(t("temp_override_note", { from: fmtTemp(ov.from), to: fmtTemp(ov.to) })) }),
      h("button", { class: "mu-btn small ghost", type: "button", text: t("temp_use_preset", { value: fmtTemp(ov.from) }), onclick: function () {
        p.temperature = ov.from; S.presetOverride = null; savePreset(); rerender();
      } })
    ]);
  }
  if (Number(p.temperature) > TEMP_RISKY) {
    return h("div", { class: "ch-temp-note warn", role: "note" }, [
      h("span", { class: "ch-temp-note-text", html: icon("alert", 14) + esc(t("temp_high_note", { value: fmtTemp(p.temperature) })) }),
      h("button", { class: "mu-btn small ghost", type: "button", text: t("temp_set_recommended"), onclick: function () {
        p.temperature = 1; savePreset(); rerender();
      } })
    ]);
  }
  return null;
}
function activatePreset(id) {
  return loadPresetById(id).then(function (raw) {
    if (!raw) { if (id !== "universal") return activatePreset("universal"); raw = {}; }
    S.presetRaw = raw;
    S.preset = E.normalizePreset(raw);
    S.preset.memu = raw.memu || { id: id };
    S.presetOverride = applyPresetRecommended(id, raw, S.preset);
    S.settings.presetId = id; saveSettings();
    refreshTokens();
  });
}
var savePreset = debounce(function () {
  if (!S.preset) return;
  var id = S.settings.presetId;
  var out = E.exportPreset(S.preset);
  out.memu = Object.assign({}, S.preset.memu || {}, { id: id, modified: Date.now() });
  var builtin = S.presetIndex.find(function (p) { return p.id === id; });
  if (builtin) { out.memu.builtin = true; out.memu.edited = true; out.memu.name_ru = builtin.name_ru; out.memu.name_en = builtin.name_en; }
  Store.set("presets/" + id, out);
  if (!builtin && !S.userPresets.find(function (p) { return p.id === id; })) S.userPresets.push({ id: id, name: out.memu.name || id });
  Store.set("presetindex", S.userPresets);
  refreshTokens();
}, 500);
function presetLabel(id) {
  var b = S.presetIndex.find(function (p) { return p.id === id; });
  if (b) return presetName(b);
  var u = S.userPresets.find(function (p) { return p.id === id; });
  return u ? u.name : id;
}
function importPresetJson(json, fileName) {
  var name = String(fileName || "").replace(/\.json$/i, "").replace(/^Chat Completion[_ ]?/i, "").trim() || t("imported_preset");
  var id = E.uid("pr");
  json.memu = { id: id, name: name, imported: Date.now() };
  var normalized = E.normalizePreset(json);
  var out = E.exportPreset(normalized);
  out.memu = json.memu;
  return Store.set("presets/" + id, out).then(function () {
    S.userPresets.push({ id: id, name: name });
    Store.set("presetindex", S.userPresets);
    toast(t("preset_imported", { name: name }), "ok");
    return activatePreset(id).then(function () { if (S.drawerOpen) renderDrawer(); });
  });
}
function drawPresetPanel(body) {
  if (!S.preset) { body.appendChild(h("div", { class: "mu-empty", text: t("loading") })); return; }
  var p = S.preset;
  var id = S.settings.presetId;
  var isBuiltin = !!S.presetIndex.find(function (x) { return x.id === id; });
  var options = S.presetIndex.map(function (x) { return [x.id, presetName(x)]; }).concat(S.userPresets.map(function (x) { return [x.id, x.name]; }));
  var sel = selectEl(options, id, function (v) { activatePreset(v).then(renderDrawer); }, "ch-preset-select");
  var info = S.presetIndex.find(function (x) { return x.id === id; });
  body.appendChild(h("div", { class: "ch-preset-head" }, [
    sel,
    iconBtn("dots", "more", function (e) {
      popMenu(e.currentTarget, [
        { icon: "plus", label: t("preset_new"), onClick: function () { presetSaveAs(true); } },
        { icon: "copy", label: t("preset_save_as"), onClick: function () { presetSaveAs(false); } },
        !isBuiltin ? { icon: "edit", label: t("rename"), onClick: function () { presetRename(); } } : null,
        { icon: "upload", label: t("preset_import"), onClick: function () { pickFile(".json").then(function (f) { if (f[0]) readAs(f[0], "text").then(function (txt) { return importPresetJson(JSON.parse(txt), f[0].name); }).catch(function (e) { toast(e.message, "error"); }); }); } },
        { icon: "download", label: t("preset_export"), onClick: function () { var out = E.exportPreset(S.preset); download(safeFileName(presetLabel(id), ".json"), JSON.stringify(out, null, 2)); } },
        isBuiltin ? { icon: "refresh", label: t("preset_reset"), onClick: function () { Store.remove("presets/" + id).then(function () { activatePreset(id).then(renderDrawer); toast(t("preset_restored"), "ok"); }); } } : null,
        !isBuiltin ? { sep: true } : null,
        !isBuiltin ? { icon: "trash", label: t("delete"), danger: true, onClick: function () {
          confirmBox(t("delete_preset_q"), { danger: true, ok: t("delete") }).then(function (ok) {
            if (!ok) return;
            Store.remove("presets/" + id);
            S.userPresets = S.userPresets.filter(function (x) { return x.id !== id; });
            Store.set("presetindex", S.userPresets);
            activatePreset("universal").then(renderDrawer);
          });
        } } : null
      ]);
    })
  ]));
  if (info) body.appendChild(h("div", { class: "ch-hint pad", text: Memu.lang === "ru" ? info.summary_ru : info.summary_en }));
  if (S.presetRaw && S.presetRaw.memu && S.presetRaw.memu.edited) body.appendChild(h("div", { class: "ch-hint pad", text: t("preset_edited") }));
  function set(key, value) { p[key] = value; savePreset(); }
  var tempNoteBox = h("div", { class: "ch-temp-note-box" });
  function syncTempNote() {
    tempNoteBox.textContent = "";
    var note = temperatureNote(p, function () {
      var num = tempNoteBox.parentNode && tempNoteBox.previousElementSibling;
      if (num) num.querySelectorAll("input").forEach(function (el) { el.value = p.temperature; });
      syncTempNote();
    });
    if (note) tempNoteBox.appendChild(note);
  }
  syncTempNote();
  body.appendChild(section(t("sec_samplers"), "sliders", [
    slider("p_context", p.openai_max_context, 512, 2000000, 256, function (v) { set("openai_max_context", Math.round(v)); }, "p_context_hint"),
    slider("p_max_tokens", p.openai_max_tokens, 16, 200000, 16, function (v) { set("openai_max_tokens", Math.round(v)); }),
    slider("p_temperature", p.temperature, 0, 2, 0.01, function (v) { set("temperature", v); S.presetOverride = null; syncTempNote(); }),
    tempNoteBox,
    slider("p_top_p", p.top_p, 0, 1, 0.01, function (v) { set("top_p", v); }),
    slider("p_top_k", p.top_k, 0, 500, 1, function (v) { set("top_k", Math.round(v)); }, "p_zero_off"),
    slider("p_min_p", p.min_p, 0, 1, 0.001, function (v) { set("min_p", v); }),
    slider("p_top_a", p.top_a, 0, 1, 0.001, function (v) { set("top_a", v); }),
    slider("p_freq", p.frequency_penalty, -2, 2, 0.01, function (v) { set("frequency_penalty", v); }),
    slider("p_pres", p.presence_penalty, -2, 2, 0.01, function (v) { set("presence_penalty", v); }),
    slider("p_rep", p.repetition_penalty, 0, 3, 0.01, function (v) { set("repetition_penalty", v); }),
    field("p_seed", textInput(p.seed, function (v) { set("seed", parseInt(v, 10)); }, { type: "number" }), "p_seed_hint"),
    field("p_effort", selectEl([["auto", t("effort_auto")], ["min", t("effort_min")], ["low", t("effort_low")], ["medium", t("effort_medium")], ["high", t("effort_high")], ["xhigh", t("effort_xhigh")], ["max", t("effort_max")]], p.reasoning_effort || "auto", function (v) { set("reasoning_effort", v); })),
    toggle("p_show_thoughts", p.show_thoughts, function (v) { set("show_thoughts", v); }, "p_show_thoughts_hint"),
    toggle("p_stream", p.stream_openai !== false, function (v) { set("stream_openai", v); S.settings.gen.streaming = v; saveSettings(); })
  ], { open: true }));
  body.appendChild(section(t("sec_prompts"), "list", [promptManager()], { open: true }));
  body.appendChild(section(t("sec_formats"), "edit", [
    field("p_names", selectEl([["-1", t("names_none")], ["0", t("names_default")], ["1", t("names_completion")], ["2", t("names_content")]], String(p.names_behavior), function (v) { set("names_behavior", Number(v)); }), "p_names_hint"),
    toggle("p_squash", p.squash_system_messages, function (v) { set("squash_system_messages", v); }, "p_squash_hint"),
    field("p_postproc", selectEl([["", t("pp_none")], ["merge", t("pp_merge")], ["semi", t("pp_semi")], ["strict", t("pp_strict")], ["single", t("pp_single")]], p.custom_prompt_post_processing || "", function (v) { set("custom_prompt_post_processing", v); }), "p_postproc_hint"),
    field("p_wi_format", textInput(p.wi_format, function (v) { set("wi_format", v); }), "p_wi_format_hint"),
    field("p_scenario_format", textInput(p.scenario_format, function (v) { set("scenario_format", v); })),
    field("p_personality_format", textInput(p.personality_format, function (v) { set("personality_format", v); })),
    field("p_new_chat", textInput(p.new_chat_prompt, function (v) { set("new_chat_prompt", v); }, { multiline: true, rows: 2 })),
    field("p_new_example", textInput(p.new_example_chat_prompt, function (v) { set("new_example_chat_prompt", v); }, { multiline: true, rows: 2 })),
    field("p_send_if_empty", textInput(p.send_if_empty, function (v) { set("send_if_empty", v); }), "p_send_if_empty_hint")
  ], { open: false }));
  body.appendChild(section(t("sec_utility"), "zap", [
    field("p_prefill", textInput(p.assistant_prefill, function (v) { set("assistant_prefill", v); }, { multiline: true, rows: 3 }), "p_prefill_hint"),
    field("p_impersonation_prefill", textInput(p.assistant_impersonation, function (v) { set("assistant_impersonation", v); }, { multiline: true, rows: 2 })),
    field("p_impersonation", textInput(p.impersonation_prompt, function (v) { set("impersonation_prompt", v); }, { multiline: true, rows: 3 })),
    field("p_continue_nudge", textInput(p.continue_nudge_prompt, function (v) { set("continue_nudge_prompt", v); }, { multiline: true, rows: 3 })),
    toggle("p_continue_prefill", p.continue_prefill, function (v) { set("continue_prefill", v); }, "p_continue_prefill_hint"),
    field("p_continue_postfix", selectEl([["", t("postfix_none")], [" ", t("postfix_space")], ["\n", t("postfix_newline")], ["\n\n", t("postfix_double")]], p.continue_postfix, function (v) { set("continue_postfix", v); })),
    field("p_group_nudge", textInput(p.group_nudge_prompt, function (v) { set("group_nudge_prompt", v); }, { multiline: true, rows: 2 }))
  ], { open: false }));
}
function presetSaveAs(blank) {
  promptBox(blank ? t("preset_new") : t("preset_save_as"), blank ? "" : presetLabel(S.settings.presetId) + " " + t("copy_suffix")).then(function (name) {
    if (!name || !name.trim()) return;
    var id = E.uid("pr");
    var out = E.exportPreset(blank ? E.normalizePreset(null) : S.preset);
    out.memu = { id: id, name: name.trim(), created: Date.now() };
    Store.set("presets/" + id, out).then(function () {
      S.userPresets.push({ id: id, name: name.trim() });
      Store.set("presetindex", S.userPresets);
      activatePreset(id).then(renderDrawer);
    });
  });
}
function presetRename() {
  var id = S.settings.presetId;
  var row = S.userPresets.find(function (x) { return x.id === id; });
  if (!row) return;
  promptBox(t("rename"), row.name).then(function (name) {
    if (!name || !name.trim()) return;
    row.name = name.trim();
    S.preset.memu = Object.assign({}, S.preset.memu || {}, { name: row.name });
    Store.set("presetindex", S.userPresets);
    savePreset(); savePreset.flush();
    renderDrawer();
  });
}
/* Round 33: pointer reordering for the prompt list. A native HTML5 drag runs the OS drag loop on Windows,
   where the mouse wheel never reaches the page, so a long list could not be scrolled while dragging. Here:
   hold a row (5 px threshold), the wheel scrolls as usual, near the top/bottom edge of the list (or past it)
   it scrolls by itself — faster closer to / farther past the edge; a line shows «before»/«after», Esc cancels.
   onDrop(from, to): «to» is the insertion index in the full order (before the row at «to»). */
function pmDragSort(list, onDrop) {
  var d = null;
  // Every scroll container from the list outwards (the list itself, the drawer, the page).
  function scrollers() {
    var out = [];
    for (var n = list; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      var st = getComputedStyle(n);
      if (/(auto|scroll)/.test(st.overflowY) && n.scrollHeight > n.clientHeight + 2) out.push(n);
    }
    out.push(document.scrollingElement || document.documentElement);
    return out;
  }
  // The visible part of the list: its box clipped by the containers around it and the window.
  function visibleRect(chain) {
    var r = list.getBoundingClientRect(), top = Math.max(0, r.top), bottom = Math.min(window.innerHeight, r.bottom);
    chain.forEach(function (n) {
      if (n === document.scrollingElement || n === document.documentElement || n === list) return;
      var b = n.getBoundingClientRect(); top = Math.max(top, b.top); bottom = Math.min(bottom, b.bottom);
    });
    return { top: top, bottom: Math.max(top + 1, bottom), height: Math.max(1, bottom - top) };
  }
  function mark(next) {
    if (d.over && (!next || d.over.el !== next.el || d.over.after !== next.after)) d.over.el.classList.remove("over", "over-after");
    d.over = next;
    if (next) next.el.classList.add(next.after ? "over-after" : "over");
  }
  function retarget() {
    if (!d || !d.on) return;
    var under = document.elementFromPoint(Math.min(Math.max(d.x, list.getBoundingClientRect().left + 8), list.getBoundingClientRect().right - 8), d.y);
    var row = under && under.closest ? under.closest(".ch-pm-row[data-index]") : null;
    if (!row || !list.contains(row) || row === d.row) { mark(null); return; }
    var r = row.getBoundingClientRect();
    mark({ el: row, after: d.y > r.top + r.height / 2 });
  }
  function speed(y, r) {
    var edge = Math.min(48, r.height / 4), dist = 0;
    if (y < r.top + edge) dist = y - (r.top + edge); else if (y > r.bottom - edge) dist = y - (r.bottom - edge);
    if (!dist) return 0;
    var k = Math.min(1, Math.abs(dist) / (edge + 24));
    return (dist < 0 ? -1 : 1) * Math.max(2, Math.round(22 * k * k + (Math.abs(dist) > edge ? 5 : 0)));
  }
  function tick() {
    if (!d || !d.on) return;
    // Time-based: the same speed in px/s on a slow frame (a busy window renders fewer frames).
    var now = performance.now(), dt = Math.min(200, Math.max(1, now - (d.at || now - 16.7)));
    d.at = now;
    var v = Math.round(speed(d.y, visibleRect(d.box)) * dt / 16.7);
    // A row grabbed right at an edge must not start scrolling at once: arm after the pointer has been
    // in the calm middle once, or has moved 24 px from where the drag began.
    if (!d.armed) { if (!v || Math.abs(d.y - d.sy) > 24) d.armed = true; else v = 0; }
    // Scroll the innermost container that can still move that way (the list first, then the drawer…).
    for (var i = 0; v && i < d.box.length; i++) {
      var box = d.box[i], before = box.scrollTop;
      box.scrollTop += v;
      if (box.scrollTop !== before) { retarget(); break; }
    }
    d.raf = requestAnimationFrame(tick);
  }
  function stop(drop) {
    var cur = d; d = null;
    window.removeEventListener("pointermove", move, true);
    window.removeEventListener("pointerup", up, true);
    window.removeEventListener("pointercancel", cancel, true);
    window.removeEventListener("keydown", key, true);
    if (!cur || !cur.on) return;
    cancelAnimationFrame(cur.raf);
    document.removeEventListener("scroll", retarget, true);
    cur.row.classList.remove("dragging");
    document.documentElement.classList.remove("ch-pm-dragging");
    if (cur.ghost) cur.ghost.remove();
    var over = cur.over;
    if (over) over.el.classList.remove("over", "over-after");
    cur.swallow = Date.now();
    list._pmSwallow = cur.swallow;
    if (drop && over) {
      var to = Number(over.el.dataset.index) + (over.after ? 1 : 0);
      onDrop(Number(cur.row.dataset.index), to);
    }
  }
  function move(e) {
    if (!d || e.pointerId !== d.id) return;
    d.x = e.clientX; d.y = e.clientY;
    if (!d.on) {
      if (Math.abs(d.x - d.sx) + Math.abs(d.y - d.sy) < 5) return;
      d.on = true; d.box = scrollers();
      d.row.classList.add("dragging");
      document.documentElement.classList.add("ch-pm-dragging");
      d.ghost = h("div", { class: "ch-pm-ghost", "aria-hidden": "true", text: (d.row.querySelector(".ch-pm-name") || d.row).textContent.trim().slice(0, 80) });
      document.body.appendChild(d.ghost);
      try { window.getSelection().removeAllRanges(); } catch (err) { /* none */ }
      document.addEventListener("scroll", retarget, true);
      d.raf = requestAnimationFrame(tick);
    }
    e.preventDefault();
    if (d.ghost) d.ghost.style.transform = "translate(" + Math.round(d.x + 14) + "px," + Math.round(d.y + 10) + "px)";
    retarget();
  }
  function up(e) { if (d && e.pointerId === d.id) stop(true); }
  function cancel() { if (d) stop(false); }
  function key(e) { if (d && d.on && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stop(false); } }
  list.addEventListener("pointerdown", function (e) {
    if (d || e.button !== 0 || e.pointerType === "touch") return;
    var row = e.target.closest ? e.target.closest(".ch-pm-row[data-index]") : null;
    if (!row || !list.contains(row) || e.target.closest("button, label, input, textarea, select")) return;
    d = { row: row, id: e.pointerId, sx: e.clientX, sy: e.clientY, x: e.clientX, y: e.clientY, on: false, over: null, raf: 0, box: null, ghost: null };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", cancel, true);
    window.addEventListener("keydown", key, true);
  });
  list.addEventListener("click", function (e) { if (list._pmSwallow && Date.now() - list._pmSwallow < 400) { e.preventDefault(); e.stopPropagation(); } list._pmSwallow = 0; }, true);
}
/* prompt manager: order list with toggles, drag-and-drop, edit */
var MARKER_HINTS = { chatHistory: "pm_chat_history", dialogueExamples: "pm_examples", worldInfoBefore: "pm_wi_before", worldInfoAfter: "pm_wi_after",
  charDescription: "pm_char_desc", charPersonality: "pm_char_pers", scenario: "pm_scenario", personaDescription: "pm_persona" };
function promptManager() {
  var p = S.preset;
  var wrap = h("div", { class: "ch-pm" });
  var filter = h("input", { class: "mu-input ch-pm-filter", type: "search", placeholder: t("pm_filter") });
  var counts = h("span", { class: "mu-muted ch-small" });
  wrap.appendChild(h("div", { class: "mu-row" }, [filter,
    h("button", { class: "mu-btn small", type: "button", html: icon("plus", 14) + esc(t("pm_add")), onclick: function () { addPrompt(); } }), counts]));
  var list = h("div", { class: "ch-pm-list" });
  wrap.appendChild(list);
  pmDragSort(list, function (from, to) {
    var order = E.presetOrder(p);
    if (from === to || from < 0 || from >= order.length) return;
    var moved = order.splice(from, 1)[0];
    order.splice(to > from ? to - 1 : to, 0, moved);
    savePreset(); draw();
  });
  var tokens = {};
  try {
    if (S.lastBuilt) S.lastBuilt.breakdown.forEach(function (b) { tokens[b.identifier] = b.tokens; });
  } catch (e) { /* none */ }
  function draw() {
    list.innerHTML = "";
    var order = E.presetOrder(p);
    var q = filter.value.trim().toLowerCase();
    var enabled = 0;
    order.forEach(function (entry, index) {
      var prompt = p.prompts.find(function (x) { return x.identifier === entry.identifier; });
      if (!prompt) return;
      if (entry.enabled) enabled++;
      if (q && (String(prompt.name) + " " + String(prompt.content || "")).toLowerCase().indexOf(q) < 0) return;
      var isMarker = !!prompt.marker;
      var abs = Number(prompt.injection_position) === 1;
      var row = h("div", { class: "ch-pm-row" + (entry.enabled ? "" : " off") + (isMarker ? " marker" : ""), "data-index": index }, [
        h("span", { class: "ch-pm-grip", html: icon("grip", 14) }),
        h("span", { class: "ch-pm-role " + (prompt.role || "system"), title: prompt.role || "", text: isMarker ? "◆" : String(prompt.role || "system").charAt(0).toUpperCase() }),
        h("span", { class: "ch-pm-name", title: prompt.name, text: prompt.name || prompt.identifier }),
        abs ? h("span", { class: "ch-tag", title: t("pm_in_chat"), text: "@" + (prompt.injection_depth === undefined ? 4 : prompt.injection_depth) }) : null,
        tokens[prompt.identifier] ? h("span", { class: "ch-pm-tok", text: fmtNum(tokens[prompt.identifier]) }) : null,
        h("button", { class: "ch-mini", type: "button", title: t("edit"), html: icon(isMarker ? "info" : "edit", 13), onclick: function (e) { e.stopPropagation(); editPrompt(prompt, entry); } }),
        h("label", { class: "ch-pm-switch", title: t("enabled") }, [(function () {
          var c = h("input", { type: "checkbox" }); c.checked = !!entry.enabled;
          c.addEventListener("change", function () { entry.enabled = c.checked; savePreset(); draw(); });
          return c;
        })(), h("span", { class: "ch-switch small" })])
      ]);
      // Round 33: reordered with the pointer (pmDragSort below) — the wheel and edge auto-scroll work while dragging.
      row.addEventListener("dblclick", function () { editPrompt(prompt, entry); });
      list.appendChild(row);
    });
    var inOrder = {};
    order.forEach(function (o) { inOrder[o.identifier] = true; });
    var unused = p.prompts.filter(function (x) { return !inOrder[x.identifier]; });
    if (unused.length) {
      list.appendChild(h("div", { class: "ch-pm-sep", text: t("pm_unused", { count: unused.length }) }));
      unused.forEach(function (prompt) {
        if (q && String(prompt.name).toLowerCase().indexOf(q) < 0) return;
        list.appendChild(h("div", { class: "ch-pm-row off ghost" }, [
          h("span", { class: "ch-pm-name", text: prompt.name || prompt.identifier }),
          h("button", { class: "ch-mini", type: "button", title: t("pm_insert"), html: icon("plus", 13), onclick: function () { order.push({ identifier: prompt.identifier, enabled: true }); savePreset(); draw(); } }),
          h("button", { class: "ch-mini", type: "button", title: t("edit"), html: icon("edit", 13), onclick: function () { editPrompt(prompt, null); } }),
          h("button", { class: "ch-mini", type: "button", title: t("delete"), html: icon("trash", 13), onclick: function () { p.prompts = p.prompts.filter(function (x) { return x !== prompt; }); savePreset(); draw(); } })
        ]));
      });
    }
    counts.textContent = t("pm_counts", { on: enabled, total: order.length });
  }
  filter.addEventListener("input", draw);
  draw();
  function addPrompt() {
    var prompt = { identifier: E.uid("prompt-"), name: t("pm_new_name"), role: "system", content: "", system_prompt: false, marker: false, injection_position: 0, injection_depth: 4, injection_order: 100, injection_trigger: [], forbid_overrides: false };
    p.prompts.push(prompt);
    E.presetOrder(p).push({ identifier: prompt.identifier, enabled: true });
    savePreset();
    editPrompt(prompt, E.presetOrder(p)[E.presetOrder(p).length - 1], draw);
    draw();
  }
  function editPrompt(prompt, entry, after) {
    var draft = E.clone(prompt);
    var isMarker = !!prompt.marker;
    var content = textInput(draft.content || "", function (v) { draft.content = v; tok.textContent = "≈" + fmtNum(E.estimateTokens(v)) + " " + t("tok"); }, { multiline: true, rows: 14, live: true, mono: true });
    var tok = h("span", { class: "mu-muted ch-small", text: "≈" + fmtNum(E.estimateTokens(draft.content || "")) + " " + t("tok") });
    var triggers = ["normal", "continue", "impersonate", "swipe", "regenerate", "quiet"];
    draft.injection_trigger = Array.isArray(draft.injection_trigger) ? draft.injection_trigger : [];
    var trigWrap = h("div", { class: "ch-chips" }, triggers.map(function (tr) {
      var b = h("button", { class: "ch-chip" + (draft.injection_trigger.indexOf(tr) >= 0 ? " on" : ""), type: "button", text: t("trig_" + tr) });
      b.addEventListener("click", function () {
        var i = draft.injection_trigger.indexOf(tr);
        if (i >= 0) draft.injection_trigger.splice(i, 1); else draft.injection_trigger.push(tr);
        b.classList.toggle("on");
      });
      return b;
    }));
    var depthField = field("depth", textInput(draft.injection_depth === undefined ? 4 : draft.injection_depth, function (v) { draft.injection_depth = clamp(parseInt(v, 10) || 0, 0, 9999); }, { type: "number" }));
    var orderField = field("pm_order", textInput(draft.injection_order === undefined ? 100 : draft.injection_order, function (v) { draft.injection_order = parseInt(v, 10) || 100; }, { type: "number" }));
    function syncPos() { depthField.hidden = orderField.hidden = Number(draft.injection_position) !== 1; }
    var posSel = selectEl([["0", t("pm_relative")], ["1", t("pm_in_chat")]], String(draft.injection_position || 0), function (v) { draft.injection_position = Number(v); syncPos(); });
    syncPos();
    openModal({ title: t("pm_edit"), icon: "edit", full: !isMarker, wide: isMarker, content: h("div", { class: "ch-ed-col" }, [
      isMarker ? h("div", { class: "mu-note", text: t(MARKER_HINTS[prompt.identifier] || "pm_marker_hint") }) : null,
      h("div", { class: "ch-two" }, [field("f_name", textInput(draft.name, function (v) { draft.name = v; }, { live: true })),
        field("role", selectEl([["system", t("role_system")], ["user", t("role_user")], ["assistant", t("role_assistant")]], draft.role || "system", function (v) { draft.role = v; }))]),
      h("div", { class: "ch-two" }, [field("pm_position", posSel), depthField]),
      orderField,
      !isMarker ? h("div", { class: "ch-field" }, [h("div", { class: "ch-label-row" }, [h("span", { class: "ch-label", text: t("pm_content") }), tok]), content, h("span", { class: "ch-hint", text: t("pm_macros_hint") })]) : null,
      h("div", { class: "ch-field" }, [h("span", { class: "ch-label", text: t("pm_triggers") }), trigWrap, h("span", { class: "ch-hint", text: t("pm_triggers_hint") })]),
      prompt.identifier === "main" || prompt.identifier === "jailbreak" ? toggle("pm_forbid", draft.forbid_overrides, function (v) { draft.forbid_overrides = v; }, "pm_forbid_hint") : null,
      h("div", { class: "ch-hint", text: t("pm_identifier", { id: prompt.identifier }) })
    ]), buttons: [{ label: t("cancel"), kind: "ghost" }, { label: t("save"), kind: "primary", onClick: function () {
      Object.assign(prompt, draft); savePreset(); draw(); if (after) after();
    } }] });
  }
  return wrap;
}

/* ====================================================================== connection */
function drawConnectionPanel(body) {
  var gen = S.settings.gen;
  function set(key, v) { gen[key] = v; saveSettings(); refreshTokens(); }
  var textModels = S.models.filter(function (m) { return !m.image && !m.video && !m.audio; });
  var modelSel = selectEl([["", t("choose_model")]].concat(textModels.map(function (m) { return [m.id, (m.name || m.id) + (m.context ? " · " + fmtNum(m.context) : "")]; })), S.settings.model, function (v) { S.settings.model = v; saveSettings(); refreshTokens(); refreshComposerBar(); });
  body.appendChild(section(t("sec_model"), "zap", [
    field("model", modelSel, "model_hint"),
    h("button", { class: "mu-btn small", type: "button", html: icon("refresh", 14) + esc(t("reload_models")), onclick: function () { loadModels().then(renderDrawer); } }),
    toggle("g_clamp", gen.clampContext, function (v) { set("clampContext", v); }, "g_clamp_hint"),
    field("g_context_override", textInput(gen.contextOverride || "", function (v) { set("contextOverride", Math.max(0, parseInt(v, 10) || 0)); }, { type: "number", placeholder: t("from_preset") }), "g_context_override_hint")
  ]));
  body.appendChild(assistSettingsSection());
  body.appendChild(section(t("sec_generation"), "send", [
    toggle("g_streaming", gen.streaming, function (v) { set("streaming", v); }),
    field("g_thinking", selectEl([["auto", t("thinking_auto")], ["on", t("on")], ["off", t("off")]], gen.thinking, function (v) { set("thinking", v); }), "g_thinking_hint"),
    toggle("g_send_prefill", gen.sendPrefill, function (v) { set("sendPrefill", v); }, "g_send_prefill_hint"),
    toggle("g_show_prefill", gen.showPrefill, function (v) { set("showPrefill", v); }),
    toggle("g_extended", gen.extendedSamplers, function (v) { set("extendedSamplers", v); }, "g_extended_hint"),
    toggle("g_caching", gen.promptCaching, function (v) { set("promptCaching", v); }, "g_caching_hint"),
    toggle("g_name_field", gen.nameField, function (v) { set("nameField", v); }, "g_name_field_hint"),
    field("g_postproc", selectEl([["preset", t("pp_preset")], ["none", t("pp_none")], ["merge", t("pp_merge")], ["semi", t("pp_semi")], ["strict", t("pp_strict")], ["single", t("pp_single")]], gen.postProcessing, function (v) { set("postProcessing", v); }), "g_postproc_hint"),
    field("g_stop", textInput((gen.stop || []).join("\n"), function (v) { set("stop", v.split("\n").map(function (x) { return x.replace(/\\n/g, "\n"); }).filter(Boolean).slice(0, 16)); }, { multiline: true, rows: 2 }), "g_stop_hint"),
    toggle("g_auto_continue", gen.autoContinue, function (v) { set("autoContinue", v); }, "g_auto_continue_hint"),
    toggle("g_keep_swipes", gen.keepSwipes, function (v) { set("keepSwipes", v); })
  ]));
  body.appendChild(section(t("sec_card"), "user", [
    toggle("g_prefer_char_prompt", gen.preferCharPrompt, function (v) { set("preferCharPrompt", v); }, "g_prefer_char_prompt_hint"),
    toggle("g_prefer_char_jb", gen.preferCharJailbreak, function (v) { set("preferCharJailbreak", v); }),
    toggle("g_pin_examples", gen.pinExamples, function (v) { set("pinExamples", v); }, "g_pin_examples_hint"),
    toggle("g_inline_images", gen.inlineImages, function (v) { set("inlineImages", v); }, "g_inline_images_hint"),
    toggle("g_send_reasoning", gen.sendReasoning, function (v) { set("sendReasoning", v); })
  ], { open: false }));
  body.appendChild(section(t("sec_reasoning"), "brain", [
    toggle("g_auto_parse", gen.autoParseReasoning, function (v) { set("autoParseReasoning", v); }, "g_auto_parse_hint"),
    h("div", { class: "ch-two" }, [field("g_prefix", textInput(gen.reasoningPrefix, function (v) { set("reasoningPrefix", v || "<think>"); })), field("g_suffix", textInput(gen.reasoningSuffix, function (v) { set("reasoningSuffix", v || "</think>"); }))])
  ], { open: false }));
}
function loadModels() {
  return Memu.models().then(function (rows) {
    S.models = Array.isArray(rows) ? rows : [];
    S.modelsLoaded = true;
    // r33: the list holds only models enabled in MemuAI Proxy. A saved model that is no longer there (switched off,
    // hidden, provider removed) is replaced by MemuAI's default text model, and the person is told once.
    if (!S.settings.model || modelMissing(S.settings.model)) {
      var gone = S.settings.model;
      var next = fallbackModel();
      if (next) {
        S.settings.model = next; saveSettings();
        if (gone) toast(t("model_gone", { old: shortModel(gone), model: modelTitle(modelRow(next), next) }), "warn");
      }
    }
    refreshComposerBar();
  }).catch(function (e) {
    // r30: the host's own text already says «the model list is unavailable» (and only in Russian): it was shown twice,
    // «…недоступен: Список моделей недоступен». A model-list failure gets one clear sentence in the UI language.
    var msg = String(e && e.message || "");
    toast(!msg || /модел|model/i.test(msg) ? t("models_unavailable") : t("models_failed", { message: msg }), "error");
  });
}

/* ====================================================================== World Info */
function loadWorld(id) {
  if (S.worlds[id]) return Promise.resolve(S.worlds[id]);
  return Store.get("worlds/" + id, null).then(function (w) { if (w) { w.id = id; S.worlds[id] = w; } return w; });
}
function exportWorldMenu(anchor, world) {
  popMenu(anchor, [
    { icon: "download", label: t("wi_export_st"), onClick: function () { download(safeFileName(world.name, ".json"), JSON.stringify(E.exportWorld(world), null, 2)); } },
    { icon: "download", label: t("wi_export_cb"), onClick: function () { download(safeFileName(world.name, ".character_book.json"), JSON.stringify(E.toCharacterBook(world), null, 2)); } },
    { icon: "download", label: t("wi_export_agnai"), onClick: function () { download(safeFileName(world.name, ".agnai.json"), JSON.stringify(E.toAgnaiMemory(world), null, 2)); } }
  ]);
}
function openLorebookHelp() {
  openModal({ title: t("wi_help"), icon: "book", wide: true, content: function (body) {
    body.classList.add("ch-help");
    ["wih_what", "wih_keys", "wih_where", "wih_timing", "wih_budget", "wih_formats"].forEach(function (k) {
      body.appendChild(h("h4", { text: t(k + "_t") }));
      body.appendChild(h("p", { text: t(k) }));
    });
  }, buttons: [{ label: t("ok"), kind: "primary" }] });
}
function importWorldJson(json, fileName) {
  var name = (json.name || String(fileName || "").replace(/\.json$/i, "") || t("lorebook")).trim();
  var world = E.importWorld(json, name).world;
  world.id = E.uid("w"); world.name = world.name || name;
  saveWorld(world);
  toast(t("lorebook_imported", { name: name, count: Object.keys(world.entries).length }), "ok");
  if (S.drawerOpen) renderDrawer();
  return Promise.resolve(world);
}
function drawWorldsPanel(body) {
  var wi = S.settings.wi;
  function set(key, v) { wi[key] = v; saveSettings(); refreshTokens(); }
  body.appendChild(h("div", { class: "mu-row pad" }, [
    h("button", { class: "mu-btn small primary", type: "button", html: icon("plus", 14) + esc(t("new_lorebook")), onclick: function () {
      promptBox(t("new_lorebook"), "").then(function (name) {
        if (!name || !name.trim()) return;
        var world = { id: E.uid("w"), name: name.trim(), entries: {} };
        saveWorld(world); renderDrawer(); openWorldEditor(world.id);
      });
    } }),
    h("button", { class: "mu-btn small", type: "button", html: icon("wand", 14) + esc(t("ai_book_btn")), title: t("ai_book_btn_title"), onclick: function () { openAiCreator("lorebook"); } }),
    h("button", { class: "mu-btn small", type: "button", html: icon("upload", 14) + esc(t("import")), title: t("import_lorebook_title"), onclick: function () {
      openImportDialog({ mode: "lorebook" });
    } }),
    h("span", { class: "mu-grow" }),
    iconBtn("help", "wi_help", function () { openLorebookHelp(); })
  ]));
  var list = h("div", { class: "ch-worlds" });
  if (!S.worldIndex.length) list.appendChild(h("div", { class: "mu-empty", text: t("no_lorebooks") }));
  S.worldIndex.forEach(function (w) {
    var isGlobal = (wi.global || []).indexOf(w.id) >= 0;
    var linked = S.char && (S.char.worldId === w.id);
    var chatLinked = S.chat && S.chat.meta && S.chat.meta.worldId === w.id;
    list.appendChild(h("div", { class: "ch-world" }, [
      h("button", { class: "ch-world-main", type: "button", onclick: function () { openWorldEditor(w.id); } }, [
        h("span", { class: "ch-world-ic", html: icon("book", 16) }),
        h("span", { class: "ch-world-text" }, [h("b", { text: w.name }), h("span", { class: "mu-muted ch-small", text: t("entries_n", { count: w.count || 0 }) })]),
        linked ? h("span", { class: "ch-tag", text: t("tag_char") }) : null,
        chatLinked ? h("span", { class: "ch-tag", text: t("tag_chat") }) : null
      ]),
      h("label", { class: "ch-pm-switch", title: t("global_lorebook") }, [(function () {
        var c = h("input", { type: "checkbox" }); c.checked = isGlobal;
        c.addEventListener("change", function () {
          var g = wi.global || (wi.global = []);
          if (c.checked) { if (g.indexOf(w.id) < 0) g.push(w.id); } else wi.global = g.filter(function (x) { return x !== w.id; });
          saveSettings(); refreshTokens();
        });
        return c;
      })(), h("span", { class: "ch-switch small" })])
    ]));
  });
  body.appendChild(h("div", { class: "ch-hint pad", text: t("global_lorebook_hint") }));
  body.appendChild(list);
  body.appendChild(section(t("sec_wi_settings"), "settings", [
    slider("wi_depth", wi.depth, 0, 100, 1, function (v) { set("depth", Math.round(v)); }, "wi_depth_hint"),
    slider("wi_budget", wi.budget, 1, 100, 1, function (v) { set("budget", Math.round(v)); }, "wi_budget_hint"),
    field("wi_budget_cap", textInput(wi.budgetCap, function (v) { set("budgetCap", Math.max(0, parseInt(v, 10) || 0)); }, { type: "number" }), "wi_budget_cap_hint"),
    slider("wi_min_act", wi.minActivations, 0, 50, 1, function (v) { set("minActivations", Math.round(v)); }),
    slider("wi_max_depth", wi.minActivationsDepthMax, 0, 100, 1, function (v) { set("minActivationsDepthMax", Math.round(v)); }),
    slider("wi_max_steps", wi.maxRecursionSteps, 0, 10, 1, function (v) { set("maxRecursionSteps", Math.round(v)); }, "wi_max_steps_hint"),
    field("wi_strategy", selectEl([["0", t("wi_even")], ["1", t("wi_char_first")], ["2", t("wi_global_first")]], String(wi.strategy), function (v) { set("strategy", Number(v)); })),
    toggle("wi_recursive", wi.recursive, function (v) { set("recursive", v); }),
    toggle("wi_case", wi.caseSensitive, function (v) { set("caseSensitive", v); }),
    toggle("wi_whole", wi.matchWholeWords, function (v) { set("matchWholeWords", v); }),
    toggle("wi_group_scoring", wi.useGroupScoring, function (v) { set("useGroupScoring", v); }),
    toggle("wi_include_names", S.settings.gen.wiIncludeNames, function (v) { S.settings.gen.wiIncludeNames = v; saveSettings(); }),
    toggle("wi_overflow", wi.overflowAlert, function (v) { set("overflowAlert", v); })
  ], { open: false }));
}
var POSITIONS = [["0", "pos_before"], ["1", "pos_after"], ["2", "pos_an_top"], ["3", "pos_an_bottom"], ["4", "pos_depth"], ["5", "pos_em_top"], ["6", "pos_em_bottom"], ["7", "pos_outlet"]];
function openWorldEditor(id) {
  loadWorld(id).then(function (world) {
    if (!world) return;
    var q = "";
    var expanded = {};
    var draw = function () {};
    // r37: the co-writer for this lorebook (the open character, if any, is its context).
    var assistHost = { kind: "lorebook", box: null, fieldKeys: ["wi_content"], label: function () { return t("wi_content"); },
      card: function () { return S.char ? S.char.data : null; }, world: function () { return world; },
      reveal: function () { return null; },
      revealEntry: function (title) {
        var e = Object.keys(world.entries).map(function (k) { return world.entries[k]; }).find(function (x) { return String(x.comment).trim().toLowerCase() === String(title).trim().toLowerCase(); });
        if (!e) return null;
        expanded[e.uid] = true; draw();
        return modal.body.querySelector('[data-assist-entry="' + e.uid + '"]');
      },
      addEntryTo: function () { return world; },
      onEntryAdded: function () { draw(); } };
    var modal = openModal({ title: world.name, icon: "book", full: true, headExtra: h("div", { class: "mu-row" }, [
      h("button", { class: "mu-btn small ch-as-open", type: "button", title: t("as_panel_book_title"), html: icon("sparkle", 14) + "<span>" + esc(t("as_panel")) + "</span>", onclick: function () { assistPanel(assistHost); } }),
      iconBtn("edit", "rename", function () { promptBox(t("rename"), world.name).then(function (n) { if (n && n.trim()) { world.name = n.trim(); saveWorld(world); modal.box.querySelector(".ch-modal-title").textContent = world.name; } }); }),
      iconBtn("download", "export", function (ev) { exportWorldMenu(ev && ev.currentTarget, world); }),
      S.char ? iconBtn("link", "link_to_char", function () { S.char.worldId = S.char.worldId === world.id ? "" : world.id; saveCharacter(S.char); toast(S.char.worldId ? t("linked") : t("unlinked"), "ok"); }) : null,
      iconBtn("trash", "delete", function () {
        confirmBox(t("delete_lorebook_q", { name: world.name }), { danger: true, ok: t("delete") }).then(function (ok) {
          if (!ok) return;
          Store.remove("worlds/" + world.id);
          delete S.worlds[world.id];
          S.worldIndex = S.worldIndex.filter(function (w) { return w.id !== world.id; });
          Store.set("worldindex", S.worldIndex);
          S.settings.wi.global = (S.settings.wi.global || []).filter(function (x) { return x !== world.id; }); saveSettings();
          modal.close(); renderDrawer();
        });
      })
    ]), content: function (body) {
      var search = h("input", { class: "mu-input", type: "search", placeholder: t("wi_search") });
      var sort = selectEl([["order", t("wi_sort_order")], ["name", t("wi_sort_name")], ["uid", t("wi_sort_uid")]], "order", function () { draw(); });
      body.appendChild(h("div", { class: "mu-row" }, [
        h("button", { class: "mu-btn small primary", type: "button", html: icon("plus", 14) + esc(t("new_entry")), onclick: function () {
          var uids = Object.keys(world.entries).map(Number).filter(Number.isFinite);
          var uid = uids.length ? Math.max.apply(null, uids) + 1 : 0;
          world.entries[uid] = E.normalizeEntry({ uid: uid, comment: t("new_entry") }, uid);
          expanded[uid] = true; saveWorld(world); draw();
        } }),
        h("button", { class: "mu-btn small", type: "button", title: t("ai_book_add_btn_title"), html: icon("wand", 14) + esc(t("ai_book_add_btn")), onclick: function () {
          openAiCreator("lorebook", { world: world, onDone: function () { draw(); } });
        } }), search, sort]));
      var list = h("div", { class: "ch-entries" });
      body.appendChild(list);
      search.addEventListener("input", function () { q = search.value.toLowerCase(); draw(); });
      draw = function () {
        list.innerHTML = "";
        var entries = Object.keys(world.entries).map(function (k) { return world.entries[k]; });
        entries.sort(function (a, b) {
          if (sort.value === "name") return String(a.comment).localeCompare(String(b.comment));
          if (sort.value === "uid") return Number(a.uid) - Number(b.uid);
          return Number(b.order) - Number(a.order);
        });
        if (!entries.length) list.appendChild(h("div", { class: "mu-empty", text: t("no_entries") }));
        entries.forEach(function (e) {
          if (q && (String(e.comment) + " " + (e.key || []).join(" ") + " " + String(e.content)).toLowerCase().indexOf(q) < 0) return;
          list.appendChild(entryCard(world, e, expanded, draw, assistHost));
        });
      };
      draw();
    } });
    assistHost.box = modal.box;
  });
}
function entryCard(world, e, expanded, redraw, assistHost) {
  function save() { saveWorld(world); }
  var open = !!expanded[e.uid];
  var status = e.constant ? "const" : (e.key || []).length ? "key" : "none";
  var card = h("div", { class: "ch-entry" + (e.disable ? " off" : "") + (open ? " open" : "") });
  var head = h("div", { class: "ch-entry-head" }, [
    h("button", { class: "ch-mini", type: "button", html: icon(open ? "down" : "right", 14), onclick: function () { expanded[e.uid] = !open; redraw(); } }),
    h("span", { class: "ch-entry-dot " + status, title: t("wi_status_" + status) }),
    (function () { var inp = textInput(e.comment, function (v) { e.comment = v; save(); }, { placeholder: t("wi_title") }); inp.classList.add("ch-entry-title"); return inp; })(),
    h("span", { class: "ch-entry-keys", text: (e.key || []).slice(0, 4).join(", ") }),
    h("span", { class: "ch-tag", text: t(POSITIONS[Number(e.position)] ? POSITIONS[Number(e.position)][1] : "pos_before") + (Number(e.position) === 4 ? " " + e.depth : "") }),
    h("span", { class: "mu-muted ch-small", text: "≈" + fmtNum(E.estimateTokens(e.content)) }),
    h("label", { class: "ch-pm-switch", title: t("enabled") }, [(function () {
      var c = h("input", { type: "checkbox" }); c.checked = !e.disable;
      c.addEventListener("change", function () { e.disable = !c.checked; save(); card.classList.toggle("off", e.disable); });
      return c;
    })(), h("span", { class: "ch-switch small" })]),
    h("button", { class: "ch-mini", type: "button", title: t("more"), html: icon("dots", 14), onclick: function (ev) {
      popMenu(ev.currentTarget, [
        { icon: "copy", label: t("duplicate"), onClick: function () {
          var uids = Object.keys(world.entries).map(Number);
          var uid = Math.max.apply(null, uids) + 1;
          world.entries[uid] = Object.assign(E.clone(e), { uid: uid, comment: e.comment + " " + t("copy_suffix") }); save(); redraw();
        } },
        { icon: "trash", label: t("delete"), danger: true, onClick: function () { delete world.entries[e.uid]; save(); redraw(); } }
      ]);
    } })
  ]);
  card.appendChild(head);
  if (!open) return card;
  function keysInput(key) {
    return textInput((e[key] || []).join(", "), function (v) { e[key] = v.split(",").map(function (x) { return x.trim(); }).filter(Boolean); save(); }, { placeholder: t("wi_keys_ph") });
  }
  function tri(key, labelKey) {
    return field(labelKey, selectEl([["", t("use_global")], ["true", t("yes")], ["false", t("no")]], e[key] === null || e[key] === undefined ? "" : String(e[key]), function (v) { e[key] = v === "" ? null : v === "true"; save(); }));
  }
  function numIn(key, labelKey, allowNull) {
    return field(labelKey, textInput(e[key] === null || e[key] === undefined ? "" : e[key], function (v) { e[key] = v === "" && allowNull ? null : (Number(v) || 0); save(); }, { type: "number", placeholder: allowNull ? t("use_global") : "" }));
  }
  var posField = field("position", selectEl(POSITIONS.map(function (p) { return [p[0], t(p[1])]; }), String(e.position), function (v) { e.position = Number(v); save(); redraw(); }));
  var bodyEl = h("div", { class: "ch-entry-body" }, [
    (function () {
      // r37: ✨ next to the keys suggests keywords as chips (pick the ones to add).
      var keys = keysInput("key");
      var wrap = h("div", { class: "ch-field" }, [h("div", { class: "ch-label-row" }, [h("span", { class: "ch-label", text: t("wi_keys") }),
        assistEnabled() ? h("button", { class: "ch-as-btn", type: "button", title: t("as_keys_title"), "aria-label": t("as_keys_title"), html: icon("sparkle", 14), onclick: function () { assistSuggestKeys(keys, e, world, wrap); } }) : null]),
        keys, h("span", { class: "ch-hint", text: t("wi_keys_hint") })]);
      return h("div", { class: "ch-two" }, [wrap, field("wi_secondary", keysInput("keysecondary"))]);
    })(),
    h("div", { class: "ch-three" }, [
      field("wi_logic", selectEl([["0", t("logic_and_any")], ["3", t("logic_and_all")], ["2", t("logic_not_any")], ["1", t("logic_not_all")]], String(e.selectiveLogic || 0), function (v) { e.selectiveLogic = Number(v); save(); })),
      field("wi_strategy_entry", selectEl([["normal", t("wi_normal")], ["const", t("wi_constant")]], e.constant ? "const" : "normal", function (v) { e.constant = v === "const"; save(); redraw(); })),
      posField
    ]),
    Number(e.position) === 4 ? h("div", { class: "ch-two" }, [numIn("depth", "depth"), field("role", selectEl([["0", t("role_system")], ["1", t("role_user")], ["2", t("role_assistant")]], String(e.role || 0), function (v) { e.role = Number(v); save(); }))]) : null,
    Number(e.position) === 7 ? field("wi_outlet", textInput(e.outletName, function (v) { e.outletName = v; save(); })) : null,
    (function () {
      var ta = textInput(e.content, function (v) { e.content = v; save(); }, { multiline: true, rows: 6, live: true });
      setTimeout(function () { autosizeField(ta); }, 0); ta.addEventListener("input", function () { autosizeField(ta); });
      ta.setAttribute("data-assist-entry", String(e.uid));
      var spec = { field: "wi_content", label: t("wi_content") + (e.comment ? " · " + e.comment : ""), host: assistHost,
        ctx: function () { return { card: S.char ? S.char.data : null, world: world, entry: e }; } };
      return h("div", { class: "ch-field" }, [h("div", { class: "ch-label-row" }, [h("span", { class: "ch-label", text: t("wi_content") }), assistButton(ta, spec)]), ta]);
    })(),
    h("div", { class: "ch-four" }, [numIn("order", "wi_order"), numIn("probability", "wi_probability"), numIn("scanDepth", "wi_scan_depth", true), numIn("sticky", "wi_sticky", true)]),
    h("div", { class: "ch-four" }, [numIn("cooldown", "wi_cooldown", true), numIn("delay", "wi_delay", true), field("wi_group", textInput(e.group, function (v) { e.group = v; save(); })), numIn("groupWeight", "wi_group_weight")]),
    h("div", { class: "ch-three" }, [tri("caseSensitive", "wi_case"), tri("matchWholeWords", "wi_whole"), tri("useGroupScoring", "wi_group_scoring")]),
    h("div", { class: "ch-flags" }, [
      toggle("wi_use_prob", e.useProbability, function (v) { e.useProbability = v; save(); }),
      toggle("wi_group_override", e.groupOverride, function (v) { e.groupOverride = v; save(); }),
      toggle("wi_exclude_rec", e.excludeRecursion, function (v) { e.excludeRecursion = v; save(); }),
      toggle("wi_prevent_rec", e.preventRecursion, function (v) { e.preventRecursion = v; save(); }),
      toggle("wi_delay_rec", !!e.delayUntilRecursion, function (v) { e.delayUntilRecursion = v ? 1 : 0; save(); }),
      toggle("wi_ignore_budget", e.ignoreBudget, function (v) { e.ignoreBudget = v; save(); }),
      toggle("wi_match_persona", e.matchPersonaDescription, function (v) { e.matchPersonaDescription = v; save(); }),
      toggle("wi_match_desc", e.matchCharacterDescription, function (v) { e.matchCharacterDescription = v; save(); }),
      toggle("wi_match_pers", e.matchCharacterPersonality, function (v) { e.matchCharacterPersonality = v; save(); }),
      toggle("wi_match_scenario", e.matchScenario, function (v) { e.matchScenario = v; save(); }),
      toggle("wi_match_notes", e.matchCreatorNotes, function (v) { e.matchCreatorNotes = v; save(); }),
      toggle("wi_match_depth", e.matchCharacterDepthPrompt, function (v) { e.matchCharacterDepthPrompt = v; save(); })
    ]),
    h("div", { class: "ch-two" }, [
      field("wi_filter_names", textInput(((e.characterFilter || {}).names || []).join(", "), function (v) { e.characterFilter = e.characterFilter || {}; e.characterFilter.names = v.split(",").map(function (x) { return x.trim(); }).filter(Boolean); save(); }), "wi_filter_names_hint"),
      field("wi_filter_mode", selectEl([["inc", t("wi_include")], ["exc", t("wi_exclude")]], (e.characterFilter || {}).isExclude ? "exc" : "inc", function (v) { e.characterFilter = e.characterFilter || {}; e.characterFilter.isExclude = v === "exc"; save(); }))
    ])
  ]);
  card.appendChild(bodyEl);
  return card;
}

/* ====================================================================== personas panel */
function drawPersonasPanel(body) { drawPersonaManager(body, function () { render(); }, false); }

/* ====================================================================== appearance */
function drawAppearancePanel(body) {
  var ui = S.settings.ui;
  function set(key, v) { ui[key] = v; saveSettings(); applyAppearance(); }
  function setAndRender(key, v) { set(key, v); if (S.view === "chat") renderMessages(); }
  var presets = [["bubbles", t("style_bubbles")], ["flat", t("style_flat")], ["document", t("style_document")]];
  var styleRow = h("div", { class: "ch-style-pick" }, presets.map(function (p) {
    var b = h("button", { class: "ch-style-opt" + (ui.chatStyle === p[0] ? " on" : ""), type: "button" }, [h("span", { class: "ch-style-prev " + p[0] }, [h("i"), h("i"), h("i")]), h("span", { text: p[1] })]);
    b.addEventListener("click", function () { set("chatStyle", p[0]); renderDrawer(); });
    return b;
  }));
  body.appendChild(section(t("sec_chat_style"), "chat", [
    styleRow,
    field("avatar_shape", selectEl([["circle", t("shape_circle")], ["rounded", t("shape_rounded")], ["square", t("shape_square")], ["portrait", t("shape_portrait")]], ui.avatarShape, function (v) { set("avatarShape", v); })),
    slider("avatar_size", ui.avatarSize, 24, 120, 1, function (v) { set("avatarSize", v); }),
    slider("msg_width", ui.msgWidth, 480, 2000, 10, function (v) { set("msgWidth", v); }),
    toggle("hide_avatars", ui.hideAvatars, function (v) { setAndRender("hideAvatars", v); }),
    toggle("show_timestamps", ui.showTimestamps, function (v) { setAndRender("showTimestamps", v); }),
    toggle("show_model", ui.showModel, function (v) { setAndRender("showModel", v); }),
    toggle("show_tokens", ui.showTokens, function (v) { setAndRender("showTokens", v); }),
    toggle("show_ids", ui.showIds, function (v) { setAndRender("showIds", v); }),
    toggle("reasoning_open", ui.reasoningOpen, function (v) { setAndRender("reasoningOpen", v); }),
    toggle("enter_sends", ui.enterSends, function (v) { set("enterSends", v); }, "enter_sends_hint")
  ]));
  body.appendChild(section(t("sec_typography"), "edit", [
    field("font", selectEl([["sans", t("font_sans")], ["serif", t("font_serif")], ["mono", t("font_mono")], ["rounded", t("font_rounded")], ["book", t("font_book")]], ui.font, function (v) { set("font", v); })),
    slider("font_size", ui.fontSize, 70, 160, 1, function (v) { set("fontSize", v); }),
    field("density", selectEl([["compact", t("density_compact")], ["normal", t("density_normal")], ["airy", t("density_airy")]], ui.density || "normal", function (v) { set("density", v); }))
  ]));
  function color(key, labelKey) {
    var input = h("input", { type: "color", class: "ch-color" });
    input.value = /^#[0-9a-fA-F]{6}$/.test(ui[key] || "") ? ui[key] : "#888888";
    input.addEventListener("input", function () { set(key, input.value); });
    return h("div", { class: "ch-color-row" }, [h("span", { class: "ch-label", text: t(labelKey) }), h("span", { class: "mu-grow" }), input,
      h("button", { class: "ch-mini", type: "button", title: t("reset"), html: icon("refresh", 13), onclick: function () { set(key, ""); renderDrawer(); } })]);
  }
  body.appendChild(section(t("sec_colors"), "palette", [
    h("div", { class: "ch-hint", text: t("colors_hint") }),
    color("accent", "c_accent"), color("quoteColor", "c_quote"), color("italicColor", "c_italic"), color("userBubble", "c_user")
  ]));
  body.appendChild(section(t("sec_background"), "image", [
    h("div", { class: "mu-row" }, [
      h("button", { class: "mu-btn small", type: "button", html: icon("image", 14) + esc(t("bg_global")), onclick: function () { chooseBackground("global"); } }),
      S.chat ? h("button", { class: "mu-btn small", type: "button", html: icon("chat", 14) + esc(t("bg_chat")), onclick: function () { chooseBackground("chat"); } }) : null,
      h("button", { class: "mu-btn small ghost", type: "button", text: t("bg_clear"), onclick: function () { ui.bg = ""; if (S.chat) delete S.chat.meta.background; saveSettings(); saveChat(); applyAppearance(); } })
    ]),
    slider("bg_dim", ui.bgDim, 0, 95, 1, function (v) { set("bgDim", v); }),
    slider("bg_blur", ui.blur, 0, 40, 1, function (v) { set("blur", v); }, "bg_blur_hint")
  ], { open: false }));
  body.appendChild(section(t("sec_behavior"), "settings", [
    toggle("animations", ui.animations, function (v) { set("animations", v); }),
    toggle("dbl_edit", ui.dblEdit !== false, function (v) { set("dblEdit", v); })
  ], { open: false }));
}

/* ====================================================================== extras: regex, quick replies, data */
function drawExtrasPanel(body) {
  body.appendChild(section(t("sec_quick_replies"), "zap", [quickRepliesEditor()]));
  body.appendChild(section(t("sec_regex"), "regex", [regexEditor()], { open: false }));
  body.appendChild(section(t("sec_data"), "folder", [dataTools()], { open: false }));
}
function quickRepliesEditor() {
  var wrap = h("div", { class: "ch-ed-col" });
  function draw() {
    wrap.innerHTML = "";
    wrap.appendChild(h("div", { class: "ch-hint", text: t("qr_hint") }));
    (S.settings.quickReplies || []).forEach(function (q, i) {
      wrap.appendChild(h("div", { class: "ch-qr-row" }, [
        textInput(q.label, function (v) { q.label = v; saveSettings(); }, { placeholder: t("qr_label") }),
        selectEl([["send", t("qr_send")], ["insert", t("qr_insert")], ["slash", t("qr_slash")]], q.mode || "send", function (v) { q.mode = v; saveSettings(); }),
        iconBtn("trash", "delete", function () { S.settings.quickReplies.splice(i, 1); saveSettings(); draw(); }),
        textInput(q.text, function (v) { q.text = v; saveSettings(); }, { multiline: true, rows: 2, placeholder: t("qr_text") })
      ]));
    });
    wrap.appendChild(h("button", { class: "mu-btn small", type: "button", html: icon("plus", 14) + esc(t("qr_add")), onclick: function () {
      S.settings.quickReplies = S.settings.quickReplies || [];
      S.settings.quickReplies.push({ label: t("qr_new"), text: "", mode: "send" }); saveSettings(); draw();
    } }));
  }
  draw();
  return wrap;
}
function regexEditor() {
  var wrap = h("div", { class: "ch-ed-col" });
  function draw() {
    wrap.innerHTML = "";
    wrap.appendChild(h("div", { class: "ch-hint", text: t("regex_hint") }));
    (S.settings.regex || []).forEach(function (r, i) {
      var places = [[1, "rx_user"], [2, "rx_ai"], [5, "rx_wi"], [6, "rx_reasoning"]];
      wrap.appendChild(h("div", { class: "ch-rx" + (r.disabled ? " off" : "") }, [
        h("div", { class: "mu-row" }, [textInput(r.scriptName, function (v) { r.scriptName = v; saveSettings(); }, { placeholder: t("rx_name") }),
          h("label", { class: "ch-pm-switch" }, [(function () { var c = h("input", { type: "checkbox" }); c.checked = !r.disabled; c.addEventListener("change", function () { r.disabled = !c.checked; saveSettings(); }); return c; })(), h("span", { class: "ch-switch small" })]),
          iconBtn("trash", "delete", function () { S.settings.regex.splice(i, 1); saveSettings(); draw(); })]),
        field("rx_find", textInput(r.findRegex, function (v) { r.findRegex = v; saveSettings(); }, { placeholder: "/pattern/gi" })),
        field("rx_replace", textInput(r.replaceString, function (v) { r.replaceString = v; saveSettings(); }, { placeholder: "$1" })),
        h("div", { class: "ch-chips" }, places.map(function (p) {
          var on = (r.placement || []).indexOf(p[0]) >= 0;
          var b = h("button", { class: "ch-chip" + (on ? " on" : ""), type: "button", text: t(p[1]) });
          b.addEventListener("click", function () { r.placement = r.placement || []; var k = r.placement.indexOf(p[0]); if (k >= 0) r.placement.splice(k, 1); else r.placement.push(p[0]); saveSettings(); b.classList.toggle("on"); });
          return b;
        })),
        h("div", { class: "ch-two" }, [
          field("rx_mode", selectEl([["alter", t("rx_alter")], ["display", t("rx_display")], ["prompt", t("rx_prompt")]], r.markdownOnly ? "display" : r.promptOnly ? "prompt" : "alter", function (v) { r.markdownOnly = v === "display"; r.promptOnly = v === "prompt"; saveSettings(); })),
          h("div", { class: "ch-two" }, [field("rx_min_depth", textInput(r.minDepth === null || r.minDepth === undefined ? "" : r.minDepth, function (v) { r.minDepth = v === "" ? null : Number(v); saveSettings(); }, { type: "number" })),
            field("rx_max_depth", textInput(r.maxDepth === null || r.maxDepth === undefined ? "" : r.maxDepth, function (v) { r.maxDepth = v === "" ? null : Number(v); saveSettings(); }, { type: "number" }))])
        ])
      ]));
    });
    wrap.appendChild(h("div", { class: "mu-row" }, [
      h("button", { class: "mu-btn small", type: "button", html: icon("plus", 14) + esc(t("rx_add")), onclick: function () {
        S.settings.regex = S.settings.regex || [];
        S.settings.regex.push({ id: E.uid("rx"), scriptName: t("rx_new"), findRegex: "", replaceString: "", placement: [2], disabled: false, markdownOnly: true, promptOnly: false, trimStrings: [], minDepth: null, maxDepth: null });
        saveSettings(); draw();
      } }),
      h("button", { class: "mu-btn small", type: "button", html: icon("upload", 14) + esc(t("rx_import")), onclick: function () {
        pickFile(".json").then(function (files) {
          if (!files[0]) return;
          readAs(files[0], "text").then(function (txt) {
            var json = JSON.parse(txt);
            (Array.isArray(json) ? json : [json]).forEach(function (r) { if (r && r.findRegex !== undefined) { r.id = r.id || E.uid("rx"); S.settings.regex.push(r); } });
            saveSettings(); draw();
          }).catch(function (e) { toast(e.message, "error"); });
        });
      } }),
      (S.settings.regex || []).length ? h("button", { class: "mu-btn small", type: "button", html: icon("download", 14) + esc(t("rx_export")), onclick: function () { download("regex-scripts.json", JSON.stringify(S.settings.regex, null, 2)); } }) : null
    ]));
  }
  draw();
  return wrap;
}
function dataTools() {
  var wrap = h("div", { class: "ch-ed-col" });
  var usage = h("div", { class: "mu-muted ch-small", text: t("loading") });
  Memu.store.usage().then(function (u) { usage.textContent = t("storage_usage", { used: fmtBytes(u.bytes), keys: fmtNum(u.keys) }); }).catch(function () { usage.textContent = ""; });
  wrap.appendChild(usage);
  wrap.appendChild(h("div", { class: "mu-row" }, [
    h("button", { class: "mu-btn small", type: "button", html: icon("download", 14) + esc(t("backup_export")), onclick: exportBackup }),
    h("button", { class: "mu-btn small", type: "button", html: icon("upload", 14) + esc(t("backup_import")), onclick: importBackup })
  ]));
  wrap.appendChild(h("div", { class: "ch-hint", text: t("backup_hint") }));
  return wrap;
}
function exportBackup() {
  toast(t("backup_working"));
  Memu.store.list("").then(function (rows) {
    var keys = rows.map(function (r) { return r.key; });
    var out = { format: "memu-characters-backup", version: 1, created: new Date().toISOString(), data: {} };
    var chain = Promise.resolve();
    for (var i = 0; i < keys.length; i += 40) (function (slice) {
      chain = chain.then(function () { return Memu.store.getMany(slice).then(function (values) { Object.assign(out.data, values); }); });
    })(keys.slice(i, i + 40));
    return chain.then(function () { return download("characters-backup-" + new Date().toISOString().slice(0, 10) + ".json", JSON.stringify(out)); });
  }).catch(function (e) { toast(e.message, "error"); });
}
function importBackup() {
  pickFile(".json").then(function (files) {
    if (!files[0]) return;
    readAs(files[0], "text").then(function (txt) {
      var json = JSON.parse(txt);
      if (!json || json.format !== "memu-characters-backup" || !json.data) throw new Error(t("backup_bad"));
      return confirmBox(t("backup_import_q"), { ok: t("import") }).then(function (ok) {
        if (!ok) return;
        var keys = Object.keys(json.data);
        var chain = Promise.resolve();
        keys.forEach(function (k) { chain = chain.then(function () { return Memu.store.set(k, json.data[k]); }); });
        return chain.then(function () { Store.cache = {}; toast(t("backup_done", { count: keys.length }), "ok"); return boot(); });
      });
    }).catch(function (e) { toast(e.message, "error"); });
  });
}


/* ====================================================================== boot */
/* @memu-part:ai-create.js */
/* @memu-part:assist-prompts.js */
/* @memu-part:assist.js */
function boot() {
  var app = $("#app");
  app.classList.add("loading");
  return Promise.all([
    Store.get("settings", null),
    Store.get("index", []),
    Store.get("personas", []),
    Store.get("worldindex", []),
    Store.get("presetindex", []),
    Memu.asset("presets/index.json").catch(function () { return { presets: [] }; })
  ]).then(function (r) {
    S.settings = mergeDefaults(r[0] || {}, DEFAULT_SETTINGS);
    S.index = Array.isArray(r[1]) ? r[1] : [];
    S.personas = Array.isArray(r[2]) ? r[2] : [];
    S.worldIndex = Array.isArray(r[3]) ? r[3] : [];
    S.userPresets = Array.isArray(r[4]) ? r[4] : [];
    S.presetIndex = (r[5] && r[5].presets) || [];
    if (!S.personas.length) {
      S.personas.push({ id: E.uid("p"), name: t("default_user_name"), description: "", avatar: "", position: 0, depth: 2, role: 0 });
      S.settings.personaId = S.personas[0].id;
      savePersonas(); saveSettings();
    } else syncStarterPersona();
    return Promise.all(S.worldIndex.map(function (w) { return loadWorld(w.id); }));
  }).then(function () {
    return Promise.all([activatePreset(S.settings.presetId || "universal"), loadModels()]);
  }).then(function () {
    app.classList.remove("loading");
    applyAppearance();
    if (S.settings.lastCharId && S.index.some(function (c) { return c.id === S.settings.lastCharId; }) && S.settings.reopenLast !== false) {
      return openCharacter(S.settings.lastCharId);
    }
    render();
  }).catch(function (error) {
    app.classList.remove("loading");
    render();
    toast(t("boot_failed", { message: error.message }), "error");
  });
}
Memu.onLanguage(function () { syncStarterPersona(); render(); });
// The untouched starter persona (the only one, no description/avatar, still named by a default) follows the UI language:
// «Пользователь» ⇄ «User». Phase 2: it was «Вы»/«You» — a pronoun, so {{user}} broke sentences ("while You relaxes").
// A persona the person edited is left alone.
function syncStarterPersona() {
  var p = S.personas.length === 1 ? S.personas[0] : null;
  if (!p || p.description || p.avatar || !/^(Вы|You|Пользователь|User)$/.test(p.name || "")) return;
  var want = t("default_user_name");
  if (p.name !== want) { p.name = want; savePersonas(); }
}
Memu.onTheme(function () { applyAppearance(); });
// Narrow windows: the drawer overlays the chat with a scrim (#app::after, style.css); a click on the scrim lands on #app itself.
document.getElementById("app").addEventListener("click", function (e) {
  if (e.target === e.currentTarget && S.drawerOpen && drawerIsOverlay()) { S.drawerOpen = false; renderDrawer(); }
});
function drawerIsOverlay() {
  var d = document.getElementById("drawer");
  return !!d && !d.hidden && getComputedStyle(d).position === "absolute";
}
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") {
    // Round 31: an Esc that closed a menu is handled here — otherwise MemuAI minimizes the window too.
    if ($$(".ch-menu").length) { closeMenus(); e.preventDefault(); return; }
    if (modalStack.length) { e.preventDefault(); modalStack[modalStack.length - 1].close(); return; }
    if (S.busy) { e.preventDefault(); stopGeneration(); return; }
    if (S.drawerOpen) { e.preventDefault(); S.drawerOpen = false; renderDrawer(); return; }
  }
  if (S.view === "chat" && !modalStack.length && !S.busy && e.altKey && !e.ctrlKey && !e.metaKey) {
    var last = lastMessage();
    if (e.key === "ArrowRight" && last && last.role === "assistant") { e.preventDefault(); if (last.swipe_id >= (last.swipes || []).length - 1) swipeForward(last); else swipe(last, 1); }
    if (e.key === "ArrowLeft" && last && last.role === "assistant") { e.preventDefault(); swipe(last, -1); }
    // r33: Alt+R — regenerate the last reply, Alt+Shift+R — the remembered number of variants at once, Alt+C — continue.
    // e.code: the letter key whatever the keyboard layout (Alt+К on a Russian layout is the same key).
    if (e.code === "KeyR" && canMakeVariants()) { e.preventDefault(); if (e.shiftKey) generateVariants(variantCount()); else generate(last && last.role === "assistant" ? "regenerate" : "normal", { text: "" }); }
    if (e.code === "KeyC" && !e.shiftKey && last) { e.preventDefault(); generate("continue"); }
    if (e.code === "KeyM" && !e.shiftKey) { var chip = $("#model-chip"); if (chip) { e.preventDefault(); chip.click(); } }
  }
});
Memu.on("navigate", function (data) {
  var q = data && data.query ? String(data.query) : "";
  if (!q) return;
  var row = S.index.find(function (c) { return c.id === q || String(c.name).toLowerCase() === q.toLowerCase(); });
  if (row) openCharacter(row.id);
});
/* Introspection handle for tests and power users (module-window scope only). */
window.MemuCharacters = { S: S, E: E, generate: generate, swipe: swipe, startEdit: startEdit, exportCharacter: exportCharacter,
  openDrawer: openDrawer, openInspector: openInspector, buildFor: buildFor, openCharacter: openCharacter, importFiles: importFiles, render: render,
  dropStalePending: dropStalePending, shortModel: shortModel, newCharacter: newCharacter, saveCharacter: saveCharacter,
  syncToolbar: syncToolbar, fitTagBar: fitTagBar, generateVariants: generateVariants, variantMenu: variantMenu, openModelPicker: openModelPicker,
  openVariantCompare: openVariantCompare, setChatModel: setChatModel, loadModels: loadModels, openAiCreator: openAiCreator, aiParseJson: aiParseJson,
  aiNormCard: aiNormCard, aiNormBook: aiNormBook, assistDiff: assistDiff, assistClean: assistClean, assistJsonList: assistJsonList, assistLocalChecks: assistLocalChecks,
  assistFieldMessages: assistFieldMessages, openCharacterEditor: openCharacterEditor, openWorldEditor: openWorldEditor, modelEfforts: modelEfforts, clampEffort: clampEffort, effortValue: effortValue, setEffort: setEffort };
boot().then(function () { Memu.ready(); });
