"""Round 27 (r27-quick-features): new Quick Ask features — screen region, clipboard history, snippets, stickers,
documents (``app/quick_features.py``, ``app/quick_features_win.py``, ``pgstatic/quick/plugins/features*``).

No network. The Windows parts run against fake ctypes layers; the page is checked by the headless script
``tests/round27_quick_features_shots.py`` (skipped without Chromium).
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from app import quick_features as qf
from app import quick_features_win as qw

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"


class TmpDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r27qf-"))
        self.dir = mock.patch.object(qf, "_memu_dir", lambda: self.tmp)
        self.dir.start()
        qf.CLIPBOARD._rows = None

    def tearDown(self) -> None:
        self.dir.stop()
        qf.CLIPBOARD._rows = None
        shutil.rmtree(self.tmp, ignore_errors=True)


class SettingsTests(TmpDir):
    def test_defaults_and_junk(self) -> None:
        out = qf.normalize_settings({"clipboard": "on", "clipboard_limit": "1e999", "clipboard_ignore": "Telegram, slack.exe;;bad/name",
                                     "region_hotkey": "alt+shift+s", "clipboard_hotkey": "Alt+Shift+S", "stickers": 0})
        self.assertTrue(out["clipboard"])
        self.assertEqual(out["clipboard_limit"], 200)
        self.assertEqual(out["clipboard_ignore"], ["telegram.exe", "slack.exe"])
        self.assertEqual(out["region_hotkey"], "Alt+Shift+S")
        self.assertEqual(out["clipboard_hotkey"], "", "the same hotkey twice keeps only the first")
        self.assertFalse(out["stickers"])
        self.assertFalse(qf.normalize_settings(None)["clipboard"], "the clipboard history is opt-in")

    def test_hotkey_errors_in_both_languages(self) -> None:
        self.assertEqual(qf.hotkey_error("", "en"), "")
        self.assertEqual(qf.hotkey_error("Ctrl+Alt+V", "en"), "")
        self.assertIn("Unknown key", qf.hotkey_error("Alt+Foo", "en"))
        self.assertIn("клавиш", qf.hotkey_error("Alt+Foo", "ru"))
        self.assertIn("modifier", qf.hotkey_error("Q", "en"))

    def test_first_load_seeds_snippets_in_the_language(self) -> None:
        data = qf.load_config("en")
        self.assertEqual(len(data["snippets"]), 6)
        self.assertIn("date", [s["keyword"] for s in data["snippets"]])
        again = qf.load_config("ru")            # r27-quick-polish: untouched defaults follow the language
        self.assertEqual([s["id"] for s in again["snippets"]], [s["id"] for s in data["snippets"]])
        self.assertEqual(again["snippets"][0]["name"], "Сегодняшняя дата")
        saved = qf.save_config(snippets=[])
        self.assertEqual(saved["snippets"], [])
        self.assertEqual(qf.load_config("ru")["snippets"], [], "deleting every snippet is kept (no re-seed)")


class SnippetTests(unittest.TestCase):
    NOW = datetime(2026, 10, 9, 14, 5)

    def test_variables(self) -> None:
        out = qf.expand("{date} {date+1} {date-9} {weekday} {time} {datetime} {{literal}} {unknown} {input}|{selection}|{clipboard}",
                        now=self.NOW, clipboard="CB", selection="SEL", input_text="IN", lang="ru")
        self.assertEqual(out["text"], "09.10.2026 10.10.2026 30.09.2026 пятница 14:05 09.10.2026 14:05 {literal} {unknown} IN|SEL|CB")
        self.assertEqual(qf.expand("{date}", now=self.NOW, lang="en")["text"], "2026-10-09")
        self.assertEqual(qf.expand("{weekday+1}", now=self.NOW, lang="en")["text"], "Saturday")
        self.assertEqual(qf.expand("{time+1}", now=self.NOW)["text"], "{time+1}", "time has no day shift")

    def test_cursor_counts_steps_after_the_caret(self) -> None:
        out = qf.expand("Hi {cursor}!\r\nBye{cursor}", now=self.NOW)
        self.assertEqual(out["text"], "Hi !\r\nBye")
        self.assertEqual(out["cursor"], len("!\nBye"), "CRLF is one caret step; the second {cursor} is dropped")
        self.assertEqual(qf.expand("no caret")["cursor"], 0)
        uid = qf.expand("{uuid}")["text"]
        self.assertRegex(uid, r"^[0-9a-f-]{36}$")

    def test_normalize_and_match(self) -> None:
        rows = qf.normalize_snippets([
            {"keyword": ";Sig ", "name": "Подпись", "text": "x"}, {"keyword": "sig", "name": "Дубль", "text": "y"},
            {"keyword": "", "name": "", "text": "z"}, {"keyword": "off", "name": "Отказ", "text": " ", "kind": "ai"},
            {"keyword": "offer", "name": "Оффер", "text": "o", "kind": "ai", "id": "../bad"}, "junk"])
        self.assertEqual([r["keyword"] for r in rows], ["sig", "", "offer"])
        self.assertEqual(rows[1]["name"], "Дубль", "a duplicate keyword is dropped, the snippet stays")
        self.assertEqual(rows[2]["kind"], "ai")
        self.assertNotEqual(rows[2]["id"], "../bad")
        self.assertEqual([r["keyword"] for r in qf.match_snippets(rows, ";of")], ["offer"])
        self.assertEqual(qf.match_snippets(rows, ";sig hello")[0]["keyword"], "sig")
        self.assertEqual(qf.match_snippets(rows, ";дуб")[0]["name"], "Дубль")
        self.assertEqual(len(qf.match_snippets(rows, ";")), 3)


class SecretTests(unittest.TestCase):
    def test_secrets_are_recognised(self) -> None:
        for text in ("sk-proj-" + "a" * 30, "ghp_" + "b" * 36, "AKIA" + "C" * 16, "-----BEGIN RSA PRIVATE KEY-----\nabc",
                     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmn", "password: hunter2x", "пароль = qwerty123",
                     "P@ssw0rd!", "Kx9#mPq2", "123456789:AA" + "d" * 33):
            self.assertTrue(qf.looks_secret(text), text)

    def test_ordinary_text_is_kept(self) -> None:
        for text in ("Hello world", "MyVar_123", "user@mail.com", "https://example.com/a?b=1&c=2", r"C:\Users\me\file.txt",
                     "12.10.2026", "Встреча в 15:30", "a1b2c3d4e5", "SELECT * FROM t;", "#ff8800", ""):
            self.assertFalse(qf.looks_secret(text), text)

    def test_password_managers(self) -> None:
        settings = qf.normalize_settings({"clipboard_ignore": ["slack"]})
        for name in ("KeePassXC.exe", "1password.exe", "Bitwarden.exe", "slack.exe", "SomePasswordVault.exe"):
            self.assertTrue(qf.ignored_process(name, settings), name)
        self.assertFalse(qf.ignored_process("notepad.exe", settings))
        self.assertFalse(qf.ignored_process("", settings))


class ClipboardStoreTests(TmpDir):
    def test_dedupe_limit_pins_and_persistence(self) -> None:
        store = qf.CLIPBOARD
        for i in range(5):
            store.add(f"item {i}", app="code.exe", limit=3)
        self.assertEqual([r["text"] for r in store.rows()], ["item 4", "item 3", "item 2"])
        store.update(store.rows()[2]["id"], "pin")
        store.add("item 3", limit=3)
        rows = store.rows()
        self.assertEqual(rows[0]["text"], "item 3")
        self.assertEqual(rows[0]["count"], 2, "a repeated copy moves up and counts")
        for i in range(5, 9):
            store.add(f"item {i}", limit=3)
        self.assertIn("item 2", [r["text"] for r in store.rows()], "a pinned item is never pushed out")
        self.assertEqual(sum(1 for r in store.rows() if not r["pinned"]), 3)
        long = "x" * (qf.CLIPBOARD_ITEM_CHARS + 50)
        row = store.add(long)
        self.assertEqual(len(row["text"]), qf.CLIPBOARD_ITEM_CHARS)
        self.assertEqual(row["chars"], len(long))
        self.assertIsNone(store.add("   "))
        found = store.search("ITEM 8")
        self.assertEqual([r["text"] for r in found["items"]], ["item 8"])
        self.assertEqual(store.search("")["items"][0]["text"], "item 2", "pinned first")
        qf.CLIPBOARD._rows = None                        # reload from disk
        self.assertEqual(len(store.rows()), 5)
        self.assertTrue(store.update("", "clear"))
        self.assertEqual([r["text"] for r in store.rows()], ["item 2"], "«Clear» keeps pinned items")
        self.assertTrue(store.update("", "clear_all"))
        self.assertEqual(store.rows(), [])
        self.assertFalse(store.update("nope", "delete"))


class RegionTests(unittest.TestCase):
    def test_crop_box(self) -> None:
        self.assertEqual(qf.crop_box({"x": .25, "y": .5, "w": .5, "h": .25}, (1000, 800)), (250, 400, 750, 600))
        self.assertEqual(qf.crop_box({"x": .75, "y": .75, "w": -.5, "h": -.5}, (1000, 800)), (250, 200, 750, 600))
        self.assertEqual(qf.crop_box({"x": -1, "y": 0, "w": 3, "h": 1}, (100, 100)), (0, 0, 100, 100))
        self.assertIsNone(qf.crop_box({"x": .5, "y": .5, "w": .001, "h": .5}, (1000, 800)), "a click is not a region")
        self.assertIsNone(qf.crop_box({"x": "nan", "y": 0, "w": 1, "h": 1}, (100, 100)))
        self.assertIsNone(qf.crop_box(None, (100, 100)))

    def test_session_crops_the_frozen_image(self) -> None:
        from PIL import Image
        image = Image.new("RGB", (2560, 1440), (10, 20, 30))
        image.paste((250, 0, 0), (1280, 720, 2560, 1440))
        session = qf.RegionSession(image, {"rect": (0, 0, 2560, 1440)}, "ru")
        result = session.finish({"x": .5, "y": .5, "w": .5, "h": .5})
        self.assertTrue(session.done.is_set())
        self.assertEqual((result["width"], result["height"]), (1280, 720))
        self.assertEqual(result["label"], "1280×720")
        crop = Image.open(io.BytesIO(base64.b64decode(result["data"].split(",", 1)[1])))
        self.assertGreater(crop.getpixel((10, 10))[0], 200)
        self.assertEqual(session.finish(None), result, "a second answer does not change the first")
        self.assertTrue(session.preview().startswith(b"\xff\xd8"))
        cancelled = qf.RegionSession(image, {"rect": (0, 0, 1, 1)}, "en")
        self.assertEqual(cancelled.finish(None), {"cancelled": True})


class DocumentTests(unittest.TestCase):
    @staticmethod
    def data_url(blob: bytes, mime: str = "application/octet-stream") -> str:
        return f"data:{mime};base64," + base64.b64encode(blob).decode()

    def test_docx_txt_and_errors(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Срок: 1 марта</w:t></w:r></w:p>"
                                                  "<w:p><w:r><w:t>Сумма &amp; НДС</w:t></w:r></w:p></w:body></w:document>")
        out = qf.document_text("договор.docx", self.data_url(buf.getvalue()))
        self.assertEqual(out["text"], "Срок: 1 марта\nСумма & НДС")
        self.assertFalse(out["truncated"])
        long = ("начало " + "x" * 70000 + " конец").encode("utf-8")
        out = qf.document_text("log.txt", self.data_url(long, "text/plain"))
        self.assertTrue(out["truncated"])
        self.assertTrue(out["text"].startswith("начало") and out["text"].endswith("конец"), "head and tail are kept")
        self.assertLessEqual(len(out["text"]), qf.DOCUMENT_MAX_CHARS)
        self.assertEqual(qf.document_text("a.doc", self.data_url(b"x"), "en")["code"], "format")
        self.assertEqual(qf.document_text("a.docx", self.data_url(b"not a zip"), "en")["code"], "parse")
        self.assertEqual(qf.document_text("a.txt", "garbage", "en")["code"], "empty")
        self.assertEqual(qf.document_text("a.bin", self.data_url(b"\x00\x00"), "en")["code"], "empty")

    def test_pdf_with_and_without_text(self) -> None:
        sys.path.insert(0, str(ROOT / "tests"))
        from round27_quick_features_shots import tiny_pdf
        out = qf.document_text("c.pdf", self.data_url(tiny_pdf(), "application/pdf"), "en")
        self.assertIn("Contract deadline", out.get("text", ""), out)
        scan = tiny_pdf().replace(b"(Contract deadline: 1 March) Tj", b"")
        out = qf.document_text("scan.pdf", self.data_url(scan, "application/pdf"), "en")
        self.assertIn(out.get("code"), ("scan", "parse"), out)


class StickerTests(TmpDir):
    def test_normalize_and_title(self) -> None:
        row = qf.normalize_sticker({"text": "## **Итог**\n- пункт", "color": "neon", "geometry": {"x": "10", "w": 5, "h": "1e9"}})
        self.assertEqual(row["color"], "")
        self.assertEqual(row["geometry"], {"x": 10, "w": 160, "h": 2000})
        self.assertIsNone(qf.normalize_sticker({"text": "  "}))
        self.assertEqual(qf.sticker_title("```py\ncode\n```"), "code")
        self.assertEqual(qf.sticker_title("## **Итог** дня"), "Итог дня")
        self.assertEqual(qf.sticker_title("", "en"), "Sticker")


class ApiTests(TmpDir):
    def client(self, manager=None):
        from starlette.testclient import TestClient
        from app.playground_api import create_app
        app = create_app()
        app.state.quick_ask = manager
        return TestClient(app, base_url="http://127.0.0.1")

    def test_routes_without_the_native_side(self) -> None:
        c = self.client()
        state = c.get("/api/quick/features?lang=en").json()
        self.assertFalse(state["native"])
        self.assertEqual(len(state["snippets"]), 6)
        bad = c.post("/api/quick/features/settings", json={"settings": {"region_hotkey": "Alt+Foo"}, "lang": "en"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["field"], "region_hotkey")
        ok = c.post("/api/quick/features/settings", json={"settings": {"clipboard": True, "evil": 1}}).json()
        self.assertTrue(ok["settings"]["clipboard"])
        self.assertNotIn("evil", ok["settings"])
        use = c.post("/api/quick/features/snippets/use", json={"id": "s0", "insert": True, "lang": "en"}).json()
        self.assertEqual(use["text"], datetime.now().strftime("%Y-%m-%d"))
        self.assertFalse(use["inserted"])
        self.assertEqual(c.post("/api/quick/features/snippets/use", json={"id": "s2"}).json()["kind"], "ai")
        self.assertEqual(c.post("/api/quick/features/snippets/use", json={"id": "nope"}).status_code, 404)
        self.assertEqual(c.post("/api/quick/features/snippets", json={"snippets": "x"}).status_code, 400)
        region = c.post("/api/quick/features/region", json={"lang": "en"})
        self.assertEqual(region.status_code, 400)
        self.assertIn("Windows", region.json()["error"])
        self.assertEqual(c.get("/api/quick/features/region/result").json(), {"cancelled": True})
        created = c.post("/api/quick/features/stickers", json={"action": "create", "text": "Ответ", "title": "Вопрос"}).json()
        sid = created["sticker"]["id"]
        self.assertFalse(created["native"])
        self.assertEqual(c.post("/api/quick/features/stickers", json={"action": "update", "id": sid, "color": "green", "text": "Новый"}).json()["sticker"]["text"], "Новый")
        self.assertFalse(c.post("/api/quick/features/stickers", json={"action": "close", "id": sid}).json()["sticker"]["open"])
        self.assertEqual(c.get(f"/api/quick/features/stickers/{sid}").json()["sticker"]["color"], "green")
        self.assertEqual(c.post("/api/quick/features/stickers", json={"action": "delete", "id": sid}).status_code, 200)
        self.assertEqual(c.get(f"/api/quick/features/stickers/{sid}").status_code, 404)
        self.assertEqual(c.post("/api/quick/features/stickers", json={"action": "create", "text": " "}).status_code, 400)
        self.assertEqual(c.post("/api/quick/features/clipboard", json={"action": "paste", "id": "x"}).status_code, 404)
        doc = c.post("/api/quick/features/document", json={"name": "a.txt", "data": "data:text/plain;base64," + base64.b64encode(b"hello").decode()})
        self.assertEqual(doc.json()["text"], "hello")
        for page in ("/quick/region.html", "/quick/sticker.html", "/quick/plugins/features.js"):
            self.assertEqual(c.get(page).status_code, 200, page)

    def test_stickers_on_screen_are_capped(self) -> None:
        c = self.client()
        for i in range(qf.STICKERS_OPEN + 2):
            c.post("/api/quick/features/stickers", json={"action": "create", "text": f"s{i}"})
        rows = qf.load_stickers()
        self.assertEqual(sum(1 for r in rows if r["open"]), qf.STICKERS_OPEN)
        self.assertTrue(rows[0]["open"], "the newest one is on screen")


# ---------------------------------------------------------------- Windows side with fakes

class FakeApi:
    """Just enough of quick_ask.Win32 for quick_features_win."""

    def __init__(self) -> None:
        self.clip_text = "user's clipboard"
        self.formats = {13}
        self.keys: list = []
        self.sets: list = []

    def clipboard_formats(self):
        return set(self.formats)

    def get_text(self):
        return self.clip_text

    def set_text(self, text):
        self.sets.append(text)
        self.clip_text = text
        return True

    def chord(self, *vks):
        self.keys.append(vks)


class FakeFormats:
    def __init__(self, private=False, pid=42) -> None:
        self._private, self.pid = private, pid

    def private(self):
        return self._private

    def owner_pid(self):
        return self.pid


class WatcherTests(TmpDir):
    def watcher(self, *, private=False, pid=42, app="notepad.exe", text="hello", settings=None):
        api = FakeApi()
        api.clip_text = text
        watcher = qw.ClipboardWatcher(api)
        watcher.settings = qf.normalize_settings(settings or {"clipboard": True})
        watcher._formats = FakeFormats(private, pid)
        names = mock.patch.object(qw, "process_name", lambda _pid: app)
        names.start()
        self.addCleanup(names.stop)
        return watcher, api

    def test_records_ordinary_text(self) -> None:
        watcher, _api = self.watcher()
        row = watcher.capture(own_pid=1)
        self.assertEqual(row["text"], "hello")
        self.assertEqual(row["app"], "notepad.exe")

    def test_never_records_private_sources(self) -> None:
        cases = [dict(private=True), dict(app="KeePassXC.exe"), dict(pid=1), dict(text="sk-proj-" + "z" * 40),
                 dict(app="slack.exe", settings={"clipboard": True, "clipboard_ignore": ["slack"]})]
        for case in cases:
            watcher, _api = self.watcher(**case)
            self.assertIsNone(watcher.capture(own_pid=1), case)
        self.assertEqual(qf.CLIPBOARD.rows(), [])
        watcher, _api = self.watcher(text="sk-proj-" + "z" * 40, settings={"clipboard": True, "clipboard_skip_secrets": False})
        self.assertIsNotNone(watcher.capture(own_pid=1), "the secret filter can be turned off")

    def test_images_and_files_are_not_read(self) -> None:
        watcher, api = self.watcher()
        api.formats = {2, 8}          # CF_BITMAP, CF_DIB
        self.assertIsNone(watcher.capture(own_pid=1))

    def test_selection_capture_is_not_recorded(self) -> None:
        from app import quick_selection
        self.assertTrue(hasattr(quick_selection, "clipboard_busy"))
        with mock.patch.object(quick_selection, "clipboard_busy", lambda: True):
            self.assertTrue(qw.selection_busy())
        with mock.patch.object(quick_selection, "clipboard_busy", lambda: False):
            self.assertFalse(qw.selection_busy())
        source = Path(qw.__file__).read_text("utf-8")
        self.assertIn('self.settings.get("clipboard_paused") or selection_busy()', source)

    def test_restart_after_stop_keeps_running(self) -> None:
        watcher, _api = self.watcher()
        watcher._thread = threading.Thread(target=time.sleep, args=(0.3,))
        watcher._thread.start()
        watcher.stop()
        watcher.start({"clipboard": True})
        self.assertFalse(watcher._stop.is_set(), "a quick off → on does not leave the watcher stopped")
        watcher._thread.join()


class FakeManager:
    def __init__(self) -> None:
        self.api = FakeApi()
        self.inserted: list = []
        self.visible = False

    def insert_text(self, text):
        self.inserted.append(text)
        self.api.clip_text = text
        return True

    def copy_text(self, text):
        return self.api.set_text(text)

    def load_prefs(self):
        return {"language": "en"}


class RuntimeTests(unittest.TestCase):
    def test_insert_restores_the_clipboard_and_places_the_caret(self) -> None:
        manager = FakeManager()
        rt = qw.Runtime(manager)
        self.assertTrue(rt.insert("Hi !\nBye", cursor=5))
        self.assertEqual(manager.inserted, ["Hi !\nBye"])
        self.assertEqual(manager.api.keys, [(qw.VK_LEFT,)] * 5)
        self.assertEqual(manager.api.clip_text, "user's clipboard", "the user's text is back on the clipboard")

    def test_insert_leaves_images_alone(self) -> None:
        manager = FakeManager()
        manager.api.formats = {13, 8}             # text + an image: we cannot restore the image → leave it
        rt = qw.Runtime(manager)
        rt.insert("x", 0)
        self.assertEqual(manager.api.sets, [])

    def test_contain(self) -> None:
        self.assertEqual(qw.contain(1900, -50, 400, 300, (0, 0, 1920, 1040)), (1520, 0, 400, 300))
        self.assertEqual(qw.contain(0, 0, 5000, 300, (0, 0, 1920, 1040)), (0, 0, 1920, 300))

    def test_feature_hotkey_must_differ_from_quick_ask(self) -> None:
        manager = FakeManager()
        manager.load_prefs = lambda: {"quick_ask_hotkey": "Alt+Space"}
        rt = qw.Runtime(manager)
        status = rt._set_hotkey("region", "Alt+Space", lambda: None, "en")
        self.assertEqual(status["code"], "quick_ask")


class RegionRuntimeTests(unittest.TestCase):
    """pick_region with a fake Windows: the open window is excluded from the capture, not hidden (it keeps the question)."""

    def run_pick(self, build: int):
        from PIL import Image
        manager = FakeManager()
        manager.visible, manager.hwnd, manager.build = True, 77, build
        calls = []
        api = manager.api
        api.SetWindowDisplayAffinity = object()
        api.DwmFlush = lambda: 0
        api.monitors = lambda: [{"rect": (0, 0, 800, 600), "work": (0, 0, 800, 560), "dpi": 96, "primary": True}]
        api.cursor = lambda: (10, 10)
        api.take_foreground = lambda hwnd: calls.append(("foreground", hwnd))
        manager._affinity = lambda hwnd, value: calls.append(("affinity", value)) or True
        manager.hide = lambda restore_focus=True: calls.append(("hide",)) or True
        manager.show = lambda source: calls.append(("show", source))
        manager.raise_to_top = lambda: calls.append(("raise",))
        manager._push = lambda script: calls.append(("push", script))
        rt = qw.Runtime(manager)

        def show_overlay(session):
            calls.append(("overlay",))
            threading.Timer(0.05, session.finish, args=({"x": 0, "y": 0, "w": .5, "h": .5},)).start()
            return True
        rt._show_overlay = show_overlay
        rt._hide_overlay = lambda: calls.append(("overlay-hide",))
        fake_grab = mock.Mock()
        fake_grab.grab = lambda **kw: calls.append(("grab", kw["bbox"])) or Image.new("RGB", (800, 600), "white")
        with mock.patch.dict(sys.modules, {"PIL.ImageGrab": fake_grab}), mock.patch("PIL.ImageGrab", fake_grab, create=True):
            result = rt.pick_region("en")
        return result, calls

    def test_excluded_not_hidden(self) -> None:
        result, calls = self.run_pick(22631)
        self.assertEqual((result["width"], result["height"]), (400, 300))
        names = [c[0] for c in calls]
        self.assertNotIn("hide", names)
        self.assertNotIn("show", names)
        self.assertEqual([c[1] for c in calls if c[0] == "affinity"], [0x11, 0x0], "excluded for the grab only")
        self.assertLess(names.index("affinity"), names.index("grab"))
        self.assertIn("raise", names)
        self.assertIn(("foreground", 77), calls)

    def test_old_windows_hides_and_shows(self) -> None:
        result, calls = self.run_pick(18363)
        names = [c[0] for c in calls]
        self.assertIn("hide", names)
        self.assertIn(("show", "api"), calls)
        self.assertNotIn("affinity", names)

    def test_escape_hook_knows_our_extra_windows(self) -> None:
        from app import quick_ask
        manager = quick_ask.QuickAskManager.__new__(quick_ask.QuickAskManager)
        manager.api = mock.Mock(GetForegroundWindow=lambda: 555)
        manager.visible, manager.pinned, manager.hwnd = True, False, 1
        self.assertTrue(manager._escape_decide(0x1B, 0x100, 0))
        manager.extra_windows = (555,)
        self.assertFalse(manager._escape_decide(0x1B, 0x100, 0), "Esc in the region overlay must not close Quick Ask")


class BuildAndWiringTests(unittest.TestCase):
    def test_manifest_spec_and_hooks(self) -> None:
        manifest = json.loads((QUICK / "plugins" / "index.json").read_text("utf-8"))
        row = next(p for p in manifest["plugins"] if p.get("id") == "features")
        self.assertEqual(row["backend"], "app.quick_features")
        for key in ("src", "css", "settings", "settings_css"):
            self.assertTrue((QUICK / "plugins" / row[key]).exists(), row[key])
        for name in ("MemuAI.spec", "build.py"):
            text = (ROOT / name).read_text("utf-8")
            for module in ("app.quick_features", "app.quick_features_win", "app.telegram_media"):
                self.assertIn(module, text, f"{module} in {name}")
        self.assertIn("quick_features_win.destroy_all", (ROOT / "memuai.py").read_text("utf-8"))
        quick = (QUICK / "quick.js").read_text("utf-8")
        for hook in ("addAction, addAnswerAction, addContext", 'emit("file"', "turn.prompt || text", "addImage:"):
            self.assertIn(hook, quick)
        self.assertIn(".pdf,.docx", (QUICK / "index.html").read_text("utf-8"))

    def test_every_ui_string_has_both_languages(self) -> None:
        import re
        for name in ("plugins/features.js", "plugins/features-settings.js", "sticker.js", "region.js"):
            text = (QUICK / name).read_text("utf-8")
            for call in re.findall(r"\bt(?:r)?\(\s*(\"[^\"]*\"|`[^`]*`)\s*\)", text):
                self.fail(f"{name}: one-language string {call}")

    def test_selection_limit_keeps_the_tail(self) -> None:
        from app import quick_ask_api
        selection = "HEAD " + "x" * 70000 + " TAIL"
        messages = quick_ask_api.build_messages(text="q", history=[], images=[], system="s", selection=selection)
        content = messages[-1]["content"]
        self.assertIn("HEAD", content)
        self.assertIn("TAIL", content, "a long attached file keeps its end (was cut at 8000 characters)")
        self.assertLess(len(content), quick_ask_api.MAX_SELECTION + 400)


class HeadlessPageTests(unittest.TestCase):
    def test_window_views_and_pages(self) -> None:
        chrome = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
        if not os.path.exists(chrome):
            self.skipTest("no headless Chromium")
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("no playwright")
        out = tempfile.mkdtemp(prefix="r27qf-shots-")
        env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="*")
        proc = subprocess.run([sys.executable, str(ROOT / "tests" / "round27_quick_features_shots.py"), out],
                              cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
        shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
