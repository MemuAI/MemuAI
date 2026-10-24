"""Round 29 safe mode (app/safe_mode.py): startup decision, marker, crash guard, routes, hooks, restart."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-safe-")

from app import safe_mode  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="r29safe-"))
        self.patch = mock.patch.object(safe_mode, "_memu_dir", lambda: self.dir)
        self.patch.start()
        safe_mode.deactivate_for_tests()

    def tearDown(self) -> None:
        safe_mode.deactivate_for_tests()
        self.patch.stop()


class StartupDecisionTest(_Base):
    def never(self, *_a) -> bool:
        raise AssertionError("must not ask")

    def test_normal_start_is_not_safe(self) -> None:
        state = safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)
        self.assertFalse(state["active"])
        self.assertFalse(safe_mode.is_safe_mode())

    def test_flag_env_and_marker(self) -> None:
        self.assertTrue(safe_mode.decide_at_startup(True, ask=self.never, shift=lambda: False)["active"])
        self.assertEqual(safe_mode.status()["source"], "flag")
        safe_mode.deactivate_for_tests()
        with mock.patch.dict(os.environ, {safe_mode.ENV: "1"}):
            self.assertTrue(safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)["active"])
        safe_mode.deactivate_for_tests()
        safe_mode.set_marker(once=False)
        self.assertTrue(safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)["active"])
        self.assertTrue(safe_mode.marker_state()["exists"])            # «always» stays

    def test_once_marker_is_consumed(self) -> None:
        safe_mode.set_marker(once=True)
        self.assertTrue(safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)["active"])
        self.assertFalse(safe_mode.marker_state()["exists"])

    def test_empty_marker_file_means_always(self) -> None:
        safe_mode.marker_path().parent.mkdir(parents=True, exist_ok=True)
        safe_mode.marker_path().write_text("", encoding="utf-8")
        self.assertEqual(safe_mode.marker_state(), {"exists": True, "once": False})

    def test_shift_asks_and_respects_the_answer(self) -> None:
        self.assertFalse(safe_mode.decide_at_startup(False, ask=lambda *_: False, shift=lambda: True)["active"])
        safe_mode.mark_healthy()
        self.assertTrue(safe_mode.decide_at_startup(False, ask=lambda *_: True, shift=lambda: True)["active"])
        self.assertEqual(safe_mode.status()["source"], "shift")

    def test_headless_never_asks(self) -> None:
        for _ in range(4):
            state = safe_mode.decide_at_startup(False, interactive=False, ask=self.never, shift=lambda: True)
        self.assertFalse(state["active"])

    def test_crash_guard_offers_safe_mode_after_two_unfinished_starts(self) -> None:
        asked = []
        for _ in range(safe_mode.CRASH_THRESHOLD):
            safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)   # starts that never got healthy
        state = safe_mode.decide_at_startup(False, ask=lambda t, x: asked.append(x) or True, shift=lambda: False)
        self.assertTrue(state["active"])
        self.assertEqual(safe_mode.status()["source"], "crash")
        self.assertEqual(len(asked), 1)

    def test_declined_crash_offer_is_not_repeated(self) -> None:
        for _ in range(safe_mode.CRASH_THRESHOLD):
            safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)
        self.assertFalse(safe_mode.decide_at_startup(False, ask=lambda *_: False, shift=lambda: False)["active"])
        # the next start does not ask again
        self.assertFalse(safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)["active"])

    def test_healthy_start_resets_the_guard(self) -> None:
        safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)
        safe_mode.mark_healthy()
        safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)
        safe_mode.clean_exit()
        safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)   # still below the threshold

    def test_broken_files_never_raise(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        safe_mode.guard_path().write_text("{not json", encoding="utf-8")
        safe_mode.marker_path().write_text("[1, 2", encoding="utf-8")
        state = safe_mode.decide_at_startup(False, ask=self.never, shift=lambda: False)
        self.assertTrue(state["active"])                                    # a marker file of any content


class SessionAndRestartTest(_Base):
    def test_session_file_tracks_a_live_process(self) -> None:
        self.assertFalse(safe_mode.session_active())
        safe_mode.activate("flag")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(safe_mode.ENV, None)
            self.assertTrue(safe_mode.session_active())                    # this very process
            safe_mode.clean_exit()
            self.assertFalse(safe_mode.session_path().exists())

    def test_stale_session_is_dropped(self) -> None:
        safe_mode.session_path().parent.mkdir(parents=True, exist_ok=True)
        safe_mode.session_path().write_text(json.dumps({"pid": 999999991}), encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(safe_mode.ENV, None)
            self.assertFalse(safe_mode.session_active())
        self.assertFalse(safe_mode.session_path().exists())

    def test_launch_command_from_source(self) -> None:
        argv, cwd = safe_mode.launch_command(True, 42)
        self.assertEqual(argv[-3:], ["--wait-pid", "42", "--safe-mode"])
        self.assertTrue(argv[1].endswith("memuai.py"))
        argv, _ = safe_mode.launch_command(False, 42)
        self.assertNotIn("--safe-mode", argv)

    def test_relaunch_needs_the_app_and_drops_the_env(self) -> None:
        class App:
            class state:  # noqa: N801
                shutdown_callback = None
        self.assertFalse(safe_mode.relaunch(App, safe=True)["ok"])
        calls = []
        App.state.shutdown_callback = staticmethod(lambda: None)
        with mock.patch.dict(os.environ, {safe_mode.ENV: "1"}):
            result = safe_mode.relaunch(App, safe=False, popen=lambda argv, **kw: calls.append((argv, kw)))
        self.assertTrue(result["ok"])
        self.assertNotIn(safe_mode.ENV, calls[0][1]["env"])

    def test_disabled_items_are_bilingual_without_cyrillic_in_english(self) -> None:
        rows = safe_mode.disabled_items()
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            self.assertTrue(row["ru"] and row["en"])
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", row["en"] + row["note_en"]), row["id"])


class RoutesTest(_Base):
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        safe_mode.register(app)
        return TestClient(app)

    def test_boot_status_marker_and_plugins(self) -> None:
        c = self.client()
        self.assertIn('"active": false', c.get("/api/pg/safe-mode/boot.js").text)
        self.assertFalse(c.get("/api/pg/safe-mode").json()["active"])
        self.assertNotEqual(c.get("/quick/plugins/index.json").json().get("safe_mode"), True)
        self.assertEqual(c.post("/api/pg/safe-mode/marker", json={"mode": "bogus"}).status_code, 400)
        self.assertTrue(c.post("/api/pg/safe-mode/marker", json={"mode": "once"}).json()["marker"]["once"])
        self.assertFalse(c.post("/api/pg/safe-mode/marker", json={"mode": "off"}).json()["marker"]["exists"])
        safe_mode.activate("flag")
        boot = c.get("/api/pg/safe-mode/boot.js")
        self.assertEqual(boot.headers["cache-control"], "no-store")
        self.assertIn('"active": true', boot.text)
        status = c.get("/api/pg/safe-mode").json()
        self.assertTrue(status["active"])
        self.assertIn("modules", [row["id"] for row in status["disabled"]])
        self.assertEqual(c.get("/quick/plugins/index.json").json()["plugins"], [])

    def test_restart_outside_the_app_is_refused_and_clears_marker(self) -> None:
        c = self.client()
        safe_mode.set_marker(once=False)
        self.assertEqual(c.post("/api/pg/app/restart", json={"mode": "normal"}).status_code, 400)
        self.assertFalse(safe_mode.marker_state()["exists"])


class HooksTest(_Base):
    def test_extension_points_ask_is_safe_mode(self) -> None:
        from app import quick_ask_api
        safe_mode.activate("flag")
        self.assertEqual(quick_ask_api.plugin_manifest()["plugins"], [])
        expected = {
            "app/playground_api.py": ["safe_mode.register(app)", '"safe_mode": True'],
            "app/mcp_client.py": ["is_safe_mode()"],
            "app/panels.py": ["is_safe_mode()"],
            "app/module_monitor.py": ["session_active()"],
            "app/assistant_commands.py": ["is_safe_mode()"],
            "memuai.py": ["decide_at_startup(", "wait_for_pid"],
        }
        for path, needles in expected.items():
            text = (ROOT / path).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{path}: {needle}")
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertLess(api.index("safe_mode.register(app)"), api.index('app.mount("/", StaticFiles'))

    def test_page_build_installer_and_docs(self) -> None:
        index = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index("/api/pg/safe-mode/boot.js"), index.index("/safe-mode.js?v="))
        self.assertIn("/safe-mode.css?v=", index)
        for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
            self.assertIn("'app.safe_mode'", (ROOT / spec).read_text(encoding="utf-8"), spec)
        self.assertIn('"app.safe_mode"', (ROOT / "build.py").read_text(encoding="utf-8"))
        self.assertIn('args="--safe-mode"', (ROOT / "installer" / "products.py").read_text(encoding="utf-8"))
        pages = [p for g in json.loads((ROOT / "app" / "docs" / "index.json").read_text(encoding="utf-8"))["groups"]
                 for p in g["pages"]]
        self.assertIn("safe-mode", pages)
        for lang in ("ru", "en"):
            page = (ROOT / "app" / "docs" / lang / "safe-mode.md").read_text(encoding="utf-8")
            self.assertIn("--safe-mode", page)
            self.assertIn("MEMUAI_SAFE_MODE", page)
        js = (ROOT / "app" / "pgstatic" / "safe-mode.js").read_text(encoding="utf-8")
        self.assertIn('MemuDocs.open("safe-mode")', js)


if __name__ == "__main__":
    unittest.main()
