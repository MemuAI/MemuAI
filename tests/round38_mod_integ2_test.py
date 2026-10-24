"""Round 38, wave 2 (r38-mod-integ-2): panels, commands and AI skills of «Персонажи», «Новые модели API», «Бенчмарки»,
the Translator command, server panel data (Memu.panel.data), the shared panel kit, chat result cards, the MemuMusic
widget settings section. No network: the monitor and the OpenRouter catalogue are mocked.

Live part (playground in a subprocess + Chromium): the three new panels render in the dark and light theme, a chat
result card with buttons. Screenshots: R38_SHOTS=<dir>.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from round38_mod_integ_test import CHROMIUM, SHOTS, _free_port, sync_playwright  # noqa: E402

NOW = time.time()
EVENTS = {"events": [
    {"id": 1, "module_id": "api-models", "title": "Новые модели API · OpenRouter", "text": "+ OpenAI: GPT-6 mini · openai/gpt-6-mini", "at": NOW - 7200},
    {"id": 2, "module_id": "api-models", "title": "Новые модели API · OpenRouter",
     "text": "+ Anthropic: Claude Opus 5.5 · anthropic/claude-opus-5.5\n+ Qwen: Qwen 4 Max · qwen/qwen-4-max", "at": NOW - 600},
], "seen": 1}
CATALOG = {"anthropic/claude-opus-5.5": {"context": 1000000, "input": 15.0, "output": 75.0},
           "openai/gpt-6-mini": {"context": 400000, "input": 0.25, "output": 2.0},
           "qwen/qwen-4-max:free": {"context": 128000, "input": 0.0, "output": 0.0}}
BENCH = {"cache_arena": {"text": {"at": NOW * 1000 - 3600e3, "rows": [
    {"model": "gemini-3-pro", "org": "Google", "score": 1501.2}, {"model": "claude-opus-5.5", "org": "Anthropic", "score": 1512.7},
    {"model": "gpt-6", "org": "OpenAI", "score": 1498.0}, {"model": "grok-5", "org": "xAI", "score": 1470.4}]}},
    "cache_simple": {"at": NOW * 1000, "rows": [{"model": "Claude Opus 5.5", "org": "Anthropic", "score": 71.3},
                                               {"model": "GPT-6", "org": "OpenAI", "score": 68.1}]},
    "cache_aa": {"at": NOW * 1000, "rows": [{"model": "Old", "deprecated": True, "m": {"intelligence": 99}},
                                            {"model": "GPT-6", "org": "OpenAI", "m": {"intelligence": 74}}]}}
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def seed(root: Path) -> None:
    from app import modules_host as mh
    from app.characters_api import store_set
    for module in ("characters", "api-models", "benchmarks", "translator"):
        mh.configure_module(root, module, enabled=True)
    store_set(root, "characters", "index", [
        {"id": "c-luna", "name": "Луна", "tags": ["фэнтези", "маг"], "thumb": PNG, "fav": True, "updated": NOW * 1000 - 120000, "lastChat": "ch1", "chats": 2},
        {"id": "c-rex", "name": "Рекс", "tags": ["детектив"], "thumb": "", "updated": NOW * 1000 - 86400000 * 3, "lastChat": "ch2", "chats": 1},
        {"id": "c-ada", "name": "Ада Лавлейс", "tags": [], "updated": 0, "chats": 0}])
    store_set(root, "characters", "chatlist/c-luna", [{"id": "ch1", "updated": NOW * 1000 - 120000, "count": 14,
                                                       "preview": "Звёзды сегодня шепчут о дороге на север."}])
    store_set(root, "characters", "chatlist/c-rex", [{"id": "ch2", "updated": NOW * 1000 - 86400000 * 3, "count": 6, "preview": "Улики не врут."}])
    mh.save_persistent(root, "benchmarks", BENCH)


def patches():
    from app import module_integration_data as d
    return [mock.patch.object(d, "_monitor", lambda module: EVENTS if module == "api-models" else {"events": [
                {"id": 7, "title": "Бенчмарки · LMArena", "text": "Новый лидер: claude-opus-5.5", "at": NOW - 300}]}),
            mock.patch.object(d, "_monitor_status", lambda module: {"enabled": True, "sources": [{"id": "openrouter", "last_success": NOW - 1800}]}),
            mock.patch.object(d, "_fetch_openrouter", lambda: CATALOG)]


class DataAndCommandsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "modules"
        seed(cls.root)
        cls.patches = patches()
        for p in cls.patches:
            p.start()
        from app import module_integration_data as d
        d._CATALOG.update(at=0.0, specs={})

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def run_cmd(self, module, command, args=None):
        from app import module_integration as mi
        return asyncio.run(mi.run_command(module, command, args or {}, root=self.root))

    def test_manifests_valid(self):
        from app import module_integration as mi, modules_host as mh
        for module in ("characters", "api-models", "benchmarks", "translator"):
            manifest = json.loads((mh.builtin_dir() / module / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(mi.validate_contributes(manifest["contributes"], builtin=True, folder=mh.builtin_dir() / module), [], module)
        self.assertEqual({r["module"] for r in mi.contributions(self.root)}, {"characters", "api-models", "benchmarks", "translator"})
        # a custom module cannot ask for server data
        errors = []
        clean = mi.normalize_contributes({"panels": [{"id": "p", "entry": "a.html", "data": "characters.panel"}]}, errors=errors)
        self.assertNotIn("data", clean["panels"][0])
        self.assertTrue(errors)

    def test_characters_data(self):
        from app import module_integration as mi
        data = mi.panel_data("characters", "recent", {}, root=self.root)
        self.assertEqual([r["name"] for r in data["recent"]][:2], ["Луна", "Рекс"])
        self.assertEqual(data["recent"][0]["preview"], "Звёзды сегодня шепчут о дороге на север.")
        self.assertTrue(data["recent"][0]["thumb"].startswith("data:image/png"))
        self.assertEqual(data["day"]["name"], "Луна")          # favourites first, the only favourite
        self.assertEqual(data["total"], 3)
        with self.assertRaises(KeyError):
            mi.panel_data("characters", "nope", {}, root=self.root)

    def test_characters_command(self):
        out = self.run_cmd("characters", "open", {"name": "рекс"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["client"]["detail"]["query"], "c-rex")
        latest = self.run_cmd("characters", "open")
        self.assertEqual(latest["card"]["title"], "Луна")       # most recently updated, not the chat id order
        from app import module_integration as mi
        self.assertEqual(mi.match_phrase("Открой чат с Луной", self.root)["command"], "open")

    def test_api_models_data_and_command(self):
        from app import module_integration as mi
        data = mi.panel_data("api-models", "new-models", {}, root=self.root)
        names = [m["name"] for m in data["models"]]
        self.assertEqual(names, ["Claude Opus 5.5", "Qwen 4 Max", "GPT-6 mini"])
        self.assertEqual(data["models"][0]["input"], 15.0)
        self.assertEqual(data["models"][1]["output"], 0.0)     # matched by the bare name (…:free)
        self.assertFalse(data["models"][0]["seen"])
        self.assertTrue(data["models"][2]["seen"])
        self.assertTrue(data["enabled"])
        out = self.run_cmd("api-models", "recent")
        self.assertEqual(out["card"]["kind"], "list")
        self.assertIn("$15 → $75", out["card"]["items"][0]["subtitle"])
        self.assertIn("Claude Opus 5.5", out["text"])
        self.assertEqual(mi.match_phrase("какие новые модели вышли?", self.root)["module"], "api-models")

    def test_benchmarks_data_and_command(self):
        from app import module_integration as mi
        data = mi.panel_data("benchmarks", "leaders", {}, root=self.root)
        self.assertEqual([b["id"] for b in data["boards"]], ["arena-text", "aa", "simple"])
        self.assertEqual(data["boards"][0]["rows"][0]["model"], "claude-opus-5.5")
        self.assertEqual([r["model"] for r in data["boards"][1]["rows"]], ["GPT-6"])   # deprecated dropped
        self.assertEqual(data["changes"][0]["text"], "Новый лидер: claude-opus-5.5")
        out = self.run_cmd("benchmarks", "leaders", {"board": "simple"})
        self.assertEqual(out["card"]["title"], "SimpleBench")
        self.assertEqual(out["card"]["items"][0]["rank"], 1)
        self.assertIn("Claude Opus 5.5", out["text"])
        found = mi.match_phrase("кто лидирует в бенчмарке SimpleBench", self.root)
        self.assertEqual((found["command"], found["args"]), ("leaders", {"board": "SimpleBench"}))
        self.assertIsNone(mi.match_phrase("кто лидирует в чемпионате", self.root))

    def test_translator_command_opens_module(self):
        out = self.run_cmd("translator", "translate", {"text": "Привет, мир", "to": "en"})
        self.assertEqual(out["client"]["open_module"], "translator")
        self.assertEqual(out["client"]["detail"]["args"], {"text": "Привет, мир", "to": "en"})
        main = (ROOT / "app/module_builtins/translator/main.js").read_text(encoding="utf-8")
        self.assertIn('data.command === "translate"', main)

    def test_skill_prompt_mentions_new_modules(self):
        from app import module_integration as mi
        prompt = mi.skill_prompt(self.root)
        for name in ("characters.open", "api-models.recent", "benchmarks.leaders", "translator.translate", "LMArena"):
            self.assertIn(name, prompt)

    def test_panel_document_has_kit_and_data(self):
        from app import module_integration as mi
        page = mi.panel_document("benchmarks", "leaders", root=self.root)
        self.assertIn(".mk-row", page)                 # the shared kit
        self.assertIn(".bm-place", page)               # the module's own styles after it
        self.assertIn("panel_data", page)
        self.assertIn("data-size", page)


class StaticTest(unittest.TestCase):
    def test_js_syntax_and_wiring(self):
        node = shutil.which("node")
        files = ["app/module_builtins/characters/integration/panels.js", "app/module_builtins/api-models/integration/panels.js",
                 "app/module_builtins/benchmarks/integration/panels.js", "app/module_builtins/translator/main.js",
                 "app/module_builtins/music/integration/settings-widgets.js", "app/pgstatic/module-integration.js", "app/pgstatic/app.js"]
        if node:
            for name in files:
                subprocess.run([node, "--check", str(ROOT / name)], check=True)
        app_js = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
        self.assertIn("MemuModInt.cardsHtml(m, i)", app_js)
        self.assertIn("MemuModInt.runTool(call, { message: assistant })", app_js)
        for spec in ("MemuAI.spec", "MemuAIProxy.spec", "build.py"):
            self.assertIn("app.module_integration_data", (ROOT / spec).read_text(encoding="utf-8"))
        for name in files[:3]:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("setInterval", text)


@unittest.skipUnless(sync_playwright and CHROMIUM, "needs playwright and Chromium")
class LivePanelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name) / "data"
        cls.port = _free_port()
        env = dict(os.environ, MEMUAI_DATA_DIR=str(data), MEMUAI_PROXY_URL="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
        boot = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); from app import pgdata, modules_host as mh; "
                "pgdata.save_preferences({'profile_onboarded': True, 'profile_name': 'Test'}); "
                "import round38_mod_integ2_test as T; T.seed(mh.default_root())") % (str(ROOT), str(ROOT / "tests"))
        subprocess.run([sys.executable, "-c", boot], env=env, check=True)
        code = ("import sys, uvicorn; sys.path.insert(0, %r); sys.path.insert(0, %r); import round38_mod_integ2_test as T; "
                "[p.start() for p in T.patches()]; from app.playground_api import create_app; "
                "uvicorn.run(create_app(), host='127.0.0.1', port=%d, log_level='error')") % (str(ROOT), str(ROOT / "tests"), cls.port)
        cls.proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        http = httpx.Client(trust_env=False, timeout=30)
        for _ in range(240):
            try:
                if http.get(cls.base + "/api/pg/modint").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.tmp.cleanup()

    def poll(self, page, script, timeout=30000):
        for _ in range(timeout // 200):
            try:
                if page.evaluate(script):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
        raise TimeoutError(script)

    def open_home(self, theme):
        page = self.browser.new_page(viewport={"width": 1360, "height": 1000}, color_scheme=theme)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(self.base + "/")
        self.poll(page, "() => !!(window.MemuModInt && MemuModInt.state().modules >= 4 && window.MemuPanels)")
        page.evaluate("t => { document.documentElement.dataset.theme = t; MemuModules.broadcastTheme(); }", theme)
        return page, errors

    def test_panels_and_chat_card(self):
        page, errors = self.open_home("dark")
        page.evaluate("() => MemuPanels.openGallery()")
        page.wait_for_selector('[data-modpanel-add="benchmarks:leaders"]', timeout=15000)
        page.wait_for_selector('[data-modpanel-add="characters:today"]', timeout=5000)
        page.keyboard.press("Escape")
        keys = ["characters:recent", "characters:today", "api-models:new-models", "benchmarks:leaders"]
        for key in keys:
            page.evaluate("k => MemuModInt.addPanel(k, 'home')", key)
        for key in keys:
            page.wait_for_selector(f'.mmi-slot[data-mmi="{key}"] iframe.mmi-frame.ready', timeout=30000)
        frames = {key: page.query_selector(f'.mmi-slot[data-mmi="{key}"] iframe').content_frame() for key in keys}
        frames["characters:recent"].wait_for_selector('.mk-row[data-char="c-luna"]', timeout=15000)
        frames["characters:today"].wait_for_selector(".ch-actions .mk-cta", timeout=15000)
        self.assertIn("Продолжить", frames["characters:today"].inner_text(".ch-day + .ch-actions"))
        frames["api-models:new-models"].wait_for_selector(".am-price", timeout=15000)
        frames["benchmarks:leaders"].wait_for_selector('.bm-place[data-place="1"]', timeout=15000)
        self.assertIn("claude-opus-5.5", frames["benchmarks:leaders"].inner_text(".mk-list"))
        frames["benchmarks:leaders"].click('[data-board="simple"]')
        self.assertIn("Claude Opus 5.5", frames["benchmarks:leaders"].inner_text(".mk-list"))
        time.sleep(0.8)
        page.evaluate("() => document.querySelector('#panels-home')?.scrollIntoView()")
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / "w2-01-panels-dark.png"))
        # a chat result card: the AI ran benchmarks.leaders; the card is drawn under the answer
        html = page.evaluate("""async () => {
            const m = { role: "assistant", content: "" };
            const out = await MemuModInt.runTool({ tool: "module_command", args: { module: "benchmarks", command: "leaders", args: { board: "arena" } } }, { message: m });
            const m2 = { role: "assistant", content: "" };
            await MemuModInt.runTool({ tool: "module_command", args: { module: "characters", command: "open", args: { name: "Луна" } } }, { message: m2 });
            const box = document.createElement("div"); box.id = "cardtest";
            box.style.cssText = "position:fixed;left:300px;top:40px;width:520px;padding:16px;background:var(--bg);z-index:99999";
            box.innerHTML = MemuModInt.cardsHtml(m, 0) + MemuModInt.cardsHtml(m2, 1);
            document.body.appendChild(box);
            return { hasCardInResult: "card" in out, html: box.innerHTML }; }""")
        self.assertFalse(html["hasCardInResult"])
        self.assertIn("mmi-rank", html["html"])
        self.assertIn("Луна", html["html"])
        time.sleep(0.4)
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / "w2-02-chat-cards-dark.png"), clip={"x": 290, "y": 30, "width": 560, "height": 420})
        self.assertEqual([e for e in errors if "ResizeObserver" not in e], [])
        page.close()

    def test_light_theme(self):
        page, errors = self.open_home("light")
        for key in ["characters:today", "api-models:new-models", "benchmarks:leaders"]:
            page.evaluate("k => MemuModInt.addPanel(k, 'home')", key)
        for key in ["characters:today", "api-models:new-models", "benchmarks:leaders"]:
            page.wait_for_selector(f'.mmi-slot[data-mmi="{key}"] iframe.mmi-frame.ready', timeout=30000)
        time.sleep(1.5)
        page.evaluate("() => document.querySelector('#panels-home')?.scrollIntoView()")
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / "w2-03-panels-light.png"))
        self.assertEqual([e for e in errors if "ResizeObserver" not in e], [])
        page.close()


if __name__ == "__main__":
    unittest.main()
