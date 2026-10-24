/* ====================================================================== r37: AI co-writer — prompts
   Inlined into main.js at the «@memu-part:assist-prompts.js» marker (app/modules_host.py load_package), before assist.js.
   Everything the co-writer sends to a model is built here, so the wording can be tuned in one place.

   Principles the prompts enforce: the person is the author and the AI is an editor at their elbow — keep the
   author's voice, tense, person, formatting and macros ({{char}}, {{user}}), never invent facts that contradict
   or go beyond the card unless asked to, and answer with the field text only (no preface, no quotes, no fences). */

// What each field is for — the model gets the one line for the field it edits.
var ASSIST_FIELD_GUIDE = {
  name: "the character's name (a short proper name, no description)",
  description: "the character description: appearance, background, abilities, relationships, important facts. It is always in the prompt, so it should be dense and concrete",
  personality: "a short personality summary: temperament, values, quirks, how they treat others",
  scenario: "the scenario: where and when the roleplay starts, the situation between {{char}} and {{user}}",
  first_mes: "the first message of the chat, written by {{char}}: it sets the scene, the style, the tense and the length every later reply will copy. It must leave {{user}} something to react to and must never act or speak for {{user}}",
  alternate_greeting: "an alternate first message (a different opening situation for the same character), written by {{char}}, never acting for {{user}}",
  mes_example: "example dialogue: blocks that start with <START>, lines prefixed with {{user}}: and {{char}}:, showing the voice and manner of {{char}}",
  system_prompt: "a character-specific system prompt for the model (instructions, not prose); {{original}} keeps the preset's prompt",
  post_history_instructions: "post-history instructions (a short jailbreak/reminder sent after the chat history: style, pacing, rules); {{original}} keeps the preset's one",
  creator_notes: "creator notes shown to people who use the card (what the card is, how to play it, recommended settings); not sent to the model",
  depth_prompt: "the character's note injected at depth in the chat (short, imperative reminders about the character)",
  tags: "comma-separated tags for the gallery (genre, setting, traits); short, lowercase, 3-10 of them",
  creator: "the card author's name",
  character_version: "the card version (e.g. 1.0)",
  wi_content: "a lorebook (world info) entry: inserted into the prompt when its keywords appear. Dense, factual, third person, about one thing; no dialogue",
  wi_title: "the title (memo) of a lorebook entry: a few words",
  wi_keys: "the entry's trigger keywords"
};

// Field-editing actions. `needsText` = the action makes no sense on an empty field.
var ASSIST_ACTIONS = {
  improve: { icon: "sparkle", needsText: true, rule: "Improve this text: clearer, more vivid and better structured, while keeping every fact, the author's voice, tense, person, formatting and length (±20%)." },
  continue: { icon: "forward", needsText: true, rule: "Continue this text from where it stops. Return ONLY the new continuation (do not repeat the existing text), matching its voice, tense and formatting. Add roughly one or two paragraphs." },
  expand: { icon: "plus", needsText: true, rule: "Expand this text: add concrete detail, sensory specifics and depth consistent with the rest of the card. Keep everything that is there and the author's voice; roughly 1.5–2× longer." },
  shorten: { icon: "columns", needsText: true, rule: "Make this text more concise (about half as long) without losing any important fact, name or rule. Cut repetition and filler first." },
  fix: { icon: "check", needsText: true, rule: "Fix spelling, grammar, punctuation and typos only. Change nothing else: not the wording, not the style, not the facts, not the formatting." },
  vivid: { icon: "zap", needsText: true, rule: "Rewrite this text to be livelier and more evocative: stronger verbs, showing instead of telling, more character. Keep every fact and the length roughly the same." },
  tone: { icon: "palette", needsText: true, rule: "Rewrite this text in the tone described by the author's instruction. Keep every fact." },
  variants: { icon: "layers", needsText: false, rule: "" },
  fill: { icon: "wand", needsText: false, rule: "This field is empty. Write it from scratch, based only on what the rest of the card establishes. Invent as little as possible; what you add must fit the card." },
  custom: { icon: "edit", needsText: false, rule: "Follow the author's instruction for this field." },
  fix_issue: { icon: "check", needsText: false, rule: "Fix the problem the reviewer found (described in the instruction). Change only what is needed to fix it; keep everything else as written." }
};

// The tones offered under «Make it livelier / another tone».
var ASSIST_TONES = ["tone_vivid", "tone_dark", "tone_light", "tone_romantic", "tone_formal", "tone_humor", "tone_poetic", "tone_terse"];

function assistLangRule(lang, sample) {
  if (lang === "ru") return "Write in Russian.";
  if (lang === "en") return "Write in English.";
  var text = String(sample || "");
  var cyr = (text.match(/[А-Яа-яЁё]/g) || []).length, lat = (text.match(/[A-Za-z]/g) || []).length;
  if (cyr + lat < 20) return "Write in " + (Memu.lang === "ru" ? "Russian" : "English") + " unless the field text is in another language.";
  return "Write in the same language as the card (it is mostly " + (cyr > lat ? "Russian" : "English") + "); keep a field's existing language.";
}

var ASSIST_SYSTEM = [
  "You are a co-writer helping an author build a roleplay character card (SillyTavern / Character Card V2-V3 format) and its lorebook.",
  "The author is in charge; you are an editor at their elbow, not a ghostwriter.",
  "Rules:",
  "- Keep the author's voice, style, tense, grammatical person and formatting (markdown, *actions*, \"speech\", line breaks, lists).",
  "- Keep macros exactly as written: {{char}}, {{user}}, {{original}}, <START> and any other {{...}}. Prefer {{char}} / {{user}} over literal names where the card does.",
  "- Do not contradict the card. Do not invent major facts (backstory twists, new characters, powers) unless the instruction asks for ideas.",
  "- Never write actions or speech for {{user}} in messages written by {{char}}.",
  "- Answer with the resulting field text ONLY: no preface, no explanations, no quotes around it, no code fences, no notes after it."
].join("\n");

// Card (and lorebook) → compact context block. `skip` = the field being edited (shown separately).
function assistCardBrief(card, skip, limit) {
  limit = limit || 1800;
  var out = [];
  function add(label, key, value) {
    if (key === skip) return;
    var v = String(value || "").trim();
    if (!v) return;
    out.push("## " + label + "\n" + (v.length > limit ? v.slice(0, limit) + " […]" : v));
  }
  card = card || {};
  add("Name", "name", card.name);
  add("Description", "description", card.description);
  add("Personality", "personality", card.personality);
  add("Scenario", "scenario", card.scenario);
  add("First message", "first_mes", card.first_mes);
  (card.alternate_greetings || []).forEach(function (g, i) { add("Alternate greeting " + (i + 1), "alternate_greeting:" + i, g); });
  add("Example dialogue", "mes_example", card.mes_example);
  add("System prompt", "system_prompt", card.system_prompt);
  add("Post-history instructions", "post_history_instructions", card.post_history_instructions);
  add("Creator notes", "creator_notes", card.creator_notes);
  add("Character note (depth prompt)", "depth_prompt", card.extensions && card.extensions.depth_prompt && card.extensions.depth_prompt.prompt);
  add("Tags", "tags", (card.tags || []).join(", "));
  return out.join("\n\n");
}
function assistBookBrief(world, skipUid, limit) {
  if (!world || !world.entries) return "";
  limit = limit || 600;
  var rows = Object.keys(world.entries).map(function (k) { return world.entries[k]; }).filter(function (e) { return String(e.uid) !== String(skipUid); });
  if (!rows.length) return "";
  return "# Lorebook «" + (world.name || "") + "»\n" + rows.slice(0, 60).map(function (e) {
    var c = String(e.content || "").trim();
    return "- [" + (e.comment || "untitled") + "] keys: " + (e.key || []).join(", ") + "\n  " + (c.length > limit ? c.slice(0, limit) + " […]" : c).replace(/\n/g, "\n  ");
  }).join("\n");
}

/* A field action → chat messages. a = {action, field, label, text, selection:{start,end,text}|null, instruction, card, world, entry, lang} */
function assistFieldMessages(a) {
  var spec = ASSIST_ACTIONS[a.action] || ASSIST_ACTIONS.custom;
  var guide = ASSIST_FIELD_GUIDE[a.field] || "a text field of the card";
  var sel = a.selection && a.selection.text ? a.selection : null;
  var ctx = [assistCardBrief(a.card, a.field), assistBookBrief(a.world, a.entry && a.entry.uid)].filter(Boolean).join("\n\n");
  var parts = [];
  parts.push("# The card so far (context — do not rewrite it)\n" + (ctx || "(the card is still empty)"));
  if (a.entry) parts.push("# The lorebook entry being edited\nTitle: " + (a.entry.comment || "") + "\nKeys: " + (a.entry.key || []).join(", "));
  parts.push("# Field being edited: " + a.label + "\nThis field is " + guide + ".");
  if (sel) {
    parts.push("# Full current text of the field\n" + a.text);
    parts.push("# The author selected this fragment — rewrite ONLY it\n" + sel.text);
  } else if (a.text) parts.push("# Current text of the field\n" + a.text);
  else parts.push("# Current text of the field\n(empty)");
  var task = spec.rule;
  if (a.action === "variants") task = (a.text ? "Write three clearly different rewrites of " + (sel ? "the selected fragment" : "the field text") + " (different angles or approaches, same facts)."
    : "Write three clearly different versions of this field, based on the card.") +
    " Reply with JSON only: {\"variants\": [\"...\", \"...\", \"...\"]}.";
  if (a.instruction) task += "\nAuthor's instruction: " + a.instruction;
  if (sel && a.action !== "variants") task += "\nReturn only the replacement for the selected fragment — it will be pasted in its place, so it must fit the text around it.";
  task += "\n" + assistLangRule(a.lang, a.text || assistCardBrief(a.card));
  if (a.field === "tags") task += "\nReturn the tags comma-separated on one line.";
  if (a.field === "name" || a.field === "wi_title") task += "\nReturn only the name, nothing else.";
  parts.push("# Task\n" + task);
  return [{ role: "system", content: ASSIST_SYSTEM }, { role: "user", content: parts.join("\n\n") }];
}

// The side-panel chat: the model sees the whole card and may propose field text inside <field name="key">…</field>.
function assistChatSystem(kind, fieldKeys) {
  return ASSIST_SYSTEM.split("\n- Answer with")[0] + "\n" + [
    "You are chatting with the author about their " + (kind === "lorebook" ? "lorebook" : "character card") + ". Be concrete and brief; use short lists.",
    "When you propose new text for a field, put the complete proposed text of that field inside <field name=\"KEY\">…</field>, where KEY is one of: " + fieldKeys.join(", ") + ".",
    "The author can apply such a block to the field with one click, so it must be the full field text, ready to use. Propose a field only when it helps; ideas and questions go in plain text.",
    "For a new lorebook entry use <entry title=\"…\" keys=\"key1, key2\">content</entry>."
  ].join("\n");
}

// «Review the card»: JSON list of concrete issues.
function assistReviewMessages(card, world, localNotes, lang) {
  var fields = "name, description, personality, scenario, first_mes, alternate_greeting:N, mes_example, system_prompt, post_history_instructions, creator_notes, tags";
  var body = "# The card\n" + (assistCardBrief(card, null, 4000) || "(empty)") + (world ? "\n\n" + assistBookBrief(world, null, 400) : "") +
    (localNotes.length ? "\n\n# Automatic checks already found (do not repeat them)\n- " + localNotes.join("\n- ") : "") +
    "\n\n# Task\nReview this card as an experienced card author. Find concrete problems: contradictions between fields, repetition across fields, " +
    "the first message acting or speaking for {{user}}, literal names where {{char}}/{{user}} belong, vague or generic text, missing important information, " +
    "example dialogue in a wrong format, style mismatches. At most 8 issues, most important first; skip nitpicks. " +
    "Reply with JSON only: {\"issues\": [{\"field\": \"one of: " + fields + "\", \"severity\": \"high|medium|low\", \"title\": \"short\", \"detail\": \"one or two sentences\", \"fix\": \"an instruction for an editor to fix it in that field, or empty if it needs the author's decision\"}]}. " +
    "Write title and detail in " + (lang === "en" ? "English" : lang === "ru" ? "Russian" : (Memu.lang === "ru" ? "Russian" : "English")) + ".";
  return [{ role: "system", content: "You are a meticulous editor of roleplay character cards. You reply with JSON only." }, { role: "user", content: body }];
}

// Lorebook helpers ------------------------------------------------------------------------------------------------
function assistKeysMessages(entry, world, lang) {
  return [{ role: "system", content: "You suggest trigger keywords for lorebook (world info) entries. Reply with JSON only." },
    { role: "user", content: "# Entry\nTitle: " + (entry.comment || "") + "\nCurrent keys: " + (entry.key || []).join(", ") + "\nContent:\n" + (entry.content || "") +
      (world ? "\n\n" + assistBookBrief(world, entry.uid, 200) : "") +
      "\n\n# Task\nSuggest 6-12 trigger keywords that would appear in chat when this entry is relevant: the name and its short forms, nicknames, " +
      "inflected forms for languages with cases (e.g. Russian: «дракон», «дракона», «дракону»), key related words. Avoid words so common they would trigger everywhere, and keys already used by other entries for other things. " +
      "Do not repeat the current keys. " + assistLangRule(lang, entry.content) + " Reply: {\"keys\": [\"...\"]}" }];
}
function assistEntryFromTextMessages(text, card, world, lang) {
  return [{ role: "system", content: ASSIST_SYSTEM.split("\n- Answer with")[0] + "\nYou reply with JSON only." },
    { role: "user", content: (card ? "# Card context\n" + assistCardBrief(card, null, 800) + "\n\n" : "") + (world ? assistBookBrief(world, null, 200) + "\n\n" : "") +
      "# Fragment the author selected\n" + text +
      "\n\n# Task\nTurn this fragment into one lorebook entry about its main subject: a dense factual entry in third person, using only what the fragment and the card say. " +
      assistLangRule(lang, text) + " Reply: {\"title\": \"...\", \"keys\": [\"...\"], \"content\": \"...\"}" }];
}
function assistGapsMessages(card, world, lang) {
  return [{ role: "system", content: "You audit a roleplay card and its lorebook for missing world info. You reply with JSON only." },
    { role: "user", content: (card ? "# Card\n" + assistCardBrief(card, null, 3000) + "\n\n" : "") + (assistBookBrief(world, null, 500) || "# Lorebook\n(empty)") +
      "\n\n# Task\nList named things (people, places, factions, items, events, terms) that the card or the lorebook mention but no lorebook entry describes. " +
      "Skip the card's own character and {{user}} (the card already describes them) and never use macros like {{char}} as keys. " +
      "For each propose an entry built from what the text already says about it (do not invent big facts; mark guesses as brief and neutral). At most 8, most important first. " +
      assistLangRule(lang, card ? card.description : "") + " Reply: {\"entries\": [{\"title\": \"...\", \"keys\": [\"...\"], \"content\": \"...\", \"why\": \"where it is mentioned, briefly\"}]}" }];
}
function assistConflictsMessages(world, card, lang) {
  return [{ role: "system", content: "You check a lorebook for contradictions. You reply with JSON only." },
    { role: "user", content: assistBookBrief(world, null, 1500) + (card ? "\n\n# Card\n" + assistCardBrief(card, null, 1500) : "") +
      "\n\n# Task\nFind contradictions between entries (or between an entry and the card), duplicated entries, and keys shared by unrelated entries. At most 8. " +
      "Write in " + (lang === "en" ? "English" : lang === "ru" ? "Russian" : (Memu.lang === "ru" ? "Russian" : "English")) + ". " +
      "Reply: {\"issues\": [{\"entries\": [\"entry title\", \"…\"], \"title\": \"short\", \"detail\": \"…\", \"fix\": \"instruction to fix the FIRST listed entry, or empty\"}]}" }];
}
