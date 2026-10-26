# -*- coding: utf-8 -*-
"""Round 33 (#7, r33-characters / r33c-characters): the «Characters» module.

* /api/pg/modules/models — models switched off in MemuAI Proxy, models of a dead provider and hidden models are not
  listed (the module used to offer and call them); readable names (app/model_names), ``badges``, per-model
  ``reasoning_levels`` and ``reasoning_default``.
* modules_host «/* @memu-part:ai-create.js */» — the part is inlined into the module document and the export.
* characters_api.build_completion passes the new reasoning levels (off / on / xhigh) to the Proxy.
* settings keys (variantCount / variantLeftClick), manifest permissions.
* A browser flow against a fake upstream (tests/round29_characters_harness): the composer model picker (readable
  name + full id, search, keyboard), the reasoning chip built from the model's own levels, the sent ``effort``,
  visible «Regenerate», N parallel variants from the right-click menu, «Create with AI» preview → saved character,
  ai JSON parsing/normalisation, no console errors.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r33-characters-")

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "module_builtins" / "characters"

ADMIN_MODELS = [
    {"id": "reform-qwen3.8-flash", "provider": "custom", "prefix": "reform", "model": "qwen3.8-flash", "enabled": True,
     "caps": {"thinking": False}},
    {"id": "reform-glm-5.3", "provider": "custom", "prefix": "reform", "model": "glm-5.3", "enabled": False,
     "caps": {}},
    {"id": "dead-gpt-4o", "provider": "custom", "prefix": "dead", "model": "gpt-4o", "enabled": True,
     "caps": {}},
    {"id": "reform-kimi-k3", "provider": "custom", "prefix": "reform", "model": "kimi-k3", "enabled": True,
     "caps": {}},
    {"id": "xkiro-qwen/qwen3.8-max:free", "provider": "custom", "prefix": "xkiro", "model": "qwen/qwen3.8-max:free",
     "enabled": True, "caps": {}},
]
PG_STATE = {
    "default_model": "reform-qwen3.8-flash",
    "hidden_models": ["reform-kimi-k3"],
    "image_models": ["reform-wanx2.1-t2i-turbo"],
    "models": [
        {"id": "reform-qwen3.8-flash", "name": "Qwen3.8 Flash", "badges": [], "provider_label": "REFORM",
         "caps": {"reasoning": True, "reasoning_profile": {"kind": "levels", "options": ["auto", "off", "low", "medium", "high"],
                                                             "default": "medium"}}},
        {"id": "reform-kimi-k3", "name": "Kimi K3", "caps": {}},
        {"id": "xkiro-qwen/qwen3.8-max:free", "badges": ["free"], "provider_label": "XKIRO", "caps": {}},
    ],
}

class _Proxy(BaseHTTPRequestHandler):
    state_ok = True

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/admin/models"):
            body, code = {"models": ADMIN_MODELS}, 200
        elif self.path.startswith("/api/admin/pg/state") and _Proxy.state_ok:
            body, code = PG_STATE, 200
        else:
            body, code = {"error": "not found"}, 404
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

class ModulesModelsEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls._env = os.environ.get("MEMUAI_PROXY_URL")
        os.environ["MEMUAI_PROXY_URL"] = f"http://127.0.0.1:{cls.server.server_address[1]}"
        from app import playground_api
        cls.client = TestClient(playground_api.create_app(), base_url="http://127.0.0.1").__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.server.shutdown()
        cls.server.server_close()
        if cls._env is None:
            os.environ.pop("MEMUAI_PROXY_URL", None)
        else:
            os.environ["MEMUAI_PROXY_URL"] = cls._env

    def rows(self):
        response = self.client.post("/api/pg/modules/models", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return {row["id"]: row for row in response.json()["models"]}

    def test_disabled_dead_and_hidden_models_are_not_listed(self):
        _Proxy.state_ok = True
        rows = self.rows()
        self.assertIn("reform-qwen3.8-flash", rows)
        self.assertNotIn("reform-glm-5.3", rows, "switched off in Proxy")
        self.assertNotIn("dead-gpt-4o", rows, "provider without an account (not in pg/state)")
        self.assertNotIn("reform-kimi-k3", rows, "hidden in MemuAI")
        self.assertTrue(rows["reform-wanx2.1-t2i-turbo"]["image"], "image models stay for Studio/Design")

    def test_readable_names_badges_and_reasoning(self):
        _Proxy.state_ok = True
        rows = self.rows()
        qwen = rows["reform-qwen3.8-flash"]
        self.assertEqual(qwen["name"], "Qwen3.8 Flash")
        self.assertEqual(qwen["reasoning_levels"], ["auto", "off", "low", "medium", "high"])
        self.assertEqual(qwen["reasoning_default"], "medium")
        self.assertTrue(qwen["reasoning"])
        self.assertTrue(qwen["default"])
        self.assertEqual(qwen["provider_label"], "REFORM")
        free = rows["xkiro-qwen/qwen3.8-max:free"]
        self.assertEqual(free["name"], "Qwen3.8 Max", "model_names fallback when pg/state has no name")
        self.assertEqual(free["badges"], ["free"])
        self.assertEqual(free["reasoning_levels"], [])
        self.assertNotEqual(rows["reform-wanx2.1-t2i-turbo"]["name"], "reform-wanx2.1-t2i-turbo")

    def test_without_pg_state_the_catalogue_still_drops_disabled_models(self):
        _Proxy.state_ok = False
        try:
            rows = self.rows()
        finally:
            _Proxy.state_ok = True
        self.assertNotIn("reform-glm-5.3", rows)
        self.assertIn("reform-qwen3.8-flash", rows)
        self.assertEqual(rows["reform-qwen3.8-flash"]["name"], "Qwen3.8 Flash")

class PackageTest(unittest.TestCase):
    def test_ai_create_part_is_inlined(self):
        from app.modules_host import load_package, module_document
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        self.assertIn("/* @memu-part:ai-create.js */", source)
        with tempfile.TemporaryDirectory() as temp:
            package = load_package(Path(temp), "characters")
        js = package["js"]
        self.assertNotIn("/* @memu-part:", js)
        self.assertIn("/* @memu-inlined-part:ai-create.js */", js)
        self.assertIn("function openAiCreator(", js)
        doc = module_document(package, {}, env={"lang": "en", "theme": "dark"})
        self.assertIn("function aiParseJson(", doc)

    def test_part_marker_accepts_only_plain_file_names(self):
        from app import modules_host
        self.assertIsNotNone(modules_host._PART_RE.search("/* @memu-part:ai-create.js */"))
        for bad in ("/* @memu-part:../evil.js */", "/* @memu-part:a/b.js */", "/* @memu-part:x.py */"):
            self.assertIsNone(modules_host._PART_RE.search(bad), bad)

    def test_manifest_permissions(self):
        manifest = json.loads((MODULE / "manifest.json").read_text(encoding="utf-8"))
        perms = manifest.get("permissions") or []
        for perm in ("models", "web", "media"):
            self.assertIn(perm, perms)

    def test_settings_defaults(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        self.assertIn("variantCount: 3, variantLeftClick: false", source)
        self.assertIn("clamp(parseInt(S.settings.ui.variantCount, 10) || 3, 1, 5)", source)

    def test_i18n_keys_in_both_languages(self):
        data = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        for key in ("effort_xhigh", "effort_on", "effort_default_hint", "variants_title", "ai_char_btn", "ai_book_btn"):
            self.assertIn(key, data["ru"])
            self.assertIn(key, data["en"])
        import re
        cyr = re.compile("[А-Яа-яЁё]")
        bad = [k for k, v in data["en"].items() if cyr.search(json.dumps(v, ensure_ascii=False))]
        self.assertEqual(bad, [], "Cyrillic in the English UI")

    def test_completion_passes_new_reasoning_levels(self):
        from app.characters_api import build_completion
        for level in ("off", "on", "xhigh", "minimal", "max"):
            body = build_completion({"model": "m", "messages": [{"role": "user", "content": "hi"}],
                                     "params": {"effort": level}})
            self.assertEqual(body.get("effort"), level)
        body = build_completion({"model": "m", "messages": [{"role": "user", "content": "hi"}], "params": {"effort": "turbo"}})
        self.assertNotIn("effort", body)

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

from tests import round29_characters_harness as harness  # noqa: E402

MODELS_JS = json.dumps([
    {"id": "reform-qwen3.8-flash", "name": "Qwen3.8 Flash", "provider": "custom", "provider_label": "REFORM",
     "reasoning": True, "reasoning_levels": ["auto", "off", "low", "medium", "high"], "reasoning_default": "medium",
     "context": 128000, "default": True, "badges": []},
    {"id": "aws9-gpt-6.1-sol", "name": "GPT-6.1 Sol", "provider": "custom", "provider_label": "AWS9", "vision": True,
     "reasoning": True, "reasoning_levels": ["auto", "low", "medium", "high", "xhigh"], "context": 400000, "badges": []},
    {"id": "xkiro-qwen/qwen3.8-max:free", "name": "Qwen3.8 Max", "provider": "custom", "provider_label": "XKIRO",
     "reasoning": False, "reasoning_levels": [], "context": 32000, "badges": ["free"]},
    {"id": "reform-wanx2.1-t2i-turbo", "name": "Wanx2.1 T2I Turbo", "image": True},
])

CARD_JSON = json.dumps({
    "name": "Lyra Vale", "description": "{{char}} is a wandering cartographer.", "personality": "curious, dry humour",
    "scenario": "A rainy harbour town.", "first_mes": "*unrolls a map* Lost, {{user}}?",
    "alternate_greetings": ["Need a guide?"], "mes_example": "<START>\n{{char}}: Maps don't lie.",
    "tags": ["Fantasy", "Original"], "system_prompt": "", "creator_notes": "made by AI",
})

PARENT = harness.PARENT.replace(
    'case "models": return reply(d.id, [{ id: "test/model-a", name: "Model A", context: 128000 }, { id: "img/x", name: "Img", image: true }]);',
    'case "models": return reply(d.id, __MODELS__);',
).replace(
    "const text = window.__reply;",
    "window.__n = (window.__n || 0) + 1; const text = window.__replyFor ? window.__replyFor(p, window.__n) : window.__reply;",
).replace("__MODELS__", MODELS_JS)

def open_page(pw, lang="en", theme="dark", size=(1360, 860)):
    browser = pw.chromium.launch(executable_path=harness.chromium(), args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    doc = harness.module_html(lang, theme)
    parent = PARENT.replace("__ASSETS__", json.dumps(harness.assets(), ensure_ascii=False))
    errors: list = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.route("http://harness.local/", lambda route: route.fulfill(status=200, content_type="text/html", body=parent))
    page.route("http://harness.local/module", lambda route: route.fulfill(status=200, content_type="text/html", body=doc))
    page.goto("http://harness.local/")
    return browser, page, errors

@unittest.skipUnless(sync_playwright and harness.chromium(), "playwright + chromium required")
class CharactersR33BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert '__MODELS__' not in PARENT and "window.__replyFor" in PARENT, "harness changed: update the replacements"
        cls.pw = sync_playwright().start()
        cls.browser, cls.page, cls.errors = open_page(cls.pw)
        cls.page.wait_for_timeout(1200)
        cls.frame = harness.frame_of(cls.page)
        cls.frame.evaluate("() => { window.M = window.MemuCharacters; }")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def ev(self, script, arg=None):
        return self.frame.evaluate(script, arg)

    def wait(self, script, timeout=8000):
        for _ in range(timeout // 100):
            if self.frame.evaluate(script):
                return True
            self.page.wait_for_timeout(100)
        self.fail("timeout waiting for: " + script)

    def sent(self):
        return self.page.evaluate("() => window.__sent")

    def test_1_default_model_and_chip(self):
        self.wait("() => M.S.modelsLoaded")
        self.assertEqual(self.ev("() => M.S.settings.model"), "reform-qwen3.8-flash")

    def test_2_create_character_and_chat(self):
        self.frame.click("text=New character")
        self.frame.wait_for_selector(".ch-editor")
        fields = self.frame.query_selector_all(".ch-editor .mu-input, .ch-editor .mu-textarea")
        fields[0].fill("Alice")
        fields[1].fill("{{char}} is a kind witch.")
        fields[2].fill("Welcome, {{user}}!")
        self.frame.click(".ch-modal-foot >> text=Create")
        self.wait("() => M.S.view === 'chat' && M.S.char && M.S.char.data.name === 'Alice'")
        self.frame.wait_for_selector("#model-chip")
        self.assertEqual(self.ev("() => document.querySelector('#model-chip').textContent.trim()"), "Qwen3.8 Flash")

        self.assertTrue(self.ev("() => !!document.querySelector('#effort-chip')"))
        self.frame.click("#effort-chip")
        labels = self.ev("() => [...document.querySelectorAll('.ch-menu .ch-menu-item')].map(b => b.textContent.trim())")
        joined = " | ".join(labels)
        for name in ("Auto", "No reasoning", "Low", "Medium", "High"):
            self.assertIn(name, joined)
        self.assertNotIn("Maximum", joined)
        self.assertNotIn("Minimal", joined)
        self.assertIn("model default", joined)
        self.frame.click(".ch-menu .ch-menu-item >> text=No reasoning")
        self.assertEqual(self.ev("() => M.S.settings.gen.thinking"), "off")
        self.frame.fill("#composer-text", "Hi Alice")
        self.frame.press("#composer-text", "Enter")
        self.wait("() => M.S.chat.messages.length === 3 && !M.S.busy")
        last = self.sent()[-1]
        self.assertEqual(last["model"], "reform-qwen3.8-flash")
        self.assertEqual(last["params"]["effort"], "off")
        self.assertFalse(last["params"]["thinking"])

    def test_3_picker_names_search_keyboard(self):
        self.frame.click("#model-chip")
        self.frame.wait_for_selector(".ch-mp .ch-mp-item")
        ids = self.ev("() => [...document.querySelectorAll('.ch-mp-item .ch-mp-id')].map(e => e.textContent)")
        names = self.ev("() => [...document.querySelectorAll('.ch-mp-item .ch-mp-name')].map(e => e.textContent)")
        self.assertIn("aws9-gpt-6.1-sol", ids)
        self.assertIn("GPT-6.1 Sol", names)
        self.assertNotIn("Wanx2.1 T2I Turbo", names, "image models are not chat models")
        self.assertTrue(self.ev("() => [...document.querySelectorAll('.ch-mp-tag')].some(e => e.textContent === 'free')"))
        self.frame.fill(".ch-mp-search", "sol")
        self.frame.press(".ch-mp-search", "Enter")
        self.wait("() => M.S.settings.model === 'aws9-gpt-6.1-sol' && !document.querySelector('.ch-mp')")
        self.assertEqual(self.ev("() => document.querySelector('#model-chip').textContent.trim()"), "GPT-6.1 Sol")

        self.assertEqual(self.ev("() => M.effortValue()"), "low")
        self.ev("() => M.setEffort('xhigh')")
        self.assertEqual(self.ev("() => M.S.preset.reasoning_effort"), "xhigh")
        self.assertEqual(self.ev("() => M.S.settings.gen.thinking"), "auto")
        self.assertIn("Extra high", self.ev("() => document.querySelector('#effort-chip').textContent"))

        self.ev("() => M.setChatModel('xkiro-qwen/qwen3.8-max:free', true)")
        self.assertFalse(self.ev("() => !!document.querySelector('#effort-chip')"))
        self.ev("() => M.setChatModel('aws9-gpt-6.1-sol', true)")

    def test_4_clamp_matches_backend(self):
        from app import reasoning_levels as rl
        profiles = [
            {"kind": "levels", "levels": ["low", "medium", "high"], "can_off": True, "default": ""},
            {"kind": "levels", "levels": ["low", "high", "max"], "can_off": False, "default": ""},
            {"kind": "levels", "levels": ["minimal", "low", "medium", "high", "xhigh"], "can_off": False, "default": ""},
            {"kind": "toggle", "levels": [], "can_off": True, "default": "off"},
        ]
        for prof in profiles:
            options = rl.options(prof)
            for level in ("auto", "off", "on", "minimal", "low", "medium", "high", "xhigh", "max"):
                js = self.ev("([v, o]) => M.clampEffort(v, o)", [level, options])
                self.assertEqual(js, rl.clamp(level, prof), f"{level} {options}")

    def test_5_regenerate_and_parallel_variants(self):
        self.page.evaluate("() => { window.__replyFor = (p, n) => 'Variant number ' + n + '.'; }")
        before = len(self.sent())
        self.frame.click(".ch-swtool >> text=Regenerate")
        self.wait("() => !M.S.busy && M.S.chat.messages[2].swipes.length === 2")
        self.assertEqual(self.sent()[-1]["params"]["effort"], "xhigh")
        self.assertEqual(self.sent()[-1]["model"], "aws9-gpt-6.1-sol")
        right = self.frame.locator(".ch-msg").last.locator(".ch-swipes .ch-ib").last
        right.click(button="right")
        self.frame.wait_for_selector(".ch-vmenu")
        self.frame.click(".ch-vseg-b[data-n='3']")
        self.assertEqual(self.ev("() => M.S.settings.ui.variantCount"), 3)
        self.frame.click(".ch-vgo")
        self.wait("() => !M.S.busy && M.S.chat.messages[2].swipes.length === 5", 12000)
        self.assertEqual(len(self.sent()) - before, 4)
        swipes = self.ev("() => M.S.chat.messages[2].swipes")
        self.assertEqual(len(set(swipes[2:])), 3, "three distinct variants")
        self.assertTrue(all(s.startswith("Variant number") for s in swipes[1:]))

    def test_6_ai_json_parsing(self):
        parsed = self.ev("(t) => M.aiParseJson(t)", "Here is the card:\n```json\n{\"name\": \"A\", \"tags\": [\"x\",],}\n```")
        self.assertEqual(parsed["name"], "A")
        cut = self.ev("(t) => M.aiParseJson(t)", '<think>hmm {</think>{"name": "B", "description": "half')
        self.assertEqual(cut["name"], "B")
        self.assertEqual(cut["description"], "half")
        self.assertTrue(self.ev("() => { try { M.aiParseJson('no json here'); return false; } catch (e) { return true; } }"))
        card = self.ev("(j) => M.aiNormCard(j)", {"data": {"name": " C ", "tags": "Sci-Fi, Robot", "alternate_greetings": "Hi"}})
        self.assertEqual(card["name"], "C")
        self.assertEqual(card["tags"], ["sci-fi", "robot"])
        self.assertEqual(card["alternate_greetings"], ["Hi"])
        self.assertEqual(card["first_mes"], "")
        book = self.ev("(j) => M.aiNormBook(j, {}, 'C')", {"entries": [{"title": "Port", "keys": "port, harbour", "content": "A harbour."},
                                                                       {"title": "Empty", "content": ""}]})
        self.assertEqual(len(book["entries"]), 1)
        self.assertEqual(book["entries"][0]["keys"], ["port", "harbour"])

    def test_7_create_character_with_ai(self):
        self.page.evaluate("(card) => { window.__replyFor = () => card; }", CARD_JSON)
        self.ev("() => M.openAiCreator('character')")
        self.frame.wait_for_selector(".ch-ai-idea")
        self.frame.fill(".ch-ai-idea", "A wandering cartographer in a rainy harbour town")
        self.frame.locator(".ch-modal-foot button.primary").click()
        self.frame.wait_for_selector(".ch-modal-foot button:has-text('Save character')", timeout=10000)
        self.assertIn("Lyra Vale", self.ev("() => document.querySelector('.ch-ai-card').innerText + [...document.querySelectorAll('.ch-ai-card input')].map(i => i.value).join(' ')"))
        req = self.sent()[-1]
        self.assertEqual(req["messages"][0]["role"], "system")
        self.assertIn("cartographer", req["messages"][-1]["content"])
        self.frame.click(".ch-modal-foot button:has-text('Save character')")
        self.wait("() => M.S.char && M.S.char.data.name === 'Lyra Vale'")
        data = self.ev("() => M.S.char.data")
        self.assertIn("cartographer", data["description"])
        self.assertEqual(data["alternate_greetings"], ["Need a guide?"])
        self.assertIn("fantasy", data["tags"])

    def test_9_no_console_errors(self):
        noise = [e for e in self.errors if "favicon" not in e]
        self.assertEqual(noise, [])

if __name__ == "__main__":
    unittest.main()
