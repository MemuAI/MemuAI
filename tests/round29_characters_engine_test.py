# -*- coding: utf-8 -*-
"""Round 29: the pure engine of the «Characters» module (macros, World Info, SillyTavern prompt builder,
cards, chat files), extracted from main.js (@engine-start … @engine-end) and run in Node."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "module_builtins" / "characters"

SCRIPT = r"""
const assert = require('assert');
const fs = require('fs');
const E = require(process.argv[2]);
const presetDir = process.argv[3];
const P = (id) => JSON.parse(fs.readFileSync(presetDir + '/' + id + '.json', 'utf8'));
const out = {};

// ---------- macros
const ctx = { user: 'Bob', char: 'Alice', description: '{{char}} likes {{user}}', variables: {}, globals: {} };
assert.strictEqual(E.substitute('{{description}}', ctx), 'Alice likes Bob');                 // user/char last, like ST
assert.strictEqual(E.substitute('{{setvar::mood::happy}}I am {{getvar::mood}}', ctx), 'I am happy');
assert.strictEqual(ctx.variables.mood, 'happy');
assert.strictEqual(E.substitute('a\n{{trim}}\nb', ctx), 'ab');
assert.strictEqual(E.substitute('x{{// hidden comment }}y', ctx), 'xy');
assert.ok(['a', 'b'].includes(E.substitute('{{random::a::b}}', ctx)));
assert.ok(['a', 'b'].includes(E.substitute('{{random:a,b}}', ctx)));
const roll = Number(E.substitute('{{roll:1d6}}', ctx)); assert.ok(roll >= 1 && roll <= 6);
assert.strictEqual(E.substitute('{{addvar::n::2}}{{addvar::n::3}}{{getvar::n}}', ctx), '5');
assert.strictEqual(E.substitute('{{incvar::k}}', ctx), '1');
assert.strictEqual(E.substitute('{{reverse:abc}}', ctx), 'cba');
assert.strictEqual(E.substitute('<USER> and <BOT>', ctx), 'Bob and Alice');
assert.strictEqual(E.substitute('{{isodate}}', ctx).length, 10);
const pick1 = E.substitute('{{pick::x::y::z}}', Object.assign({}, ctx, { chatId: 'c1' }));
assert.strictEqual(E.substitute('{{pick::x::y::z}}', Object.assign({}, ctx, { chatId: 'c1' })), pick1);  // stable per chat

// ---------- World Info
const world = { id: 'w', name: 'W', source: 'global', entries: {
  0: { uid: 0, key: ['dragon'], content: 'Dragons breathe fire.', position: 0, order: 100 },
  1: { uid: 1, key: ['fire'], content: 'Fire is hot.', position: 1, order: 50 },                     // recursion from 0
  2: { uid: 2, constant: true, content: 'Always on.', position: 1, order: 10 },
  3: { uid: 3, key: ['castle'], keysecondary: ['king'], selectiveLogic: 3, content: 'AND ALL', position: 0 },
  4: { uid: 4, key: ['castle'], keysecondary: ['queen'], selectiveLogic: 2, content: 'NOT ANY', position: 0 },
  5: { uid: 5, key: ['/dr[a-z]+n/i'], content: 'Regex hit', position: 4, depth: 1, role: 1 },
  6: { uid: 6, key: ['dragon'], content: 'Disabled', disable: true },
  7: { uid: 7, key: ['sword'], content: 'Too deep', position: 0 },
}};
const wi = E.checkWorldInfo({ chatLines: ['I see a DRAGON at the castle with the king', 'hello', 'a sword'], worlds: [world],
  settings: { depth: 2, recursive: true }, maxContext: 8000, meta: {} });
assert.ok(wi.worldInfoBefore.includes('Dragons breathe fire.'));
assert.ok(wi.worldInfoAfter.includes('Fire is hot.'), 'recursion activates entry 1');
assert.ok(wi.worldInfoAfter.includes('Always on.'));
assert.ok(wi.worldInfoBefore.includes('AND ALL'));
assert.ok(wi.worldInfoBefore.includes('NOT ANY'));
assert.ok(!wi.worldInfoBefore.includes('Disabled'));
assert.ok(!wi.worldInfoBefore.includes('Too deep'), 'scan depth 2 ignores the third message');
assert.deepStrictEqual(wi.WIDepthEntries, [{ depth: 1, entries: ['Regex hit'], role: 1 }]);
// sticky
const meta = {};
const stickyWorld = { id: 's', name: 'S', source: 'global', entries: { 0: { uid: 0, key: ['magic'], content: 'Sticky', sticky: 2 } } };
let r = E.checkWorldInfo({ chatLines: ['magic'], worlds: [stickyWorld], settings: {}, maxContext: 8000, meta, chatLength: 1 });
assert.ok(r.worldInfoBefore.includes('Sticky'));
r = E.checkWorldInfo({ chatLines: ['nothing'], worlds: [stickyWorld], settings: {}, maxContext: 8000, meta, chatLength: 2 });
assert.ok(r.worldInfoBefore.includes('Sticky'), 'sticky keeps it active');
r = E.checkWorldInfo({ chatLines: ['nothing'], worlds: [stickyWorld], settings: {}, maxContext: 8000, meta, chatLength: 4 });
assert.ok(!r.worldInfoBefore.includes('Sticky'), 'sticky expires');
// character_book conversion round trip
const book = E.toCharacterBook(E.normalizeWorld(world));
const back = E.normalizeWorld(book, 'W');
assert.strictEqual(Object.keys(back.entries).length, 8);
assert.strictEqual(back.entries[5].depth, 1);

// ---------- prompt builder (universal preset = the default)
const char = { name: 'Alice', description: '{{char}} is a witch.', personality: 'kind', scenario: 'A tower', first_mes: 'Hi',
  mes_example: '<START>\n{{user}}: hey\n{{char}}: hello', system_prompt: '', post_history_instructions: '',
  extensions: { depth_prompt: { prompt: 'DEPTH NOTE', depth: 0, role: 'system' } } };
const chat = [{ is_user: false, name: 'Alice', mes: 'Hi' }, { is_user: true, name: 'Bob', mes: 'Tell me about the dragon' }];
const built = E.buildPrompt({ type: 'normal', preset: P('universal'), char, charName: 'Alice', user: 'Bob',
  persona: { description: 'Bob is a knight', position: 0 }, chat, worlds: [world], meta: {}, note: { text: 'AN!', position: 'chat', depth: 1, interval: 1 },
  settings: { postProcessing: 'none' }, lang: 'en' });
const msgs = built.messages;
assert.strictEqual(msgs[0].role, 'system');
const sys = msgs[0].content;
const idx = (s) => sys.indexOf(s);
assert.ok(idx('<OBJECTIVE_AND_PERSONA>') === 0, 'preset order: Read-Me (comment) then OBJECTIVE first');
assert.ok(idx('Dragons breathe fire.') > idx('<world>') && idx('Dragons breathe fire.') < idx('</world>'), 'WI before inside <world>');
assert.ok(idx('Bob is a knight') > idx('<protagonist') && idx('Bob is a knight') < idx('</protagonist>'), 'persona in its marker');
assert.ok(idx('Alice is a witch.') > idx('<characters') && idx('Alice is a witch.') < idx('</characters>'));
assert.ok(idx('Bob: hey') > idx('<FEW_SHOT_EXAMPLES>'), '{{mesExamplesRaw}} resolved with names');
assert.ok(!sys.includes('{{'), 'no unresolved macros');
// depth 0 injections are after the last message, AN at depth 1 before it
const roles = msgs.map((m) => m.role + ':' + String(m.content).slice(0, 12));
const lastUser = msgs.findIndex((m) => m.content === 'Tell me about the dragon');
assert.ok(lastUser > 0);
assert.ok(msgs.slice(lastUser + 1).some((m) => String(m.content).includes('DEPTH NOTE')), 'depth 0 after last message: ' + roles.join(' | '));
assert.ok(msgs.slice(0, lastUser).some((m) => String(m.content).includes('AN!')), 'AN at depth 1 before last message');
assert.ok(msgs.some((m) => m.role === 'user' && String(m.content).includes('Regex hit')), 'WI at depth with role user');
assert.strictEqual(built.params.temperature, 2);  // the universal preset file stays faithful (2); the UI applies the recommended 1.0
assert.strictEqual(built.params.effort, 'max');
assert.ok(built.tokens > 100);

// impersonate adds the impersonation prompt at the very end
const imp = E.buildPrompt({ type: 'impersonate', preset: P('universal'), char, charName: 'Alice', user: 'Bob', chat, meta: {}, settings: { postProcessing: 'none' } });
assert.ok(String(imp.messages[imp.messages.length - 1].content).includes("respond as the user's Bob"));
// continue with continue_prefill = true: the continued message is last, as assistant
const cont = E.buildPrompt({ type: 'continue', preset: P('universal'), char, charName: 'Alice', user: 'Bob',
  chat: chat.concat([{ is_user: false, name: 'Alice', mes: 'Once upon' }]), meta: {}, settings: { postProcessing: 'none' } });
assert.strictEqual(cont.messages[cont.messages.length - 1].role, 'assistant');
assert.strictEqual(cont.messages[cont.messages.length - 1].content, 'Once upon');
// continue without prefill (classic preset): nudge at the end
const cont2 = E.buildPrompt({ type: 'continue', preset: P('classic'), char, charName: 'Alice', user: 'Bob',
  chat: chat.concat([{ is_user: false, name: 'Alice', mes: 'Once upon' }]), meta: {}, settings: { postProcessing: 'none' } });
const tail = cont2.messages.slice(-3).map((m) => String(m.content)).join('\n');
assert.ok(tail.includes('Once upon'), tail);

// every shipped preset builds without unresolved markers and with the history inside
for (const id of ['universal', 'storyteller', 'engine', 'classic', 'megumin']) {
  const b = E.buildPrompt({ type: 'normal', preset: P(id), char, charName: 'Alice', user: 'Bob', chat, meta: {}, settings: {} });
  assert.ok(b.messages.length >= 2, id);
  assert.ok(b.messages.some((m) => String(m.content).includes('Tell me about the dragon')), id + ' has the last message');
  out[id] = { messages: b.messages.length, tokens: b.tokens };
}
// token budget trims old history
const long = []; for (let i = 0; i < 400; i++) long.push({ is_user: i % 2 === 1, name: i % 2 ? 'Bob' : 'Alice', mes: 'word '.repeat(200) + i });
const small = Object.assign(P('classic'), { openai_max_context: 6000, openai_max_tokens: 500 });
const trimmed = E.buildPrompt({ type: 'normal', preset: small, char, charName: 'Alice', user: 'Bob', chat: long, meta: {}, settings: {} });
assert.ok(trimmed.warnings.includes('history_trimmed'));
assert.ok(trimmed.tokens <= 6000, String(trimmed.tokens));
assert.ok(String(trimmed.messages[trimmed.messages.length - 1].content).includes('399') || trimmed.messages.some((m) => String(m.content).includes('399')));
// card override of the main prompt with {{original}}
const ov = E.buildPrompt({ type: 'normal', preset: P('classic'), char: Object.assign({}, char, { system_prompt: 'CARD MAIN. {{original}}' }), charName: 'Alice', user: 'Bob', chat, meta: {}, settings: { postProcessing: 'none' } });
assert.ok(ov.messages.some((m) => String(m.content).includes('CARD MAIN.')));
// post-processing
assert.deepStrictEqual(E.postProcess([{ role: 'system', content: 'a' }, { role: 'system', content: 'b' }, { role: 'user', content: 'c' }], 'merge').map((m) => m.content), ['a\n\nb', 'c']);
assert.strictEqual(E.postProcess([{ role: 'system', content: 'a' }, { role: 'user', content: 'u' }, { role: 'system', content: 'b' }], 'semi')[1].content, 'u\n\nb');
assert.strictEqual(E.postProcess([{ role: 'system', content: 'a' }, { role: 'user', content: 'b' }], 'single').length, 1);

// ---------- presets
const norm = E.normalizePreset(P('universal'));
assert.ok(E.presetOrder(norm).length > 30);
const exported = E.exportPreset(norm);
assert.ok(exported.prompt_order.some((o) => o.character_id === 100001));
assert.ok(E.normalizePreset({}).prompts.some((p) => p.identifier === 'chatHistory'));

// ---------- cards (PNG chara + ccv3)
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const data = E.cardToData({ spec: 'chara_card_v2', data: { name: 'Zoë', description: 'Ünïcode ✓ описание', alternate_greetings: ['Hey'], tags: ['a'],
  character_book: { entries: [{ keys: ['k'], content: 'lore', insertion_order: 5, enabled: true, extensions: { position: 4, depth: 2 } }] } } });
const withCard = E.writeCardToPng(new Uint8Array(png), E.dataToV2(data), E.dataToV3(data));
const read = E.readCardFromPng(withCard);
assert.strictEqual(read.spec, 'chara_card_v3');
assert.strictEqual(read.data.name, 'Zoë');
assert.strictEqual(read.data.description, 'Ünïcode ✓ описание');
assert.strictEqual(read.data.character_book.entries[0].extensions.depth, 2);
assert.ok(E.isPng(withCard));
// a second write replaces the chunks instead of stacking them
const twice = E.writeCardToPng(withCard, E.dataToV2(Object.assign({}, data, { name: 'New' })), null);
assert.strictEqual(E.readCardFromPng(twice).data.name, 'New');
// V1 card
assert.strictEqual(E.cardToData({ name: 'Old', char_persona: 'p', char_greeting: 'g' }).first_mes, 'g');

// ---------- chat .jsonl
const jsonl = E.chatToJsonl({ created: 1, messages: [{ role: 'user', mes: 'hi', date: 1 }, { role: 'assistant', mes: 'yo', swipes: ['yo', 'hey'], swipe_id: 0, date: 2, reasoning: 'r' }] }, 'Bob', 'Alice');
const parsed = E.jsonlToChat(jsonl);
assert.strictEqual(parsed.header.user_name, 'Bob');
assert.strictEqual(parsed.messages.length, 2);
assert.deepStrictEqual(parsed.messages[1].swipes, ['yo', 'hey']);
assert.strictEqual(parsed.messages[1].reasoning, 'r');

// ---------- reasoning, regex, tokens
assert.deepStrictEqual(E.splitReasoning('<think>plan</think>\nAnswer'), { text: 'Answer', reasoning: 'plan', open: false });
assert.strictEqual(E.splitReasoning('<think>still').open, true);
assert.strictEqual(E.runRegexScripts('hello world', [{ findRegex: '/world/g', replaceString: 'there', placement: [2] }], 2, {}), 'hello there');
assert.strictEqual(E.runRegexScripts('hello world', [{ findRegex: '/world/g', replaceString: 'there', placement: [2], markdownOnly: true }], 2, {}), 'hello world');
assert.ok(E.estimateTokens('The quick brown fox jumps over the lazy dog.') >= 9 && E.estimateTokens('The quick brown fox jumps over the lazy dog.') <= 14);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is required")
class CharactersEngineTest(unittest.TestCase):
    def test_engine(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        start, end = source.index("/* @engine-start"), source.index("/* @engine-end */")
        engine = (ROOT / "app" / "pgstatic" / "tokens.js").read_text(encoding="utf-8") + "\n" + source[start:end] + "\nmodule.exports = CharEngine;\n"
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "engine.js").write_text(engine, encoding="utf-8")
            (Path(temp) / "run.js").write_text(SCRIPT, encoding="utf-8")
            done = subprocess.run(["node", str(Path(temp) / "run.js"), str(Path(temp) / "engine.js"),
                                   str(MODULE / "assets" / "presets")], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        stats = json.loads(done.stdout)
        self.assertEqual(set(stats), {"universal", "storyteller", "engine", "classic", "megumin"})

    def test_presets_are_clean_and_renamed(self):
        index = json.loads((MODULE / "assets" / "presets" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["default"], "universal")
        for row in index["presets"]:
            data = json.loads((MODULE / "assets" / "presets" / f"{row['id']}.json").read_text(encoding="utf-8"))
            for junk in ("custom_url", "reverse_proxy", "proxy_password", "openrouter_model", "chat_completion_source"):
                self.assertNotIn(junk, data, row["id"])
            self.assertTrue(data["prompts"] and data["prompt_order"], row["id"])
            self.assertFalse(any("[А-Яа-я]" in ch for ch in row["name_en"]))


if __name__ == "__main__":
    unittest.main()
