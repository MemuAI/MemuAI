# -*- coding: utf-8 -*-
"""Phase-2 wave D regressions for the «Characters» engine (Node) and shipped presets.

* the «Universal» preset file stays faithful (temperature 2); index.json carries the recommended effective value;
* nested macros ({{setvar::X::…{{getvar::Y}}…}}) are resolved inside-out, {{summary}} is empty;
* whitespace-only prompt messages are not sent («Engine» preset sent 56 of them);
* leaked control tokens (<｜begin▁of▁sentence｜>, <|im_end|>…) are detected;
* «Send the prefill» off also turns «Continue» into the nudge instead of an assistant prefill;
* the «Engine» preset builds without macro leftovers.
"""
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

// nested macros
let ctx = { user: 'Bob', char: 'Alice', variables: { Language: 'Russian' }, globals: {} };
assert.strictEqual(E.substitute('A{{setvar::Cfg::Write in {{getvar::Language}} as {{char}}.}}B[{{getvar::Cfg}}]', ctx), 'AB[Write in Russian as Alice.]');
assert.strictEqual(ctx.variables.Cfg, 'Write in Russian as Alice.');
assert.strictEqual(E.substitute('{{getvar::{{char}}}}', { user: 'Bob', char: 'Alice', variables: { Alice: 'ok' }, globals: {} }), 'ok');
assert.strictEqual(E.substitute('x {{summary}}y', ctx), 'x y');
assert.strictEqual(E.substitute('stray }} and {{ braces {{user}}', ctx), 'stray }} and {{ braces Bob');

// leaked control tokens
assert.strictEqual(E.findLeakedToken('home.<｜begin▁of▁sentence｜># Increasingly'), 5);
assert.strictEqual(E.findLeakedToken('ok<|im_end|>'), 2);
assert.strictEqual(E.findLeakedToken('ok<|eot_id|>'), 2);
assert.strictEqual(E.findLeakedToken('ok<|endoftext|>'), 2);
assert.strictEqual(E.findLeakedToken('a normal <b>reply</b> with {{macros}} and <|not a token|>'), -1);

const char = { name: 'Alice', description: '{{char}} is a witch.', personality: '', scenario: '', first_mes: 'Hi {{user}}', mes_example: '' };
const chat = [
  { is_user: false, name: 'Alice', mes: 'Hi Bob' },
  { is_user: true, name: 'Bob', mes: 'Tell me a story' },
  { is_user: false, name: 'Alice', mes: 'Once upon a time' }
];

// «Engine»: no blank messages, no macro leftovers
const eng = E.buildPrompt({ type: 'normal', preset: P('engine'), char, charName: 'Alice', user: 'Bob', chat: chat.slice(0, 2), meta: {}, settings: { postProcessing: 'none' }, lang: 'en' });
assert.ok(eng.messages.length > 3);
eng.messages.forEach((m, i) => { if (typeof m.content === 'string') assert.ok(/\S/.test(m.content), 'blank message #' + i); });
const engText = eng.messages.map((m) => String(m.content)).join('\n');
assert.ok(!engText.includes('{{') && !engText.includes('}}'), 'engine macro leftovers: ' + (engText.match(/.{0,80}(\{\{|\}\}).{0,40}/s) || [''])[0]);
// «Storyteller»: {{summary}} resolved
const st = E.buildPrompt({ type: 'normal', preset: P('storyteller'), char, charName: 'Alice', user: 'Bob', chat: chat.slice(0, 2), meta: {}, settings: { postProcessing: 'none' }, lang: 'en' });
assert.ok(!st.messages.map((m) => String(m.content)).join('\n').includes('{{'));

// Continue: universal has continue_prefill; with «Send the prefill» off the nudge is used instead
const uni = P('universal');
assert.strictEqual(uni.continue_prefill, true);
const c1 = E.buildPrompt({ type: 'continue', preset: uni, char, charName: 'Alice', user: 'Bob', chat, meta: {}, settings: { postProcessing: 'none' } });
assert.strictEqual(c1.messages[c1.messages.length - 1].role, 'assistant');
assert.strictEqual(c1.messages[c1.messages.length - 1].content, 'Once upon a time');
const c2 = E.buildPrompt({ type: 'continue', preset: uni, char, charName: 'Alice', user: 'Bob', chat, meta: {}, settings: { postProcessing: 'none', sendPrefill: false } });
const last2 = c2.messages[c2.messages.length - 1];
assert.ok(String(last2.content).includes('cut off'), 'continue nudge expected, got ' + last2.role + ': ' + String(last2.content).slice(0, 80));
assert.ok(c2.messages.some((m) => m.role === 'assistant' && m.content === 'Once upon a time'));
assert.strictEqual(c1.params.temperature, 2);  // the file is faithful; the UI applies index.json «recommended»
console.log('ok');
"""


@unittest.skipUnless(shutil.which("node"), "node is required")
class CharactersPhase2DEngineTest(unittest.TestCase):
    def test_engine(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        start, end = source.index("/* @engine-start"), source.index("/* @engine-end */")
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "engine.js").write_text((ROOT / "app" / "pgstatic" / "tokens.js").read_text(encoding="utf-8") + "\n" + source[start:end] + "\nmodule.exports = CharEngine;\n", encoding="utf-8")
            (Path(temp) / "run.js").write_text(SCRIPT, encoding="utf-8")
            done = subprocess.run(["node", str(Path(temp) / "run.js"), str(Path(temp) / "engine.js"),
                                   str(MODULE / "assets" / "presets")], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)


class CharactersPresetFaithfulTest(unittest.TestCase):
    def test_universal_is_faithful_with_recommended_override(self):
        uni = json.loads((MODULE / "assets" / "presets" / "universal.json").read_text(encoding="utf-8"))
        self.assertEqual(uni["temperature"], 2)
        index = json.loads((MODULE / "assets" / "presets" / "index.json").read_text(encoding="utf-8"))
        row = next(r for r in index["presets"] if r["id"] == "universal")
        self.assertEqual(row.get("recommended"), {"temperature": 1})

    def test_i18n_keys(self):
        i18n = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        for key in ("temp_override_note", "temp_use_preset", "temp_high_note", "temp_set_recommended"):
            self.assertIn(key, i18n["ru"])
            self.assertIn(key, i18n["en"])
            self.assertNotRegex(i18n["en"][key], "[А-Яа-яЁё]")

    def test_layout_rules(self):
        css = (MODULE / "style.css").read_text(encoding="utf-8")
        self.assertIn(".ch-main{grid-column:2;grid-row:1;", css)        # ≤760px: the chat no longer lands in the 0px column
        self.assertIn("@media (max-width:1199px)", css)                  # drawer overlays the chat when it would squeeze it
        self.assertIn(".ch-persona-chip img{width:26px;height:26px;flex-shrink:0", css)


if __name__ == "__main__":
    unittest.main()
