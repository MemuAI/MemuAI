"""Round 30: Telegram Mini App — desktop layout, header buttons, new MemuCode session, argument auto-complete.

Offline: signed initData (mock), a fake command catalogue, temporary folders. The browser part (headless Chromium,
skipped when it is missing) opens the real Mini App server and checks the header buttons after redraws, the
«New session» sheet, the desktop layout of Telegram Desktop and the chat commands menu.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

from app import tg_webapp, tgapp_complete  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TGAPP = ROOT / "app" / "tgapp"
TOKEN = "123456:TEST-token_for-mini-app"
USER = 4242
CATALOG = {
    "commands": [
        {"name": "model", "hint": "[model|auto|notes]", "ru": "Выбрать модель", "en": "Pick a model", "aliases": []},
        {"name": "mode", "hint": "[mode]", "ru": "Режим", "en": "Mode", "aliases": []},
        {"name": "permissions", "hint": "[mode]", "ru": "Разрешения", "en": "Permissions", "aliases": ["approvals"]},
        {"name": "effort", "hint": "[auto|minimal|low|medium|high|max]", "ru": "Глубина", "en": "Effort", "aliases": []},
        {"name": "think", "hint": "[on|off]", "ru": "Размышления", "en": "Thinking", "aliases": ["thinking"]},
        {"name": "color", "hint": "[color|#hex|reset]", "ru": "Цвет", "en": "Colour", "aliases": []},
        {"name": "resume", "hint": "[id|search]", "ru": "Продолжить", "en": "Resume", "aliases": ["sessions"]},
        {"name": "cd", "hint": "<path> [--new]", "ru": "Папка", "en": "Folder", "aliases": []},
        {"name": "attach", "hint": "<path>", "ru": "Прикрепить", "en": "Attach", "aliases": []},
        {"name": "help", "hint": "[command]", "ru": "Справка", "en": "Help", "aliases": ["?"]},
        {"name": "review", "hint": "[target]", "ru": "Ревью", "en": "Review", "aliases": []},
        {"name": "write-tests", "hint": "[task]", "ru": "Тесты", "en": "Tests", "aliases": [], "source": "skill"},
    ],
    "models": ["anthropic/claude-sonnet", "openai/gpt-5"], "model": "openai/gpt-5",
}

def values(result):
    return [item["value"] for item in result["items"]]

class CompleteUnit(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="r30-proj-"))
        (self.root / "src" / "ui").mkdir(parents=True)
        (self.root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
        (self.root / "src" / "ui" / "menu.js").write_text("x\n", encoding="utf-8")
        (self.root / "my docs").mkdir()
        (self.root / "README.md").write_text("# hi\n", encoding="utf-8")
        (self.root / "node_modules" / "pkg").mkdir(parents=True)
        (self.root / "node_modules" / "pkg" / "main.js").write_text("", encoding="utf-8")
        self.outside = Path(tempfile.mkdtemp(prefix="r30-outside-"))
        (self.outside / "secret.txt").write_text("s", encoding="utf-8")
        (self.outside / "folder").mkdir()
        tgapp_complete._index.clear()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def run_(self, text, **kw):
        kw.setdefault("root", str(self.root))
        kw.setdefault("catalog", CATALOG)
        return tgapp_complete.complete(text, **kw)

    def test_commands_with_aliases(self):
        result = self.run_("/mo")
        self.assertEqual(result["kind"], "command")
        self.assertEqual(result["span"], 3)
        self.assertEqual(values(result)[:2], ["/mode", "/model"])
        self.assertEqual(values(self.run_("/approvals"))[0], "/permissions")

    def test_model_arguments_merge_the_terminal_and_the_proxy_models(self):
        result = self.run_("/model ", models=["demo/mini"], lang="en")
        self.assertEqual(result["kind"], "argument")
        self.assertEqual(values(result), ["auto", "notes", "anthropic/claude-sonnet", "openai/gpt-5", "demo/mini"])
        current = next(item for item in result["items"] if item["value"] == "openai/gpt-5")
        self.assertIn("current", current["meta"])
        narrowed = self.run_("/model gpt")
        self.assertEqual(values(narrowed), ["openai/gpt-5"])
        self.assertEqual(narrowed["span"], 3)

    def test_modes_effort_and_hint_choices(self):
        self.assertEqual(values(self.run_("/permissions ")), ["default", "acceptEdits", "plan", "auto", "bypassPermissions"])
        self.assertEqual(values(self.run_("/approvals a")), ["auto", "acceptEdits"])
        self.assertEqual(values(self.run_("/effort h")), ["high"])
        self.assertEqual(values(self.run_("/thinking ")), ["on", "off"])

        self.assertEqual(values(self.run_("/color ")), ["reset"])
        self.assertEqual(tgapp_complete.hint_choices("[auto|minimal|low]"), ["auto", "minimal", "low"])
        self.assertEqual(tgapp_complete.hint_choices("[model]"), [])
        self.assertEqual(values(self.run_("/help mo")), ["mode", "model"])
        self.assertEqual(self.run_("/effort high now")["items"], [], "a second word has nothing to complete")

    def test_mentions_stay_inside_the_project(self):
        result = self.run_("look at @ma")
        self.assertEqual(result["kind"], "mention")
        self.assertEqual(result["span"], 3)
        self.assertEqual(values(result), ["@src/main.py"])
        nested = values(self.run_("@men"))
        self.assertEqual(nested, ["@src/ui/menu.js"])
        spaced = values(self.run_("@my"))
        self.assertEqual(spaced, ['@"my docs/'])
        self.assertEqual(self.run_("@x", root="")["items"], [])

    def test_folder_and_file_arguments(self):
        folders = values(self.run_("/cd "))
        self.assertEqual(folders, ['"my docs/', "src/"])
        self.assertEqual(values(self.run_("/cd sr")), ["src/"])
        self.assertEqual(values(self.run_("/cd src/")), ["src/ui/"])

        outside = values(self.run_(f"/cd {self.outside}/"))
        self.assertEqual(outside, [f"{self.outside}/folder/"])
        self.assertEqual(values(self.run_(f"/attach {self.outside}/")), [f"{self.outside}/folder/"])
        self.assertEqual(values(self.run_("/attach src/")), ["src/ui/", "src/main.py"])
        self.assertTrue(next(i for i in self.run_("/attach src/")["items"] if i["value"] == "src/main.py")["final"])

    def test_resume_lists_the_sessions_of_the_project(self):
        rows = [{"id": "20261010-1", "title": "Refactor the parser"}, {"id": "20261009-2", "title": ""}]
        with mock.patch("app.cli.session.list_sessions", return_value=rows) as listing:
            result = self.run_("/resume ")
        self.assertEqual(values(result), ["20261010-1", "20261009-2"])
        self.assertEqual(result["items"][0]["label"], "Refactor the parser")
        self.assertEqual(Path(listing.call_args[0][0]), self.root)
        with mock.patch("app.cli.session.list_sessions", return_value=rows):
            self.assertEqual(values(self.run_("/resume pars")), ["20261010-1"])

    def test_nothing_for_plain_text_or_several_lines(self):
        self.assertEqual(self.run_("hello")["items"], [])
        self.assertEqual(self.run_("/model\n")["items"], [])
        self.assertEqual(self.run_("/model x\n/mo")["items"], [])

def init_data() -> str:
    return tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH",
                                     "user": {"id": USER, "first_name": "Yono"}}, TOKEN)

class CompleteApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        from app import tgapp_chat
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "memes": [], "chat_memes": {}})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare", "code_control": True})
        cls.models = mock.patch.object(tgapp_chat, "chat_models", return_value=[
            {"id": "test/model", "name": "Test", "provider": "test", "vision": True, "pinned": False, "default": True}])
        cls.models.start()
        from app.tgapp_api import create_tgapp, auth_failures, auth_limiter
        auth_failures._hits.clear()
        auth_limiter._hits.clear()
        cls.client = TestClient(create_tgapp())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.models.stop()
        tg_webapp.save_config({"enabled": False})

    def post(self, name, body, auth=True):
        headers = {}
        if auth:
            token = self.client.post("/tgapp/api/auth", json={"init_data": init_data()}).json()["token"]
            headers = {"Authorization": "Bearer " + token}
        return self.client.post("/tgapp/api/" + name, json=body, headers=headers)

    def test_needs_a_session_and_the_code_switch(self):
        self.assertEqual(self.post("code.complete", {"text": "/model "}, auth=False).status_code, 401)
        tg_webapp.save_config({"code_control": False})
        try:
            self.assertEqual(self.post("code.complete", {"text": "/model "}).status_code, 403)
        finally:
            tg_webapp.save_config({"code_control": True})

    def test_models_of_the_proxy_and_files_of_the_session_only(self):
        from app import tgapp_code
        project = Path(tempfile.mkdtemp(prefix="r30-api-"))
        (project / "app.py").write_text("x", encoding="utf-8")
        try:
            with mock.patch.object(tgapp_code, "command_catalog", return_value=dict(CATALOG)), \
                    mock.patch.object(tgapp_code, "root_of", return_value=str(project)) as root_of:
                data = self.post("code.complete", {"text": "/model te"}).json()
                self.assertEqual([i["value"] for i in data["items"]], ["test/model"])
                data = self.post("code.complete", {"text": "open @ap", "session_id": "s1"}).json()
                self.assertEqual([i["value"] for i in data["items"]], ["@app.py"])
                root_of.assert_called_with("s1", "")

                data = self.post("code.complete", {"text": "open @ap", "root": str(project)}).json()
                self.assertEqual(data["items"], [])
        finally:
            shutil.rmtree(project, ignore_errors=True)

class StaticContract(unittest.TestCase):
    def read(self, name):
        return (TGAPP / name).read_text(encoding="utf-8")

    def test_files_are_wired(self):
        html = self.read("index.html")
        self.assertIn('<script src="chatcmd.js?v=1.1-r30"></script>', html)
        self.assertLess(html.index("chatcmd.js"), html.index("app.js?v="))

        self.assertIn('<link rel="stylesheet" href="shared/cmd-highlight.css?v=1.0-r30">', html)
        self.assertLess(html.index("shared/cmd-highlight.js"), html.index("chatcmd.js"))

    def test_shared_files_are_served_and_nothing_else(self):
        from fastapi.testclient import TestClient
        from app.tgapp_api import create_tgapp
        client = TestClient(create_tgapp())
        for name, media in (("cmd-highlight.js", "text/javascript"), ("cmd-highlight.css", "text/css")):
            response = client.get("/tgapp/shared/" + name)
            self.assertEqual(response.status_code, 200, name)
            self.assertTrue(response.headers["content-type"].startswith(media), name)
            self.assertEqual(response.content, (ROOT / "app" / "pgstatic" / name).read_bytes())
        for name in ("app.js", "style.css", "..%2Fversion.py", "index.html"):
            self.assertEqual(client.get("/tgapp/shared/" + name).status_code, 404, name)
        self.assertIn("app.tgapp_complete", (ROOT / "build.py").read_text(encoding="utf-8"))
        self.assertIn("'app.tgapp_complete'", (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8"))

    def test_header_buttons_keep_their_handlers(self):
        app = self.read("app.js")
        self.assertIn("function keepHandlers(actionsEl)", app)
        self.assertIn("drawTop($(\"#list-title\"), $(\"#list-actions\"), root.title, root.actions, root.compact, root.sub, S.rootTop)", app)

    def test_desktop_layout_and_keyboard(self):
        device = self.read("device.js")
        self.assertIn('rail: layout === "split" && (device === "desktop" || width >= RAIL_AT)', device)
        self.assertIn("requestFullscreen", device)
        self.assertIn("visualViewport", device)
        css = self.read("layout.css")
        self.assertIn("height: var(--app-h, 100%)", css)
        self.assertIn('html[data-kb="1"] .composer { padding-bottom: 8px; }', css)
        app = self.read("app.js")
        self.assertIn("function enterSends()", app)
        self.assertNotIn('!("ontouchstart" in window))) {', app)

    def test_every_new_string_has_both_languages(self):
        import re
        for name in ("chatcmd.js", "device.js", "code.js"):
            text = self.read(name)
            for match in re.finditer(r'tr\("([^"]*)",\s*"([^"]*)"\)', text):
                ru, en = match.groups()
                self.assertTrue(ru and en, (name, match.group(0)))
                self.assertFalse(re.search(r"[А-Яа-яЁё]", en), (name, en))

THEME = {"bg_color": "#17212b", "text_color": "#f5f5f5", "hint_color": "#708499", "button_color": "#5288c1",
         "button_text_color": "#ffffff", "secondary_bg_color": "#232e3c"}

def _chromium() -> str:
    for path in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome", "/usr/bin/chromium"):
        if path and Path(path).exists():
            return path
    return ""

@unittest.skipUnless(_chromium(), "headless Chromium is not available")
class BrowserFlows(unittest.TestCase):
    """The real Mini App server + playground + Proxy + offline model mock (tests/ui_lang_scan.Servers)."""

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(ROOT / "tests"))
        from playwright.sync_api import sync_playwright
        from ui_lang_scan import Servers
        cls.servers = Servers().start()
        from app import pgdata
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare", "code_control": True})
        pgdata.save_preferences({"telegram_bot_token": cls.servers.TG_TOKEN, "telegram_chat_id": str(cls.servers.TG_USER),
                                 "language": "ru"})
        cls.play = sync_playwright().start()
        cls.browser = cls.play.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.play.stop()
        cls.servers.stop()
        tg_webapp.save_config({"enabled": False})

    def open(self, platform="tdesktop", size=(1280, 800)):
        init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-r30",
                                         "user": {"id": self.servers.TG_USER, "first_name": "Yono", "language_code": "ru"}},
                                        self.servers.TG_TOKEN)
        frag = urllib.parse.urlencode({"tgWebAppData": init, "tgWebAppVersion": "8.0", "tgWebAppPlatform": platform,
                                       "tgWebAppThemeParams": json.dumps(THEME)})
        mobile = platform in ("android", "ios")
        ctx = self.browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=mobile, has_touch=mobile,
                                       bypass_csp=True)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.servers.tg}/tgapp/#{frag}")
        page.wait_for_selector("#app:not([hidden]) .tab", timeout=20000)
        page.wait_for_timeout(500)
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def test_telegram_desktop_gets_the_rail_and_the_new_session_button_works(self):
        page = self.open()
        self.assertEqual(page.evaluate("[document.documentElement.dataset.device, document.documentElement.dataset.layout,"
                                       " document.documentElement.dataset.rail]"), ["desktop", "split", "1"])
        self.assertTrue(page.query_selector("#top-full"), "the full-screen button of a computer")
        page.click('.tab[data-tab="code"]')
        page.wait_for_timeout(500)
        page.evaluate("window.MemuTgApp.kit.refreshTops()")
        page.click("#code-new")
        page.wait_for_selector(".sheet.open #ns-folder-row", timeout=3000)

    def test_chat_menu_survives_title_updates_and_a_layout_change(self):
        page = self.open("android", (390, 844))
        page.evaluate("window.MemuTgApp.openChat(null)")
        page.fill("#msg-input", "hi")
        page.click("#send-btn")
        page.wait_for_function("document.querySelector('#chat-more')", timeout=15000)
        page.wait_for_timeout(800)
        page.click("#chat-more")
        page.wait_for_selector(".sheet.open", timeout=3000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(600)
        page.click("#chat-more")
        page.wait_for_selector(".sheet.open", timeout=3000)

    def test_chat_command_menu_and_media_mark(self):
        page = self.open()
        page.evaluate("window.MemuTgApp.openChat(null)")
        page.wait_for_timeout(300)
        page.click("#msg-input")
        page.keyboard.type("/vi")
        page.wait_for_selector(".chat-cmd-menu:not([hidden]) .slash-item", timeout=2000)
        page.keyboard.press("Enter")
        self.assertEqual(page.input_value("#msg-input"), "/video ")

        self.assertEqual(page.evaluate("document.querySelector('.composer .cmdhl-chip:not([hidden])').dataset.kind"), "video")
        self.assertFalse(page.evaluate("document.querySelector('.composer .cmdhl-ghost').hidden"))
        self.assertEqual(page.evaluate("document.querySelector('.composer .cmdhl-note:not([hidden]) b').textContent"), "Видео")
        page.keyboard.type("волны")
        self.assertTrue(page.evaluate("document.querySelector('.composer .cmdhl-ghost').hidden"))
        page.fill("#msg-input", "")
        self.assertTrue(page.evaluate("document.querySelector('.composer .cmdhl-chip').hidden"))
        page.fill("#msg-input", "/video ")
        self.assertTrue(page.evaluate("!document.querySelector('.msg.user')"), "Enter picked the command, nothing was sent")

if __name__ == "__main__":
    unittest.main()
