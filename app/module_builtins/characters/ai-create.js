/* ====================================================================== r33: «Create with AI» — characters and lorebooks
   Inlined into main.js by MemuAI at the «@memu-part:ai-create.js» marker (app/modules_host.py load_package), so it
   shares main.js's scope (S, E, t, h, icon, openModal, Memu…).

   The person describes an idea or names an existing character / world. Optionally MemuAI's web search (Memu.search +
   Memu.fetch, the «web» permission) gathers sources first: the model proposes search queries, the top pages are read.
   The model drafts a full card (V2/V3 fields: description, personality, scenario, first message + alternate
   greetings, example dialogue, tags, system prompt, post-history instructions, creator notes) or a lorebook (entries
   with keywords) as JSON; the draft opens in an editor where every field can be edited or regenerated on its own
   (with an optional instruction), the avatar drawn by an image model (the «media» permission), and nothing is saved
   until «Save». Lorebook entries can be added to an existing lorebook. */

var AI_CARD_FIELDS = ["description", "personality", "scenario", "first_mes", "mes_example", "system_prompt", "post_history_instructions", "creator_notes"];
var AI_FIELD_LABELS = { description: "f_description", personality: "f_personality", scenario: "f_scenario", first_mes: "f_first_mes", mes_example: "f_mes_example",
  system_prompt: "f_system_prompt", post_history_instructions: "f_post_history", creator_notes: "f_creator_notes" };
var AI_LANGS = [["auto", "ai_lang_auto"], ["ru", "ai_lang_ru"], ["en", "ai_lang_en"]];
var AI = { last: null };

function aiImageModels() { return S.models.filter(function (m) { return m.image; }); }
function aiLangRule(lang) {
  if (lang === "ru") return "Write every text field in Russian.";
  if (lang === "en") return "Write every text field in English.";
  return "Write every text field in the same language as the user's request (if the request is a bare name, use " + (Memu.lang === "ru" ? "Russian" : "English") + ").";
}

function aiParseJson(text) {
  var s = String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "");
  var fence = /```(?:json)?\s*([\s\S]*?)```/i.exec(s);
  if (fence && fence[1].indexOf("{") >= 0) s = fence[1];
  var start = s.indexOf("{");
  if (start < 0) throw new Error(t("ai_bad_json"));
  var depth = 0, inStr = false, escp = false, end = -1;
  for (var i = start; i < s.length; i++) {
    var c = s[i];
    if (inStr) { if (escp) escp = false; else if (c === "\\") escp = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  var body = end > 0 ? s.slice(start, end + 1) : s.slice(start);
  var tries = [body, body.replace(/,\s*([}\]])/g, "$1"),

    end > 0 ? null : aiCloseJson(body)];
  for (var k = 0; k < tries.length; k++) {
    if (!tries[k]) continue;
    try { var v = JSON.parse(tries[k]); if (v && typeof v === "object") return v; } catch (e) {  }
  }
  throw new Error(t("ai_bad_json"));
}
function aiCloseJson(body) {
  var stack = [], inStr = false, escp = false;
  for (var i = 0; i < body.length; i++) {
    var c = body[i];
    if (inStr) { if (escp) escp = false; else if (c === "\\") escp = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') inStr = true; else if (c === "{" || c === "[") stack.push(c); else if (c === "}" || c === "]") stack.pop();
  }
  var out = body + (inStr ? '"' : "");
  out = out.replace(/,\s*$/, "").replace(/:\s*$/, ': ""');
  while (stack.length) out += stack.pop() === "{" ? "}" : "]";
  return out.replace(/,\s*([}\]])/g, "$1");
}
function aiStr(v) { return v === undefined || v === null ? "" : Array.isArray(v) ? v.join("\n") : String(v); }
function aiList(v) {
  if (Array.isArray(v)) return v.map(function (x) { return aiStr(x).trim(); }).filter(Boolean);
  return aiStr(v).split(/[,\n]/).map(function (x) { return x.trim(); }).filter(Boolean);
}

function aiCall(ctx, messages, opts) {
  opts = opts || {};
  var params = { temperature: opts.temperature === undefined ? 0.9 : opts.temperature, max_tokens: opts.max_tokens || 6000, prompt_caching: true };
  if (opts.effort) params.effort = opts.effort;
  var raw = "";
  var job = Memu.complete({ model: ctx.model, messages: messages, params: params }, {
    onDelta: function (chunk) { raw += chunk; if (opts.onText) opts.onText(raw); },
    onReasoning: function (chunk) { if (opts.onReasoning) opts.onReasoning(chunk); }
  });
  ctx.jobs.push(job);
  return job.then(function (result) {
    ctx.jobs = ctx.jobs.filter(function (j) { return j !== job; });
    var text = result && typeof result.text === "string" && result.text.length >= raw.length ? result.text : raw;
    if (!text.trim()) throw new Error(t("empty_answer"));
    return text;
  }, function (error) {
    ctx.jobs = ctx.jobs.filter(function (j) { return j !== job; });
    throw ctx.stopped ? new Error(t("ai_stopped")) : error;
  });
}
function aiStop(ctx) { ctx.stopped = true; ctx.jobs.slice().forEach(function (j) { try { j.cancel(); } catch (e) {  } }); }

function aiResearch(ctx, step) {
  var idea = ctx.idea;
  step("queries");
  var ask = [
    { role: "system", content: "You plan web searches for writing a roleplay " + (ctx.kind === "lorebook" ? "lorebook (world info)" : "character card") +
      ". Reply with JSON only: {\"known\": true|false, \"queries\": [\"...\"]}. known = the request names an existing character, person, work or setting " +
      "(book, game, anime, film, series, history). Give 1-3 short search-engine queries (English, plus one in the language of the request when it is not English) that find a wiki / encyclopedia page " +
      "about it. For an original idea give known=false and at most one query about its genre or setting." },
    { role: "user", content: idea }
  ];
  return aiCall(ctx, ask, { temperature: 0.2, max_tokens: 400, effort: "low" }).then(function (text) {
    var plan = aiParseJson(text);
    var queries = aiList(plan.queries).slice(0, 3);
    if (!queries.length) queries = [idea.slice(0, 140)];
    return queries;
  }, function (e) { if (ctx.stopped) throw e; return [idea.slice(0, 140)]; }).then(function (queries) {
    if (ctx.stopped) throw new Error(t("ai_stopped"));
    step("search", { queries: queries });
    var failed = [];
    return Promise.all(queries.map(function (q) {
      return Memu.search({ query: q, limit: 5 }).then(function (r) { return (r && r.results) || []; }, function (e) { failed.push(e); return []; });
    })).then(function (groups) {

      if (failed.length && failed.length === queries.length) throw failed[0];
      return groups;
    });
  }).then(function (groups) {
    if (ctx.stopped) throw new Error(t("ai_stopped"));
    var seen = {}, rows = [];

    for (var i = 0; i < 5; i++) groups.forEach(function (g) {
      var r = g[i];
      if (!r || !r.url || seen[r.url]) return;
      seen[r.url] = true; rows.push({ title: aiStr(r.title).slice(0, 160), url: String(r.url), snippet: aiStr(r.snippet).slice(0, 400) });
    });
    ctx.sources = rows.slice(0, 8);
    var read = ctx.sources.slice(0, 3);
    step("read", { count: read.length });
    return Promise.all(read.map(function (src) {
      return Memu.fetch({ url: src.url, limit: 14000 }).then(function (page) {
        src.text = aiStr(page && page.text).replace(/\n{3,}/g, "\n\n").slice(0, 7000);
        src.read = !!src.text;
      }, function () { src.read = false; });
    }));
  }).then(function () {
    return ctx.sources.map(function (src, i) {
      return "[" + (i + 1) + "] " + src.title + " — " + src.url + "\n" + (src.text || src.snippet || "");
    }).join("\n\n");
  });
}

function aiCardSystem(ctx) {
  return [
    "You are an expert writer of SillyTavern character cards (Character Card V2/V3) for immersive roleplay.",
    aiLangRule(ctx.lang),
    "Use the macros {{char}} for the character and {{user}} for the user inside texts, never a fixed user name.",
    "Reply with ONE JSON object and nothing else, with these keys:",
    "name (string), tags (array of 3-8 short lowercase tags),",
    "description (the main card text: appearance, background, abilities, relationships, speech style; 250-500 words, prose or concise W++-free paragraphs),",
    "personality (a compact summary of traits, 1-3 sentences),",
    "scenario (the starting situation between {{char}} and {{user}}, 2-4 sentences),",
    "first_mes (the opening message written by {{char}} in roleplay style: narration in *asterisks*, dialogue in quotes, 120-250 words, ends with an invitation for {{user}} to act),",
    "alternate_greetings (array of " + ctx.greetings + " different opening messages in the same style, different situations),",
    "mes_example (2-3 example exchanges, each block starting with <START> on its own line, lines prefixed with {{user}}: and {{char}}:),",
    "system_prompt (1-3 sentences of roleplay instructions specific to this character, may be empty),",
    "post_history_instructions (a short reminder kept after the chat history, may be empty),",
    "creator_notes (a short note for people choosing the card: what it is, recommended use),",
    "avatar_prompt (an English prompt for an image model: a portrait of the character, appearance, clothing, style, background; no text in the image)" +
      (ctx.withBook ? "," : "."),
    ctx.withBook ? "lorebook: {\"name\": string, \"entries\": [ " + ctx.bookEntries + " entries {\"title\", \"keys\": [2-5 trigger words/names as they appear in chat], \"content\": 2-5 sentences of facts, \"constant\": false} ]} — places, people, factions, items and terms of the character's world; never duplicate the description." : "",
    "If sources are given, stay faithful to them (names, facts, canon personality) and do not invent contradicting canon; fill gaps plausibly. " +
      "For an original idea, be creative, specific and consistent. Keep it suitable for the user's request; no meta commentary."
  ].filter(Boolean).join("\n");
}
function aiBookSystem(ctx) {
  return [
    "You are an expert writer of SillyTavern lorebooks (World Info) for roleplay.",
    aiLangRule(ctx.lang),
    "Reply with ONE JSON object and nothing else: {\"name\": string, \"entries\": [{\"title\": string, \"keys\": [2-6 trigger words — names, aliases and terms exactly as they would appear in chat, lowercase unless proper nouns], \"secondary_keys\": [], \"content\": string, \"constant\": boolean}]}.",
    "Write " + ctx.bookEntries + " entries. Each content is 2-6 sentences of concrete, self-contained facts (who/what/where, relations, rules), written as reference text, no dialogue.",
    "Cover the most important places, people, factions, history, items, magic/technology and terms. At most one entry may be constant (a short overview of the world).",
    "If sources are given, stay faithful to them; for an original idea be creative and consistent. Do not repeat existing entries if they are listed."
  ].join("\n");
}
function aiUserMessage(ctx, notes) {
  var parts = ["Request: " + ctx.idea];
  if (notes) parts.push("Web sources (may be partial or noisy; cite nothing, just use the facts):\n" + notes);
  if (ctx.existing && ctx.existing.length) parts.push("Existing entries (do not repeat): " + ctx.existing.join("; "));
  return parts.join("\n\n");
}

function openAiCreator(kind, opts) {
  opts = opts || {};
  var targetWorld = opts.world || null;
  if (!textModels().length) { toast(t("models_unavailable"), "error"); return; }
  var prev = AI.last && AI.last.kind === kind && !targetWorld ? AI.last : null;
  var ctx = {
    kind: kind, idea: prev ? prev.idea : "", web: prev ? prev.web : true, lang: prev ? prev.lang : "auto",
    greetings: prev ? prev.greetings : 2, withBook: prev ? prev.withBook : false, bookEntries: prev ? prev.bookEntries : (kind === "lorebook" ? 10 : 6),
    avatar: prev ? prev.avatar : aiImageModels().length > 0, imageModel: prev && prev.imageModel ? prev.imageModel : ((aiImageModels()[0] || {}).id || ""),
    model: (prev && prev.model && modelRow(prev.model)) ? prev.model : S.settings.model || fallbackModel(),
    jobs: [], stopped: false, sources: [], card: null, book: null, avatarUrl: "", busy: false
  };
  if (targetWorld) ctx.existing = Object.keys(targetWorld.entries || {}).map(function (k) { return targetWorld.entries[k].comment || (targetWorld.entries[k].key || [])[0] || ""; }).filter(Boolean).slice(0, 80);
  var title = kind === "lorebook" ? (targetWorld ? t("ai_book_add_title", { name: targetWorld.name }) : t("ai_book_title")) : t("ai_char_title");
  var modal = openModal({ title: title, icon: "wand", full: true, sticky: true, content: function (body) { body.classList.add("ch-ai-body"); } });

  modal.beforeClose = function () {
    if (ctx.busy) { aiStop(ctx); ctx.busy = false; }
    if (ctx.card || ctx.book) AI.last = ctx; else if (!targetWorld) remember();
    return true;
  };
  var body = modal.body, foot = modal.foot;
  modal.box.appendChild(foot);
  function remember() { AI.last = { kind: kind, idea: ctx.idea, web: ctx.web, lang: ctx.lang, greetings: ctx.greetings, withBook: ctx.withBook, bookEntries: ctx.bookEntries, avatar: ctx.avatar, imageModel: ctx.imageModel, model: ctx.model }; }

  function drawIdea() {
    body.innerHTML = ""; foot.innerHTML = "";
    var area = h("textarea", { class: "mu-textarea ch-ai-idea", rows: "5", placeholder: t(kind === "lorebook" ? "ai_book_ph" : "ai_char_ph"), "aria-label": t("ai_idea") });
    area.value = ctx.idea;
    area.addEventListener("input", function () { ctx.idea = area.value; go.disabled = !area.value.trim(); });
    area.addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && area.value.trim()) { e.preventDefault(); start(); } });
    var examples = h("div", { class: "ch-ai-examples" }, (kind === "lorebook" ? ["ai_bex1", "ai_bex2", "ai_bex3"] : ["ai_cex1", "ai_cex2", "ai_cex3"]).map(function (k) {
      return h("button", { class: "ch-chip", type: "button", text: t(k), onclick: function () { area.value = t(k); ctx.idea = area.value; go.disabled = false; area.focus(); } });
    }));
    body.appendChild(h("div", { class: "ch-ai-intro" }, [
      h("div", { class: "ch-ai-intro-ic", html: icon("wand", 26) }),
      h("div", {}, [h("div", { class: "ch-ai-intro-title", text: t(kind === "lorebook" ? "ai_book_intro" : "ai_char_intro") }),
        h("div", { class: "mu-muted", text: t(kind === "lorebook" ? "ai_book_intro_sub" : "ai_char_intro_sub") })])
    ]));
    body.appendChild(h("label", { class: "mu-field", text: t("ai_idea") }));
    body.appendChild(area);
    body.appendChild(examples);
    var grid = h("div", { class: "ch-ai-opts" });

    var modelBtn = h("button", { class: "ch-cchip ch-ai-model", type: "button" });
    function drawModelBtn() {
      modelBtn.innerHTML = "";
      modelBtn.appendChild(h("span", { class: "ch-cchip-ic", html: icon("sparkle", 15) }));
      modelBtn.appendChild(h("span", { class: "ch-cchip-text", text: modelTitle(modelRow(ctx.model), ctx.model) }));
      modelBtn.appendChild(h("span", { class: "ch-cchip-caret", html: icon("chevron", 14) }));
    }
    drawModelBtn();
    modelBtn.addEventListener("click", function () { openModelPicker(modelBtn, { value: ctx.model, noSettings: true, onPick: function (v) { ctx.model = v; drawModelBtn(); } }); });
    grid.appendChild(aiOpt("ai_model", modelBtn, "ai_model_hint"));
    grid.appendChild(aiOpt("ai_web", aiSwitch(ctx.web, function (v) { ctx.web = v; }), "ai_web_hint"));
    grid.appendChild(aiOpt("ai_lang", selectEl(AI_LANGS.map(function (x) { return [x[0], t(x[1])]; }), ctx.lang, function (v) { ctx.lang = v; })));
    if (kind === "character") {
      grid.appendChild(aiOpt("ai_greetings", selectEl([0, 1, 2, 3, 4].map(function (n) { return [String(n), String(n)]; }), String(ctx.greetings), function (v) { ctx.greetings = Number(v); }), "ai_greetings_hint"));
      var bookCount = selectEl([3, 5, 6, 8, 10, 12].map(function (n) { return [String(n), t("entries_n", { count: n })]; }), String(ctx.bookEntries), function (v) { ctx.bookEntries = Number(v); });
      bookCount.disabled = !ctx.withBook;
      grid.appendChild(aiOpt("ai_with_book", h("div", { class: "ch-ai-inline" }, [aiSwitch(ctx.withBook, function (v) { ctx.withBook = v; bookCount.disabled = !v; }), bookCount]), "ai_with_book_hint"));
      var imgs = aiImageModels();
      if (imgs.length) {
        var imgSel = selectEl(imgs.map(function (m) { return [m.id, m.name || m.id]; }), ctx.imageModel, function (v) { ctx.imageModel = v; });
        imgSel.disabled = !ctx.avatar;
        grid.appendChild(aiOpt("ai_avatar", h("div", { class: "ch-ai-inline" }, [aiSwitch(ctx.avatar, function (v) { ctx.avatar = v; imgSel.disabled = !v; }), imgSel]), "ai_avatar_hint"));
      } else {
        grid.appendChild(aiOpt("ai_avatar", h("span", { class: "mu-muted ch-small", text: t("ai_no_image_models") })));
      }
    } else {
      grid.appendChild(aiOpt("ai_entries", selectEl([5, 8, 10, 12, 15, 20].map(function (n) { return [String(n), t("entries_n", { count: n })]; }), String(ctx.bookEntries), function (v) { ctx.bookEntries = Number(v); })));
    }
    body.appendChild(grid);
    var go = h("button", { class: "mu-btn primary", type: "button", html: icon("wand", 16) + "<span>" + esc(t("ai_create")) + "</span>", onclick: start });
    go.disabled = !ctx.idea.trim();
    foot.appendChild(h("span", { class: "mu-muted ch-small ch-ai-foot-hint", text: t("ai_ctrl_enter") }));
    foot.appendChild(h("span", { class: "mu-grow" }));
    foot.appendChild(h("button", { class: "mu-btn ghost", type: "button", text: t("cancel"), onclick: function () { modal.close(); } }));
    foot.appendChild(go);
    setTimeout(function () { area.focus(); }, 60);
  }

  var stepsBox = null, streamBox = null;
  var STEPS = ["queries", "search", "read", "write", "avatar"];
  function drawProgress() {
    body.innerHTML = ""; foot.innerHTML = "";
    stepsBox = h("ol", { class: "ch-ai-steps" });
    STEPS.forEach(function (s) {
      if ((s === "queries" || s === "search" || s === "read") && !ctx.web) return;
      if (s === "avatar" && !(kind === "character" && ctx.avatar && ctx.imageModel)) return;
      stepsBox.appendChild(h("li", { class: "ch-ai-step", "data-step": s }, [h("span", { class: "ch-ai-step-dot" }), h("span", { class: "ch-ai-step-text", text: t("ai_step_" + s) }), h("span", { class: "ch-ai-step-meta" })]));
    });
    streamBox = h("pre", { class: "ch-ai-stream", "aria-live": "off" });
    body.appendChild(h("div", { class: "ch-ai-progress" }, [
      h("div", { class: "ch-ai-progress-head" }, [h("span", { class: "mu-spinner" }), h("b", { text: t("ai_working") }), h("span", { class: "mu-muted ch-small", text: modelTitle(modelRow(ctx.model), ctx.model) })]),
      stepsBox, streamBox]));
    foot.appendChild(h("span", { class: "mu-grow" }));
    foot.appendChild(h("button", { class: "mu-btn", type: "button", html: icon("stop", 15) + "<span>" + esc(t("stop")) + "</span>", onclick: function () { aiStop(ctx); } }));
  }
  function step(name, info) {
    if (!stepsBox) return;
    var found = false;
    $$(".ch-ai-step", stepsBox).forEach(function (li) {
      var s = li.dataset.step;
      if (s === name) { found = true; li.className = "ch-ai-step active"; }
      else if (!found) li.className = "ch-ai-step done";
    });
    var li = stepsBox.querySelector('[data-step="' + name + '"] .ch-ai-step-meta');
    if (li && info) {
      if (info.queries) li.textContent = info.queries.join(" · ");
      if (info.count !== undefined) li.textContent = t("ai_pages", { count: info.count });
      if (info.chars !== undefined) li.textContent = t("ai_chars", { count: fmtNum(info.chars) });
      if (info.text) li.textContent = info.text;
    }
  }
  function finishSteps() { if (stepsBox) $$(".ch-ai-step", stepsBox).forEach(function (li) { li.className = "ch-ai-step done"; }); }

  function start() {
    if (!ctx.idea.trim() || ctx.busy) return;
    if (!modelRow(ctx.model)) ctx.model = fallbackModel();
    remember();
    ctx.busy = true; ctx.stopped = false; ctx.jobs = []; ctx.sources = [];
    drawProgress();
    var notes = "";

    var key = ctx.idea.trim() + "\u0000" + ctx.kind;
    var cached = ctx.web && ctx.research && ctx.research.key === key ? ctx.research : null;
    var research = cached ? Promise.resolve(cached.notes).then(function (n) { ctx.sources = cached.sources; return n; })
      : ctx.web ? aiResearch(ctx, step).then(function (n) { ctx.research = { key: key, notes: n, sources: ctx.sources }; return n; }).catch(function (e) { if (ctx.stopped) throw e; toast(t("ai_search_failed", { message: errText(e) }), "warn"); return ""; }) : Promise.resolve("");
    research.then(function (n) {
      notes = n;
      if (ctx.stopped) throw new Error(t("ai_stopped"));
      step("write");
      var sys = kind === "lorebook" ? aiBookSystem(ctx) : aiCardSystem(ctx);
      return aiCall(ctx, [{ role: "system", content: sys }, { role: "user", content: aiUserMessage(ctx, notes) }], {
        temperature: 0.9, max_tokens: kind === "lorebook" ? 7000 : (ctx.withBook ? 9000 : 6000),
        onText: function (raw) { step("write", { chars: raw.length }); streamBox.textContent = raw.slice(-900); streamBox.scrollTop = streamBox.scrollHeight; },
        onReasoning: function () { step("write", { text: t("thinking") }); }
      });
    }).then(function (text) {
      var json = aiParseJson(text);
      ctx.notes = notes;
      if (kind === "lorebook") ctx.book = aiNormBook(json, ctx);
      else {
        ctx.card = aiNormCard(json);
        if (ctx.withBook && json.lorebook) ctx.book = aiNormBook(json.lorebook, ctx, ctx.card.name);
      }
      if (kind === "character" && ctx.avatar && ctx.imageModel) {
        step("avatar");
        return aiAvatar(ctx).catch(function (e) { toast(t("ai_avatar_failed", { message: errText(e) }), "warn"); });
      }
    }).then(function () {
      finishSteps();
      ctx.busy = false;
      AI.last = ctx;
      drawResult();
    }).catch(function (e) {
      ctx.busy = false;
      if (ctx.stopped) { drawIdea(); toast(t("ai_stopped"), "warn"); return; }
      drawFailure(errText(e));
    });
  }
  function drawFailure(message) {
    body.innerHTML = ""; foot.innerHTML = "";
    body.appendChild(h("div", { class: "ch-error" }, [h("span", { html: icon("alert", 16) }), h("span", { text: message })]));
    if (streamBox && streamBox.textContent) body.appendChild(h("details", { class: "ch-ai-raw" }, [h("summary", { text: t("ai_raw") }), h("pre", { text: streamBox.textContent })]));
    foot.appendChild(h("button", { class: "mu-btn ghost", type: "button", html: icon("left", 15) + "<span>" + esc(t("ai_back")) + "</span>", onclick: drawIdea }));
    foot.appendChild(h("span", { class: "mu-grow" }));
    foot.appendChild(h("button", { class: "mu-btn primary", type: "button", html: icon("refresh", 15) + "<span>" + esc(t("retry")) + "</span>", onclick: start }));
  }

  function drawResult() {
    body.innerHTML = ""; foot.innerHTML = "";
    if (kind === "character") drawCard(); else drawBookOnly();
    if (ctx.sources.length) {
      body.appendChild(h("details", { class: "ch-ai-sources" }, [
        h("summary", { html: icon("globe", 14) + "<span>" + esc(t("ai_sources", { count: ctx.sources.length })) + "</span>" }),
        h("ol", {}, ctx.sources.map(function (src) {
          return h("li", {}, [h("span", { class: "ch-ai-src-title", text: src.title || src.url }), h("span", { class: "mu-muted ch-small ch-ai-src-url", text: src.url + (src.read ? "" : " · " + t("ai_not_read")) })]);
        }))
      ]));
    }
    foot.appendChild(h("button", { class: "mu-btn ghost", type: "button", html: icon("left", 15) + "<span>" + esc(t("ai_back")) + "</span>", onclick: function () { drawIdea(); } }));
    foot.appendChild(h("button", { class: "mu-btn", type: "button", html: icon("refresh", 15) + "<span>" + esc(t("ai_redo_all")) + "</span>", onclick: function () {
      confirmBox(t("ai_redo_q"), { ok: t("ai_redo_all") }).then(function (ok) { if (ok) start(); });
    } }));
    foot.appendChild(h("span", { class: "mu-grow" }));
    foot.appendChild(h("button", { class: "mu-btn primary", type: "button", html: icon("check", 15) + "<span>" + esc(t(kind === "lorebook" ? (targetWorld ? "ai_add_entries" : "ai_save_book") : "ai_save_char")) + "</span>", onclick: save }));
  }
  function drawCard() {
    var card = ctx.card;
    var grid = h("div", { class: "ch-ai-card" });
    var side = h("div", { class: "ch-ai-side" });
    var img = h("img", { class: "ch-ed-avatar-img ch-ai-ava", src: ctx.avatarUrl || defaultAvatar(card.name || "?", card.name), alt: "" });
    side.appendChild(img);
    var avaBtns = h("div", { class: "ch-ed-avatar-actions" });
    if (aiImageModels().length) {
      var drawBtn = h("button", { class: "mu-btn small", type: "button", html: icon("image", 14) + "<span>" + esc(t(ctx.avatarUrl ? "ai_redraw" : "ai_draw")) + "</span>" });
      drawBtn.addEventListener("click", function () {
        if (!ctx.imageModel) ctx.imageModel = aiImageModels()[0].id;
        drawBtn.disabled = true; drawBtn.innerHTML = '<span class="mu-spinner"></span><span>' + esc(t("ai_drawing")) + "</span>";
        aiAvatar(ctx).then(function () { img.src = ctx.avatarUrl; }, function (e) { toast(t("ai_avatar_failed", { message: errText(e) }), "error"); })
          .then(function () { drawBtn.disabled = false; drawBtn.innerHTML = icon("image", 14) + "<span>" + esc(t(ctx.avatarUrl ? "ai_redraw" : "ai_draw")) + "</span>"; });
      });
      avaBtns.appendChild(drawBtn);
    }
    avaBtns.appendChild(h("button", { class: "mu-btn small ghost", type: "button", html: icon("upload", 14) + "<span>" + esc(t("ai_upload")) + "</span>", onclick: function () {
      pickFile("image/*").then(function (files) { if (!files[0]) return; readAs(files[0], "dataurl").then(function (u) { ctx.avatarUrl = u; img.src = u; }); });
    } }));
    side.appendChild(avaBtns);
    side.appendChild(aiField(ctx, "avatar_prompt", t("ai_avatar_prompt"), card, { rows: 3, small: true }));
    var main = h("div", { class: "ch-ai-main" });
    main.appendChild(h("div", { class: "ch-two" }, [
      aiField(ctx, "name", t("f_name"), card, { input: true, onInput: function () { if (!ctx.avatarUrl) img.src = defaultAvatar(card.name || "?", card.name); } }),
      aiField(ctx, "tags", t("f_tags"), card, { input: true, list: true })
    ]));
    AI_CARD_FIELDS.forEach(function (f) {
      main.appendChild(aiField(ctx, f, t(AI_FIELD_LABELS[f]), card, { rows: f === "description" ? 9 : f === "first_mes" || f === "mes_example" ? 7 : 3 }));
      if (f === "first_mes") main.appendChild(drawGreetings());
    });
    if (ctx.book) main.appendChild(drawBook(true));
    grid.appendChild(side); grid.appendChild(main);
    body.appendChild(grid);
  }
  function drawGreetings() {
    var box = h("div", { class: "ch-ai-greets" });
    function draw() {
      box.innerHTML = "";
      box.appendChild(h("div", { class: "ch-ai-flabel" }, [h("b", { text: t("ai_f_alternate_greetings") }), h("span", { class: "mu-muted ch-small", text: String(ctx.card.alternate_greetings.length) }), h("span", { class: "mu-grow" }),
        h("button", { class: "mu-btn small ghost", type: "button", html: icon("plus", 14) + "<span>" + esc(t("ai_add_greeting")) + "</span>", onclick: function (e) {
          var b = e.currentTarget; b.disabled = true;
          aiRegenField(ctx, "alternate_greetings[+]", "").then(function (v) { if (v) { ctx.card.alternate_greetings.push(v); } draw(); }, function (err) { b.disabled = false; toast(errText(err), "error"); });
        } })]));
      ctx.card.alternate_greetings.forEach(function (g, i) {
        var holder = { v: g };
        var row = aiField(ctx, "alternate_greetings[" + i + "]", t("ai_greeting_n", { n: i + 1 }), holder, { rows: 5, key: "v", onInput: function () { ctx.card.alternate_greetings[i] = holder.v; },
          onRemove: function () { ctx.card.alternate_greetings.splice(i, 1); draw(); } });
        box.appendChild(row);
      });
    }
    draw();
    return box;
  }
  function drawBook(inCard) {
    var book = ctx.book;
    var box = h("div", { class: "ch-ai-book" + (inCard ? " in-card" : "") });
    function draw() {
      box.innerHTML = "";
      var head = h("div", { class: "ch-ai-flabel" }, [h("span", { html: icon("book", 16) }), h("b", { text: inCard ? t("ai_linked_book") : t("ai_book_entries") }),
        h("span", { class: "mu-muted ch-small", text: t("entries_n", { count: book.entries.length }) }), h("span", { class: "mu-grow" })]);
      var more = h("button", { class: "mu-btn small", type: "button", html: icon("plus", 14) + "<span>" + esc(t("ai_more_entries")) + "</span>" });
      more.addEventListener("click", function () {
        more.disabled = true; more.innerHTML = '<span class="mu-spinner"></span><span>' + esc(t("ai_writing")) + "</span>";
        aiMoreEntries(ctx).then(function (added) { toast(t("ai_entries_added", { count: added }), "ok"); draw(); }, function (e) { toast(errText(e), "error"); draw(); });
      });
      head.appendChild(more);
      box.appendChild(head);
      if (!inCard || targetWorld) {
        if (!targetWorld) box.appendChild(aiField(ctx, "book_name", t("ai_book_name"), book, { input: true, key: "name", noRegen: true }));
      }
      book.entries.forEach(function (e, i) {
        var card = h("div", { class: "ch-ai-entry" });
        var keys = h("input", { class: "mu-input ch-ai-keys", value: e.keys.join(", "), placeholder: t("ai_keys_ph"), "aria-label": t("ai_keys") });
        keys.addEventListener("input", function () { e.keys = aiList(keys.value); });
        var titleIn = h("input", { class: "mu-input ch-ai-etitle", value: e.title, "aria-label": t("ai_entry_title") });
        titleIn.addEventListener("input", function () { e.title = titleIn.value; });
        var content = h("textarea", { class: "mu-textarea", rows: "3", "aria-label": t("ai_entry_content") });
        content.value = e.content;
        content.addEventListener("input", function () { e.content = content.value; autosizeField(content); });
        var constant = h("label", { class: "ch-ai-const", title: t("ai_constant_hint") }, [(function () { var c = h("input", { type: "checkbox" }); c.checked = !!e.constant; c.addEventListener("change", function () { e.constant = c.checked; }); return c; })(), h("span", { text: t("ai_constant") })]);
        card.appendChild(h("div", { class: "ch-ai-entry-head" }, [titleIn, constant,
          h("button", { class: "ch-ib", type: "button", title: t("ai_regen_entry"), "aria-label": t("ai_regen_entry"), html: icon("refresh", 15), onclick: function (ev) {
            var b = ev.currentTarget; b.disabled = true; b.innerHTML = '<span class="mu-spinner"></span>';
            aiRegenEntry(ctx, i).then(function () { draw(); }, function (err) { toast(errText(err), "error"); draw(); });
          } }),
          h("button", { class: "ch-ib danger", type: "button", title: t("delete"), "aria-label": t("delete"), html: icon("trash", 15), onclick: function () { book.entries.splice(i, 1); draw(); } })]));
        card.appendChild(keys);
        card.appendChild(content);
        box.appendChild(card);
        setTimeout(function () { autosizeField(content); }, 0);
      });
    }
    draw();
    return box;
  }
  function drawBookOnly() { body.appendChild(drawBook(false)); }

  function save() {
    if (kind === "lorebook") {
      var entries = ctx.book.entries.filter(function (e) { return e.content.trim(); });
      if (!entries.length) { toast(t("ai_nothing_to_save"), "warn"); return; }
      if (targetWorld) {
        var uids = Object.keys(targetWorld.entries).map(Number).filter(Number.isFinite);
        var next = uids.length ? Math.max.apply(null, uids) + 1 : 0;
        entries.forEach(function (e) { targetWorld.entries[next] = E.normalizeEntry(aiEntryToSt(e, next), next); next++; });
        saveWorld(targetWorld);
        toast(t("ai_entries_added", { count: entries.length }), "ok");
        modal.close(); AI.last = null;
        if (opts.onDone) opts.onDone(targetWorld);
        return;
      }
      var world = E.importWorld(aiBookToCharacterBook(ctx.book), ctx.book.name || t("lorebook")).world;
      world.id = E.uid("w"); world.name = (ctx.book.name || "").trim() || t("lorebook");
      saveWorld(world);
      toast(t("lorebook_imported", { name: world.name, count: Object.keys(world.entries).length }), "ok");
      modal.close(); AI.last = null;
      if (S.drawerOpen) renderDrawer();
      openWorldEditor(world.id);
      return;
    }
    var card = ctx.card;
    if (!String(card.name || "").trim()) { toast(t("ai_need_name"), "warn"); return; }
    var data = {
      name: card.name.trim(), description: card.description, personality: card.personality, scenario: card.scenario,
      first_mes: card.first_mes, mes_example: card.mes_example, creator_notes: card.creator_notes, system_prompt: card.system_prompt,
      post_history_instructions: card.post_history_instructions, alternate_greetings: card.alternate_greetings.filter(function (g) { return String(g).trim(); }),
      tags: card.tags, creator: "MemuAI", character_version: "1.0",
      extensions: { talkativeness: 0.5, depth_prompt: { prompt: "", depth: 4, role: "system" }, memu_ai: { created: Date.now(), model: ctx.model, sources: ctx.sources.map(function (s) { return s.url; }) } }
    };
    if (ctx.book && ctx.book.entries.length) data.character_book = aiBookToCharacterBook(ctx.book, card.name);
    createFromCard(null, ctx.avatarUrl || "", { data: data, source: "ai" }).then(function (char) {
      toast(t("ai_saved", { name: char.data.name }), "ok");
      modal.close(); AI.last = null;
      return openCharacter(char.id);
    }).catch(function (e) { toast(errText(e), "error"); });
  }

  if (prev && prev.card && kind === "character") { Object.assign(ctx, prev, { jobs: [], stopped: false, busy: false }); drawResult(); }
  else if (prev && prev.book && kind === "lorebook") { Object.assign(ctx, prev, { jobs: [], stopped: false, busy: false }); drawResult(); }
  else drawIdea();
  return modal;
}

function aiOpt(labelKey, control, hintKey) {
  return h("div", { class: "ch-ai-opt" }, [h("div", { class: "ch-ai-opt-label" }, [h("b", { text: t(labelKey) }), hintKey ? h("span", { class: "mu-muted ch-small", text: t(hintKey) }) : null]), h("div", { class: "ch-ai-opt-ctl" }, [control])]);
}
function aiSwitch(value, onChange) {
  var c = h("input", { type: "checkbox" });
  c.checked = !!value;
  c.addEventListener("change", function () { onChange(c.checked); });
  return h("label", { class: "ch-pm-switch" }, [c, h("span", { class: "ch-switch" })]);
}

function aiField(ctx, name, label, holder, opts) {
  opts = opts || {};
  var key = opts.key || name;
  var wrap = h("div", { class: "ch-ai-field" + (opts.small ? " small" : "") });
  var value = opts.list ? (holder[key] || []).join(", ") : aiStr(holder[key]);
  var control = opts.input ? h("input", { class: "mu-input", value: value, "aria-label": label }) : h("textarea", { class: "mu-textarea", rows: String(opts.rows || 3), "aria-label": label });
  if (!opts.input) control.value = value;
  control.addEventListener("input", function () {
    holder[key] = opts.list ? aiList(control.value) : control.value;
    if (!opts.input) autosizeField(control);
    if (opts.onInput) opts.onInput();
  });
  var tools = h("span", { class: "ch-ai-ftools" });
  if (!opts.noRegen) {
    var regen = h("button", { class: "ch-ib small", type: "button", title: t("ai_regen_field"), "aria-label": t("ai_regen_field") + ": " + label, html: icon("refresh", 15) });
    regen.addEventListener("click", function () { askAndRegen(""); });
    regen.addEventListener("contextmenu", function (e) { e.preventDefault(); ask(); });
    var tune = h("button", { class: "ch-ib small", type: "button", title: t("ai_tune_field"), "aria-label": t("ai_tune_field") + ": " + label, html: icon("wand", 15), onclick: ask });
    tools.appendChild(tune); tools.appendChild(regen);
  }
  if (opts.onRemove) tools.appendChild(h("button", { class: "ch-ib small danger", type: "button", title: t("delete"), "aria-label": t("delete"), html: icon("trash", 15), onclick: opts.onRemove }));
  var tokens = h("span", { class: "mu-muted ch-small ch-ai-ftk" });
  function count() { var v = opts.list || opts.input || opts.small ? "" : control.value; tokens.textContent = v ? "≈ " + fmtNum(E.estimateTokens(v)) + " " + t("tok") : ""; }
  control.addEventListener("input", count);
  wrap.appendChild(h("div", { class: "ch-ai-flabel" }, [h("b", { text: label }), tokens, h("span", { class: "mu-grow" }), tools]));
  wrap.appendChild(control);
  function ask() {
    promptBox(t("ai_tune_title", { field: label }), "", { icon: "wand", hint: t("ai_tune_hint") }).then(function (v) { if (v !== null) askAndRegen(v); });
  }
  function askAndRegen(instruction) {
    if (wrap.classList.contains("busy")) return;
    wrap.classList.add("busy");
    control.disabled = true;
    var before = control.value;
    aiRegenField(ctx, name, instruction, function (partial) { if (!opts.list) { control.value = partial; if (!opts.input) autosizeField(control); } }).then(function (v) {
      if (v === undefined || v === null || (Array.isArray(v) ? !v.length : !String(v).trim())) { control.value = before; return; }
      holder[key] = opts.list ? aiList(v) : aiStr(v);
      control.value = opts.list ? holder[key].join(", ") : holder[key];
      if (opts.onInput) opts.onInput();
    }, function (e) { control.value = before; toast(errText(e), "error"); }).then(function () {
      wrap.classList.remove("busy"); control.disabled = false; if (!opts.input) autosizeField(control); count();
    });
  }
  setTimeout(function () { if (!opts.input) autosizeField(control); count(); }, 0);
  return wrap;
}

function aiNormCard(json) {
  var c = json && json.data && typeof json.data === "object" && !json.description ? json.data : json || {};
  var card = { name: aiStr(c.name).trim(), tags: aiList(c.tags).slice(0, 12).map(function (x) { return x.toLowerCase(); }), avatar_prompt: aiStr(c.avatar_prompt) };
  AI_CARD_FIELDS.forEach(function (f) { card[f] = aiStr(c[f]).trim(); });
  card.alternate_greetings = (Array.isArray(c.alternate_greetings) ? c.alternate_greetings : aiStr(c.alternate_greetings) ? [c.alternate_greetings] : [])
    .map(function (g) { return aiStr(g).trim(); }).filter(Boolean);
  return card;
}
function aiNormBook(json, ctx, fallbackName) {
  var src = json && Array.isArray(json.entries) ? json : json && json.lorebook ? json.lorebook : json || {};
  var entries = (Array.isArray(src.entries) ? src.entries : []).map(function (e) {
    e = e || {};
    return { title: aiStr(e.title || e.comment || e.name).trim(), keys: aiList(e.keys || e.key).slice(0, 12), secondary_keys: aiList(e.secondary_keys || e.keysecondary).slice(0, 8),
      content: aiStr(e.content).trim(), constant: !!e.constant };
  }).filter(function (e) { return e.content; });
  entries.forEach(function (e) { if (!e.title) e.title = e.keys[0] || ""; if (!e.keys.length && e.title) e.keys = [e.title.toLowerCase()]; });
  return { name: aiStr(src.name).trim() || (fallbackName ? t("char_book_name", { name: fallbackName }) : ""), entries: entries };
}
function aiEntryToSt(e, uid) {
  return { uid: uid, key: e.keys.slice(), keysecondary: (e.secondary_keys || []).slice(), comment: e.title, content: e.content, constant: !!e.constant,
    selective: !!(e.secondary_keys && e.secondary_keys.length), order: 100, position: 1, addMemo: !!e.title };
}
function aiBookToCharacterBook(book, charName) {
  return { name: book.name || (charName ? t("char_book_name", { name: charName }) : ""), entries: book.entries.filter(function (e) { return e.content.trim(); }).map(function (e, i) {
    return { id: i, keys: e.keys, secondary_keys: e.secondary_keys || [], comment: e.title, content: e.content, constant: !!e.constant,
      selective: !!(e.secondary_keys && e.secondary_keys.length), enabled: true, insertion_order: 100, position: "after_char" };
  }) };
}

function aiCardContext(ctx) {
  var c = ctx.card ? JSON.stringify(Object.assign({}, ctx.card), null, 1) : "";
  return c.length > 14000 ? c.slice(0, 14000) + "…" : c;
}

function aiRegenField(ctx, field, instruction, onPartial) {
  ctx.jobs = ctx.jobs || [];
  ctx.stopped = false;
  var m = /^alternate_greetings\[(\d+|\+)\]$/.exec(field);
  var what = m ? (m[1] === "+" ? "a NEW alternate greeting (a different situation than the existing first_mes and alternate_greetings)" : "alternate greeting #" + (Number(m[1]) + 1) + " (alternate_greetings[" + m[1] + "])")
    : field === "tags" ? "the tags (array of 3-8 short lowercase tags)" : field === "book_name" ? "the lorebook name" : "the field \"" + field + "\"";
  var sys = (ctx.kind === "lorebook" ? aiBookSystem(ctx) : aiCardSystem(ctx)).split("\nReply with ONE JSON object")[0] +
    "\nYou now rewrite ONE part of an existing card. Reply with JSON only: {\"value\": ...} — a string" + (field === "tags" ? " array" : "") + ". Keep everything consistent with the rest of the card.";
  var user = "Original request: " + ctx.idea + "\n\nCurrent card JSON:\n" + aiCardContext(ctx) +
    (ctx.notes ? "\n\nWeb sources:\n" + String(ctx.notes).slice(0, 9000) : "") +
    "\n\nWrite " + what + " anew" + (instruction ? " following this instruction: " + instruction : " — a fresh, better version") + ".";
  return aiCall(ctx, [{ role: "system", content: sys }, { role: "user", content: user }], {
    temperature: 0.95, max_tokens: 2500,
    onText: onPartial ? function (raw) { var mm = /"value"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(raw); if (mm) { try { onPartial(JSON.parse('"' + mm[1].replace(/\\$/, "") + '"')); } catch (e) {  } } } : null
  }).then(function (text) {
    var v = aiParseJson(text).value;
    return field === "tags" ? aiList(v) : aiStr(v).trim();
  });
}
function aiMoreEntries(ctx) {
  var book = ctx.book;
  var sub = Object.assign({}, ctx, { bookEntries: 5, existing: book.entries.map(function (e) { return e.title || e.keys[0]; }).concat(ctx.existing || []) });
  sub.jobs = ctx.jobs; sub.stopped = false;
  var context = ctx.card ? "\n\nThe lorebook belongs to this character card:\n" + aiCardContext(ctx) : "";
  return aiCall(sub, [{ role: "system", content: aiBookSystem(sub) }, { role: "user", content: aiUserMessage(sub, ctx.notes ? String(ctx.notes).slice(0, 9000) : "") + context }], { temperature: 0.9, max_tokens: 4000 })
    .then(function (text) {
      var more = aiNormBook(aiParseJson(text), sub).entries;
      var known = {};
      book.entries.forEach(function (e) { known[(e.title || "").toLowerCase()] = true; });
      more = more.filter(function (e) { return !known[(e.title || "").toLowerCase()]; });
      book.entries = book.entries.concat(more);
      return more.length;
    });
}
function aiRegenEntry(ctx, index) {
  var e = ctx.book.entries[index];
  var sys = aiBookSystem(Object.assign({}, ctx, { bookEntries: 1 })).split("\nReply with ONE JSON object")[0] +
    "\nRewrite ONE lorebook entry. Reply with JSON only: {\"title\": string, \"keys\": [...], \"content\": string, \"constant\": boolean}.";
  var user = "Request: " + ctx.idea + (ctx.card ? "\n\nCharacter card:\n" + aiCardContext(ctx) : "") +
    "\n\nOther entries: " + ctx.book.entries.filter(function (x, i) { return i !== index; }).map(function (x) { return x.title; }).join("; ") +
    "\n\nEntry to rewrite (keep its topic, improve facts and keywords):\n" + JSON.stringify(e);
  return aiCall(ctx, [{ role: "system", content: sys }, { role: "user", content: user }], { temperature: 0.85, max_tokens: 1500 }).then(function (text) {
    var row = aiNormBook({ entries: [aiParseJson(text)] }, ctx).entries[0];
    if (row) ctx.book.entries[index] = row;
  });
}
function aiAvatar(ctx) {
  var card = ctx.card || {};
  var prompt = (card.avatar_prompt || "").trim() || ("Portrait of " + card.name + ". " + String(card.description || "").slice(0, 600));
  prompt += " Character portrait, upper body, vertical composition, detailed, high quality, no text.";
  return Memu.image({ prompt: prompt.slice(0, 3800), model: ctx.imageModel, aspect: "2:3" }).then(function (res) {
    var url = ((res && res.urls) || []).filter(Boolean)[0];
    if (!url) throw new Error(t("ai_no_image"));
    ctx.avatarUrl = url;
    return url;
  });
}
