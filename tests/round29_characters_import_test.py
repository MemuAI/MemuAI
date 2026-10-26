# -*- coding: utf-8 -*-
"""Round 29 (phase 2): lossless lorebook and character-card import/export of the «Characters» module.

The engine block of main.js runs in Node against samples shaped exactly like the files real tools write:
SillyTavern (World Info file, V2 card JSON/PNG with an embedded character_book), chub (the hybrid
``sillytavern_raw.json`` book, the lorebook card ``chub_raw.json``, the API ``definition``), RisuAI (V3 PNG with a
``ccv3`` chunk, a ``chara`` backfill and ``chara-ext-asset_`` chunks; the ``type: "risu"`` lorebook), Agnai (native
``kind: "character"`` and ``kind: "memory"``), NovelAI lorebooks, TextGen/Pygmalion JSON and V1 cards.
Round trips must keep every field the source carried.
"""
from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "module_builtins" / "characters"

def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

def png_with_text(texts, width: int = 2, height: int = 2) -> bytes:
    """A real (decodable) RGB PNG with the given tEXt chunks placed before IEND."""
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += _chunk(b"IDAT", zlib.compress(raw))
    for key, value in texts:
        out += _chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value)
    return out + _chunk(b"IEND", b"")

def b64json(value) -> bytes:
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode("utf-8"))

ST_ENTRY_FULL = {
    "uid": 0, "key": ["Eldoria", "/eld(o|a)ria/i"], "keysecondary": ["kingdom"], "comment": "Eldoria",
    "content": "Eldoria is a kingdom of floating isles.", "constant": False, "vectorized": False, "selective": True,
    "selectiveLogic": 0, "addMemo": True, "order": 100, "position": 0, "disable": False, "ignoreBudget": False,
    "excludeRecursion": False, "preventRecursion": False, "matchPersonaDescription": False,
    "matchCharacterDescription": False, "matchCharacterPersonality": False, "matchCharacterDepthPrompt": False,
    "matchScenario": False, "matchCreatorNotes": False, "delayUntilRecursion": 0, "probability": 100,
    "useProbability": True, "depth": 4, "outletName": "", "group": "", "groupOverride": False, "groupWeight": 100,
    "scanDepth": None, "caseSensitive": None, "matchWholeWords": None, "useGroupScoring": None, "automationId": "",
    "role": 0, "sticky": None, "cooldown": None, "delay": None, "triggers": [], "displayIndex": 0,
}
ST_WORLD = {"entries": {
    "0": ST_ENTRY_FULL,
    "5": {**ST_ENTRY_FULL, "uid": 5, "key": ["airship"], "keysecondary": [], "comment": "Airships", "content": "Sky ships.",
          "constant": True, "position": 4, "depth": 2, "role": 1, "order": 50, "sticky": 3, "cooldown": 2, "delay": 1,
          "group": "transport", "groupWeight": 40, "characterFilter": {"isExclude": True, "names": ["Bob"], "tags": []},
          "triggers": ["normal", "continue"], "displayIndex": 1, "futureField": {"x": [1, 2]}},
    "7": {**ST_ENTRY_FULL, "uid": 7, "comment": "No filter", "characterFilter": None, "displayIndex": 2,
          "selectiveLogic": 3, "scanDepth": 6, "caseSensitive": True, "matchWholeWords": False, "outletName": "map"},
}}
CB_V2 = {
    "name": "Eldoria Lore", "description": "World notes", "scan_depth": 6, "token_budget": 900,
    "recursive_scanning": True, "extensions": {"risu_fullWordMatching": True},
    "entries": [
        {"id": 0, "keys": ["Eldoria"], "secondary_keys": ["kingdom"], "comment": "Eldoria", "content": "Floating isles.",
         "constant": False, "selective": True, "insertion_order": 100, "enabled": True, "position": "before_char",
         "use_regex": True, "name": "Eldoria", "priority": 10, "case_sensitive": False,
         "extensions": {"position": 0, "exclude_recursion": False, "display_index": 0, "probability": 100,
                        "useProbability": True, "depth": 4, "selectiveLogic": 0, "outlet_name": "", "group": "",
                        "group_override": False, "group_weight": 100, "prevent_recursion": False,
                        "delay_until_recursion": False, "scan_depth": None, "match_whole_words": None,
                        "use_group_scoring": False, "case_sensitive": False, "automation_id": "", "role": 0,
                        "vectorized": False, "sticky": 0, "cooldown": 0, "delay": 0,
                        "match_persona_description": False, "match_character_description": False,
                        "match_character_personality": False, "match_character_depth_prompt": False,
                        "match_scenario": False, "match_creator_notes": False, "triggers": [], "ignore_budget": False,
                        "risu_case_sensitive": False}},
        {"id": 3, "keys": ["guild"], "secondary_keys": [], "comment": "", "name": "Thieves guild",
         "content": "@@depth 2\nThe guild hides below.", "constant": False, "selective": False, "insertion_order": 7,
         "enabled": False, "position": "after_char", "extensions": {"depth": 4, "weight": 10, "addMemo": True,
         "displayIndex": 1, "useProbability": True, "characterFilter": None, "excludeRecursion": True,
         "linked": False, "embedded": True}, "probability": 100, "selectiveLogic": 0, "case_sensitive": False},
    ],
}
CHUB_HYBRID = {
    "name": "Data: ", "description": "", "is_creation": False, "scan_depth": 50, "token_budget": 511,
    "recursive_scanning": False, "extensions": {"chub": {"id": 782, "full_path": "bartleby/example-lorebook"}},
    "entries": {"1": {"uid": 1, "key": ["Lore", "Data"], "keysecondary": [], "comment": "", "content": "Data's memory.",
                      "constant": False, "selective": False, "selectiveLogic": 0, "order": 7, "position": 2,
                      "disable": False, "addMemo": True, "excludeRecursion": True, "probability": 100,
                      "displayIndex": 1, "useProbability": True, "secondary_keys": [], "keys": ["Lore", "Data"],
                      "id": 1, "priority": 7, "insertion_order": 7, "enabled": True, "name": "extrapolating",
                      "extensions": {"depth": 4, "weight": 10}, "case_sensitive": False, "depth": 4,
                      "characterFilter": None}},
}
NOVEL = {"lorebookVersion": 5, "entries": [{"text": "Novel text", "keys": ["nov"], "displayName": "Novel",
                                            "enabled": True, "contextConfig": {"budgetPriority": 400}}]}
AGNAI_BOOK = {"kind": "memory", "name": "Agnai book", "description": "d",
              "entries": [{"name": "Sword", "entry": "A sharp sword.", "keywords": ["sword", "blade"],
                           "priority": 5, "weight": 60, "enabled": True}]}
RISU_BOOK = {"type": "risu", "ver": 1, "data": [{"key": "castle, keep", "secondkey": "", "comment": "Castle",
                                                  "content": "A big castle.", "mode": "normal", "insertorder": 120,
                                                  "alwaysActive": False, "selective": False, "useRegex": False}]}
ST_CARD_JSON = {
    "name": "Mira", "description": "{{char}} is a sky pirate.", "personality": "bold", "scenario": "On deck.",
    "first_mes": "Ahoy, {{user}}!", "mes_example": "<START>\n{{user}}: Hi\n{{char}}: Ahoy", "creatorcomment": "notes",
    "avatar": "none", "chat": "Mira - 2024-1-1", "talkativeness": "0.5", "fav": False, "tags": ["pirate"],
    "spec": "chara_card_v2", "spec_version": "2.0", "create_date": "2024-01-01T00:00:00.000Z",
    "data": {"name": "Mira", "description": "{{char}} is a sky pirate.", "personality": "bold", "scenario": "On deck.",
             "first_mes": "Ahoy, {{user}}!", "mes_example": "<START>\n{{user}}: Hi\n{{char}}: Ahoy",
             "creator_notes": "notes", "system_prompt": "Stay in character.", "post_history_instructions": "Be brief.",
             "tags": ["pirate"], "creator": "anon", "character_version": "1.2",
             "alternate_greetings": ["Arr!", "Who goes there?"],
             "extensions": {"talkativeness": "0.5", "fav": False, "world": "Eldoria Lore",
                            "depth_prompt": {"prompt": "Mira swears a lot.", "depth": 4, "role": "system"},
                            "regex_scripts": [{"scriptName": "x", "findRegex": "/a/g", "replaceString": "b"}],
                            "custom_tool": {"k": 1}},
             "character_book": CB_V2},
}
RISU_V3 = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {
    "name": "Risu Girl", "description": "desc", "personality": "", "scenario": "", "first_mes": "Hello",
    "mes_example": "", "creator_notes": "cn", "system_prompt": "", "post_history_instructions": "",
    "alternate_greetings": [], "group_only_greetings": ["Hi all"], "tags": ["risu"], "creator": "kw",
    "character_version": "3", "nickname": "Ri", "creator_notes_multilingual": {"ko": "노트"},
    "source": ["https://realm.risuai.net/character/x"], "creation_date": 1700000000, "modification_date": 1700000500,
    "assets": [{"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"},
               {"type": "emotion", "uri": "__asset:0", "name": "happy", "ext": "png"},
               {"type": "background", "uri": "https://example.com/bg.png", "name": "bg", "ext": "png"}],
    "extensions": {"risuai": {"emotions": [["happy", "__asset:0"]], "bias": [], "utilityBot": False}},
    "character_book": {"name": "", "entries": [{"keys": ["moon"], "content": "@@activate\nThe moon is green.",
                                                "extensions": {"risu_case_sensitive": False}, "enabled": True,
                                                "insertion_order": 100, "use_regex": False, "constant": False,
                                                "selective": False, "secondary_keys": [], "comment": "Moon",
                                                "mode": "normal"}]}}}
RISU_V2_BACKFILL = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
    **{k: v for k, v in RISU_V3["data"].items() if k not in ("assets", "group_only_greetings", "nickname",
                                                             "creator_notes_multilingual", "source",
                                                             "creation_date", "modification_date")},
    "creator_notes": "This character card is Character Card V3, but it is loaded as a Character Card V2."}}
AGNAI_CHAR = {"kind": "character", "name": "Aggie", "greeting": "Hey {{user}}", "scenario": "A tavern",
              "sampleChat": "{{char}}: hey", "description": "Creator notes", "systemPrompt": "sys",
              "postHistoryInstructions": "phi", "alternateGreetings": ["Yo"], "tags": ["agnai"], "creator": "me",
              "characterVersion": "2", "insert": {"prompt": "Be kind", "depth": 3},
              "persona": {"kind": "wpp", "attributes": {"mind": ["kind", "smart"], "looks": ["tall"]}},
              "characterBook": AGNAI_BOOK, "avatar": "https://example.com/a.png"}
TEXTGEN = {"char_name": "Gradio", "char_persona": "persona", "char_greeting": "hi", "world_scenario": "world",
           "example_dialogue": "ex"}
V1 = {"name": "Old", "description": "d", "personality": "p", "scenario": "s", "first_mes": "f", "mes_example": "m",
      "tags": "a, b", "creator_notes": "c", "talkativeness": "0.7"}
PYG_EXPORT = {"character": {"spec": "chara_card_v2", "spec_version": "2.0",
                            "data": {"name": "Pyg", "description": "pd", "first_mes": "hey", "personality": "",
                                     "scenario": "", "mes_example": "", "avatar": "https://x/y.png",
                                     "extensions": {}, "tags": []}}}

SCRIPT = r"""
const assert = require('assert');
const fs = require('fs');
const E = require(process.argv[2]);
const S = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const clone = (x) => JSON.parse(JSON.stringify(x));
// Every key/value the source carries must be present and equal in the result (extra keys are allowed).
function contains(actual, expected, path) {
  path = path || '$';
  if (expected === null || typeof expected !== 'object') {
    assert.deepStrictEqual(actual, expected, path + ': ' + JSON.stringify(actual) + ' != ' + JSON.stringify(expected));
    return;
  }
  assert.ok(actual !== null && typeof actual === 'object', path + ' missing');
  if (Array.isArray(expected)) {
    assert.ok(Array.isArray(actual), path + ' not an array');
    assert.strictEqual(actual.length, expected.length, path + ' length');
    expected.forEach((v, i) => contains(actual[i], v, path + '[' + i + ']'));
    return;
  }
  Object.keys(expected).forEach((k) => contains(actual[k], expected[k], path + '.' + k));
}
const out = {};

// ---------- SillyTavern World Info file: exact round trip
let r = E.importWorld(clone(S.ST_WORLD), 'Eldoria');
assert.strictEqual(r.format, 'sillytavern'); assert.strictEqual(r.count, 3); assert.strictEqual(r.world.name, 'Eldoria');
let st = E.exportWorld(r.world);
assert.deepStrictEqual(st, S.ST_WORLD, 'ST world must round-trip exactly');
// twice through the store (JSON) and again
st = E.exportWorld(E.importWorld(JSON.parse(JSON.stringify(st)), 'Eldoria').world);
assert.deepStrictEqual(st, S.ST_WORLD);

// ---------- character_book (V2/V3, chub) -> world -> character_book keeps everything
r = E.importWorld(clone(S.CB_V2), 'x');
assert.strictEqual(r.format, 'character_book'); assert.strictEqual(r.world.name, 'Eldoria Lore');
const guild = r.world.entries[3];
assert.strictEqual(guild.comment, 'Thieves guild', 'chub keeps the title in name');
assert.strictEqual(guild.disable, true); assert.strictEqual(guild.excludeRecursion, true, 'camelCase extension read');
assert.strictEqual(guild.position, 1); assert.strictEqual(guild.order, 7);
assert.strictEqual(r.world.entries[0].sticky, 0);
contains(E.toCharacterBook(r.world), S.CB_V2);
// … and through the SillyTavern file format (originalData travels like in ST)
const viaSt = E.importWorld(JSON.parse(JSON.stringify(E.exportWorld(r.world))), 'Eldoria Lore');
assert.strictEqual(viaSt.format, 'sillytavern');
contains(E.toCharacterBook(viaSt.world), S.CB_V2);
// edits win over the preserved original
viaSt.world.entries[3].disable = false; viaSt.world.entries[3].excludeRecursion = false; viaSt.world.entries[3].key = ['guild', 'thieves'];
let back = E.toCharacterBook(viaSt.world);
let g = back.entries.find((x) => x.id === 3);
assert.strictEqual(g.enabled, true); assert.deepStrictEqual(g.keys, ['guild', 'thieves']);
assert.strictEqual(E.importWorld(clone(back)).world.entries[3].excludeRecursion, false, 'snake_case wins over stale camelCase');
// a fresh world (made in MemuAI) -> character_book -> world is identical in every field ST understands
const fresh = E.importWorld(clone(S.ST_WORLD), 'Fresh').world; delete fresh.originalData;
const cb = E.toCharacterBook(fresh);
const again = E.importWorld(clone(cb)).world;
Object.keys(fresh.entries).forEach((k) => {
  const a = E.normalizeEntry(fresh.entries[k], k), b = again.entries[k];
  ['key', 'keysecondary', 'comment', 'content', 'constant', 'selective', 'selectiveLogic', 'order', 'position', 'disable',
   'excludeRecursion', 'preventRecursion', 'probability', 'useProbability', 'depth', 'outletName', 'group', 'groupOverride',
   'groupWeight', 'scanDepth', 'caseSensitive', 'matchWholeWords', 'automationId', 'role', 'sticky', 'cooldown', 'delay',
   'triggers', 'ignoreBudget', 'vectorized'].forEach((f) => assert.deepStrictEqual(b[f], a[f], 'entry ' + k + ' field ' + f));
  if (a.characterFilter && a.characterFilter.names && a.characterFilter.names.length) assert.deepStrictEqual(b.characterFilter, a.characterFilter);
});

// ---------- chub
r = E.importWorld(clone(S.CHUB_HYBRID), 'example-lorebook');
assert.strictEqual(r.format, 'sillytavern');
assert.deepStrictEqual(r.world.entries[1].key, ['Lore', 'Data']); assert.strictEqual(r.world.entries[1].order, 7);
contains(E.exportWorld(r.world), S.CHUB_HYBRID);   // every field kept (missing ST fields get their defaults)
const chubBook = E.toCharacterBook(r.world);
assert.strictEqual(chubBook.scan_depth, 50); assert.strictEqual(chubBook.token_budget, 511);
const bookCard = { spec: 'chara_card_v2', spec_version: '2.0', data: { name: 'Example Lorebook', description: '', personality: '',
  first_mes: '', mes_example: '', scenario: '', character_book: clone(S.CB_V2) } };
assert.strictEqual(E.detectJson(bookCard), 'character');
assert.ok(E.isBookCard(bookCard));
r = E.importWorld(bookCard); assert.strictEqual(r.format, 'chub'); assert.strictEqual(r.count, 2);

// ---------- other lorebook formats (converted like SillyTavern)
r = E.importWorld(clone(S.NOVEL), 'Novel book'); assert.strictEqual(r.format, 'novelai');
assert.strictEqual(r.world.entries[0].content, 'Novel text'); assert.strictEqual(r.world.entries[0].order, 400);
r = E.importWorld(clone(S.AGNAI_BOOK)); assert.strictEqual(r.format, 'agnai'); assert.strictEqual(r.world.name, 'Agnai book');
assert.deepStrictEqual(r.world.entries[0].key, ['sword', 'blade']); assert.strictEqual(r.world.entries[0].order, 60);
const agBack = E.toAgnaiMemory(r.world); assert.strictEqual(agBack.kind, 'memory'); assert.strictEqual(agBack.entries[0].entry, 'A sharp sword.');
r = E.importWorld(clone(S.RISU_BOOK), 'Risu'); assert.strictEqual(r.format, 'risu');
assert.deepStrictEqual(r.world.entries[0].key, ['castle', 'keep']); assert.strictEqual(r.world.entries[0].order, 120);
assert.throws(() => E.importWorld({ foo: 1 }), /bad_lorebook/);
['ST_WORLD', 'CB_V2', 'NOVEL', 'AGNAI_BOOK', 'RISU_BOOK', 'CHUB_HYBRID'].forEach((k) => assert.strictEqual(E.detectJson(S[k]), 'lorebook', k));

// ---------- cards
let d = E.cardToData(clone(S.ST_CARD_JSON));
assert.strictEqual(d.name, 'Mira'); assert.strictEqual(d.system_prompt, 'Stay in character.');
assert.deepStrictEqual(d.alternate_greetings, ['Arr!', 'Who goes there?']);
assert.deepStrictEqual(d.extensions.custom_tool, { k: 1 }); assert.ok(d.character_book);
let v2 = E.dataToV2(d, null);
contains(v2.data, S.ST_CARD_JSON.data);                                  // every V2 field kept, book included
assert.strictEqual(v2.spec, 'chara_card_v2'); assert.strictEqual(v2.creatorcomment, 'notes');
// the book as a linked world (how the module stores it) gives the same character_book back
const linked = E.importWorld(clone(S.ST_CARD_JSON.data.character_book)).world;
v2 = E.dataToV2(Object.assign(clone(d), { character_book: undefined }), linked);
contains(v2.data.character_book, S.CB_V2);
assert.strictEqual(v2.data.extensions.world, 'Eldoria Lore');

// V3 (RisuAI): ccv3 wins over the chara backfill, V3 fields survive, embedded asset links are dropped
const png = Buffer.from(S.RISU_PNG_B64, 'base64');
const info = E.readCardInfo(new Uint8Array(png));
assert.strictEqual(info.chunk, 'ccv3'); assert.deepStrictEqual(info.chunks.sort(), ['ccv3', 'chara']);
assert.strictEqual(E.cardOrigin(info.json), 'risu');
d = E.cardToData(info.json);
assert.strictEqual(d.nickname, 'Ri'); assert.deepStrictEqual(d.group_only_greetings, ['Hi all']);
assert.deepStrictEqual(d.creator_notes_multilingual, { ko: '노트' }); assert.strictEqual(d.creation_date, 1700000000);
const v3 = E.dataToV3(d, null);
assert.strictEqual(v3.spec, 'chara_card_v3'); assert.strictEqual(v3.data.nickname, 'Ri');
assert.strictEqual(v3.data.creation_date, 1700000000); assert.strictEqual(v3.data.modification_date, 1700000500);
assert.deepStrictEqual(v3.data.assets.map((a) => a.uri), ['ccdefault:', 'https://example.com/bg.png']);
assert.ok(!('group_only_greetings' in E.dataToV2(d, null).data), 'no V3-only fields in the V2 chunk');
// write -> read: identical data object
const written = E.writeCardToPng(new Uint8Array(png), E.dataToV2(d, null), v3);
const reread = E.readCardInfo(written);
assert.strictEqual(reread.chunk, 'ccv3');
assert.deepStrictEqual(E.cardToData(reread.json), E.cardToData(v3));
assert.deepStrictEqual(E.readPngTextKeys ? 1 : 1, 1);
// the PNG keeps its other chunks (Risu asset chunk) and exactly one chara + one ccv3
const keys = [];
(function walk(b) { let p = 8; const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
  while (p < b.length) { const len = v.getUint32(p); const type = String.fromCharCode.apply(null, b.subarray(p + 4, p + 8));
    if (type === 'tEXt') { const z = b.indexOf(0, p + 8); keys.push(String.fromCharCode.apply(null, b.subarray(p + 8, z))); }
    p += 12 + len; } })(written);
assert.deepStrictEqual(keys.sort(), ['chara', 'chara-ext-asset_:0', 'ccv3'].sort());
// a chara-only PNG (SillyTavern V2) and a raw-JSON (non base64, UTF-8) chunk
assert.strictEqual(E.readCardInfo(new Uint8Array(Buffer.from(S.ST_PNG_B64, 'base64'))).chunk, 'chara');
assert.strictEqual(E.readCardFromPng(new Uint8Array(Buffer.from(S.RAW_PNG_B64, 'base64'))).name, 'Ünïcödé');
assert.throws(() => E.readCardInfo(new Uint8Array(Buffer.from(S.PLAIN_PNG_B64, 'base64'))), /no_card/);
assert.throws(() => E.readCardInfo(new Uint8Array(Buffer.from('not a png'))), /not_png/);

// Agnai native, TextGen, V1, Pygmalion wrapper
d = E.cardToData(clone(S.AGNAI_CHAR));
assert.strictEqual(d.name, 'Aggie'); assert.strictEqual(d.first_mes, 'Hey {{user}}');
assert.strictEqual(d.description, '[character("Aggie") {\nmind("kind" + "smart")\nlooks("tall")\n}]');
assert.strictEqual(d.creator_notes, 'Creator notes'); assert.strictEqual(d.extensions.depth_prompt.prompt, 'Be kind');
assert.strictEqual(d.extensions.depth_prompt.depth, 3); assert.strictEqual(d.character_book.entries[0].content, 'A sharp sword.');
assert.strictEqual(E.cardOrigin(S.AGNAI_CHAR), 'agnai'); assert.strictEqual(E.detectJson(S.AGNAI_CHAR), 'character');
d = E.cardToData(clone(S.TEXTGEN));
assert.deepStrictEqual([d.name, d.description, d.first_mes, d.scenario, d.mes_example], ['Gradio', 'persona', 'hi', 'world', 'ex']);
d = E.cardToData(clone(S.V1));
assert.deepStrictEqual(d.tags, ['a', 'b']); assert.strictEqual(d.extensions.talkativeness, 0.7); assert.ok(!('talkativeness' in d));
d = E.cardToData(clone(S.PYG_EXPORT)); assert.strictEqual(d.name, 'Pyg'); assert.strictEqual(d.first_mes, 'hey');
['ST_CARD_JSON', 'RISU_V3', 'TEXTGEN', 'V1', 'PYG_EXPORT'].forEach((k) => assert.strictEqual(E.detectJson(S[k]), k === 'PYG_EXPORT' ? '' : 'character', k));

// export -> import of our own card is stable (second generation identical)
d = E.cardToData(clone(S.ST_CARD_JSON)); delete d.character_book;
const gen1 = E.cardToData(E.dataToV3(d, linked, { created: 1700000000000, updated: 1700000100000 }));
const gen2 = E.cardToData(E.dataToV3(gen1, linked, { created: 1, updated: 2 }));
assert.deepStrictEqual(gen2, gen1);
process.stdout.write(JSON.stringify({ ok: true }));
"""

@unittest.skipUnless(shutil.which("node"), "node is required")
class CharactersImportTest(unittest.TestCase):
    def samples(self):
        st_png = png_with_text([("chara", b64json(ST_CARD_JSON))])
        risu_png = png_with_text([("chara", b64json(RISU_V2_BACKFILL)), ("ccv3", b64json(RISU_V3)),
                                  ("chara-ext-asset_:0", base64.b64encode(png_with_text([])))])
        raw_png = png_with_text([("chara", json.dumps({"name": "Ünïcödé", "first_mes": "x"},
                                                      ensure_ascii=False).encode("utf-8"))])
        return {
            "ST_WORLD": ST_WORLD, "CB_V2": CB_V2, "CHUB_HYBRID": CHUB_HYBRID, "NOVEL": NOVEL,
            "AGNAI_BOOK": AGNAI_BOOK, "RISU_BOOK": RISU_BOOK, "ST_CARD_JSON": ST_CARD_JSON, "RISU_V3": RISU_V3,
            "AGNAI_CHAR": AGNAI_CHAR, "TEXTGEN": TEXTGEN, "V1": V1, "PYG_EXPORT": PYG_EXPORT,
            "ST_PNG_B64": base64.b64encode(st_png).decode(), "RISU_PNG_B64": base64.b64encode(risu_png).decode(),
            "RAW_PNG_B64": base64.b64encode(raw_png).decode(),
            "PLAIN_PNG_B64": base64.b64encode(png_with_text([])).decode(),
        }

    def test_round_trips(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        start, end = source.index("/* @engine-start"), source.index("/* @engine-end */")
        engine = (ROOT / "app" / "pgstatic" / "tokens.js").read_text(encoding="utf-8") + "\n" + source[start:end] + "\nmodule.exports = CharEngine;\n"
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "engine.js").write_text(engine, encoding="utf-8")
            (Path(temp) / "run.js").write_text(SCRIPT, encoding="utf-8")
            (Path(temp) / "samples.json").write_text(json.dumps(self.samples(), ensure_ascii=False), encoding="utf-8")
            done = subprocess.run(["node", str(Path(temp) / "run.js"), str(Path(temp) / "engine.js"),
                                   str(Path(temp) / "samples.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-4000:])
        self.assertEqual(json.loads(done.stdout), {"ok": True})

if __name__ == "__main__":
    unittest.main()
