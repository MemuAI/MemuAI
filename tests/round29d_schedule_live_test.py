"""Round 29 (r29d-sched-media): «Планирование 2.0» end to end on REAL models.

Skipped unless ``YANDEX_BASE`` and ``YANDEX_KEY`` are set in the environment (keys are never written
anywhere). Starts MemuAI Proxy + the MemuAI window backend + Mini App in this process on an isolated
``MEMUAI_DATA_DIR`` (``tests/ui_lang_scan.Servers``), adds the endpoint as a Custom provider through the
Proxy API — the way a user does — and then, through the same API the «Planning» panel uses:

* a chat-answer rule task → «Run now» → the Proxy scheduler runs it on the real model, the history row is
  ``ok`` with a non-empty answer and is marked as a test run; the task's ``last_status`` is updated;
* a MemuCode action in a temporary folder (``memucode -p`` subprocess, plan mode) reads a file there and
  answers with its contents;
* «Create in words»: ``/api/plan/parse`` turns a Russian phrase into a valid rule (daily 16:00 + process);
* Do For You (only when ``MEMU_LIVE_DFY=1`` — needs a vision model and a Chromium): the Proxy hands the
  task to the window, the DFY agent opens a page (a made-up host mapped to a local server) and reports
  the order status shown on it.

Model ids can be overridden: ``MEMU_LIVE_MODEL`` (fast text model) and ``MEMU_LIVE_VISION_MODEL``.
"""
from __future__ import annotations

import http.server
import os
import shutil
import socket
import re
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# The Yandex folder prefix comes from YANDEX_MODEL (gpt://<folder>/<model>/latest) — no account ids in the repo.
FOLDER = re.sub(r"[^/]+/[^/]+$", "", os.environ.get("YANDEX_MODEL", "")) or "gpt://<folder>/"
# Round 31: the Yandex key answers PermissionDenied — the XKIRO endpoint (OpenAI-compatible, free qwen
# models, a pool of keys) is used when its env is set; Yandex stays the fallback.
XKIRO = bool(os.environ.get("XKIRO_BASE") and os.environ.get("XKIRO_KEY")) and os.environ.get("MEMU_LIVE_YANDEX") != "1"
if XKIRO:
    TEXT_MODEL = os.environ.get("MEMU_LIVE_MODEL") or "qwen/qwen3.7-flash:free"
    VISION_MODEL = os.environ.get("MEMU_LIVE_VISION_MODEL") or "qwen/qwen3.8-max:free"
else:
    TEXT_MODEL = os.environ.get("MEMU_LIVE_MODEL") or FOLDER + "deepseek-v4.1-flash/latest"
    VISION_MODEL = os.environ.get("MEMU_LIVE_VISION_MODEL") or FOLDER + "qwen3.6-35b-a3b/latest"
TMP = os.environ.get("MEMU_TEST_TMP") or None     # /tmp can be wiped in shared sandboxes
LIVE = XKIRO or bool(os.environ.get("YANDEX_BASE") and os.environ.get("YANDEX_KEY"))
CHROMIUM = shutil.which("chromium") or ("/workspace/work/chromium/chrome"
                                        if os.path.exists("/workspace/work/chromium/chrome") else None)


@unittest.skipUnless(LIVE, "XKIRO_* / YANDEX_* are not set — live scheduling test skipped")
class LiveSchedulingTest(unittest.TestCase):
    srv = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._old_data = os.environ.get("MEMUAI_DATA_DIR")
        cls.data = tempfile.mkdtemp(prefix="memu-r29d-plan-live-", dir=TMP)
        os.environ["MEMUAI_DATA_DIR"] = cls.data
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "tests"))
        import httpx
        import ui_lang_scan
        cls.srv = ui_lang_scan.Servers().start()
        cls.client = httpx.Client(timeout=60, trust_env=False)
        ids = ", ".join(sorted({TEXT_MODEL, VISION_MODEL}))
        if XKIRO:
            pool = "\n".join(os.environ[k] for k in ("XKIRO_KEY", "XKIRO_KEY2", "XKIRO_KEY3") if os.environ.get(k))
            fields = {"name": "Live", "base_url": os.environ["XKIRO_BASE"], "api_key": os.environ["XKIRO_KEY"],
                      "api_keys": pool, "models": ids}
        else:
            fields = {"name": "Live", "base_url": os.environ["YANDEX_BASE"], "api_key": os.environ["YANDEX_KEY"],
                      "models": ids}
        r = cls.client.post(cls.srv.proxy + "/api/admin/accounts", json={
            "provider": "custom", "label": "live", "fields": fields})
        assert r.status_code < 300, r.text[:300]
        cls.model = cls._catalog_id(TEXT_MODEL)
        cls.vision = cls._catalog_id(VISION_MODEL)
        cls.client.post(cls.srv.pg + "/api/pg/preferences", json={
            "profile_onboarded": True, "language": "ru", "updates_enabled": False, "default_model": cls.model})

    @classmethod
    def _catalog_id(cls, raw: str) -> str:
        for _ in range(30):
            try:
                models = cls.client.get(cls.srv.proxy + "/v1/models").json().get("data") or []
            except Exception:
                models = []
            for m in models:
                if str(m.get("id", "")).endswith(raw):
                    return m["id"]
            time.sleep(1)
        return "live-" + raw

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.srv is not None:
            cls.srv.stop()
        try:        # the DFY browser is a separate process: do not leave it running after the test
            import psutil
            for proc in psutil.process_iter(["cmdline"]):
                if any(cls.data in part for part in (proc.info.get("cmdline") or [])):
                    proc.kill()
        except Exception:
            pass
        cls.client.close()
        if cls._old_data is not None:
            os.environ["MEMUAI_DATA_DIR"] = cls._old_data
        shutil.rmtree(cls.data, ignore_errors=True)

    # ------------------------------------------------------------------ helpers
    def save(self, task: dict) -> str:
        r = self.client.post(self.srv.pg + "/api/plan/save", json={"task": task, "lang": "ru"})
        self.assertEqual(r.status_code, 200, r.text[:400])
        return r.json()["id"]

    def run_and_wait(self, task_id: str, timeout: float = 240) -> dict:
        r = self.client.post(self.srv.pg + "/api/plan/run", json={"id": task_id})
        self.assertEqual(r.status_code, 200, r.text[:300])
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = self.client.get(self.srv.pg + "/api/plan/history", params={"id": task_id}).json()["results"]
            if rows:
                return rows[0]
            time.sleep(2)
        self.fail(f"task {task_id} produced no result in {timeout}s")

    def task_row(self, task_id: str) -> dict:
        tasks = self.client.get(self.srv.pg + "/api/plan/tasks", params={"lang": "ru"}).json()["tasks"]
        return next(t for t in tasks if t["id"] == task_id)

    FAR_RULE = {"when": [{"type": "daily", "times": ["03:17"], "days": [7]}]}

    # ------------------------------------------------------------------ tests
    def test_1_chat_task_run_now(self) -> None:
        tid = self.save({"name": "Live chat", "kind": "chat", "model": self.model, "rule": self.FAR_RULE,
                         "prompt": "Ответь одним словом: какого цвета ясное дневное небо?"})
        row = self.run_and_wait(tid)
        self.assertTrue(row.get("ok"), row)
        self.assertTrue(str(row.get("text") or "").strip(), row)
        answer = str(row.get("text")).lower()
        self.assertTrue(any(w in answer for w in ("голуб", "син", "blue")), answer[:300])
        self.assertTrue(row.get("test"), "a «Run now» result is marked as a test run")
        for _ in range(10):
            task = self.task_row(tid)
            if task.get("run_count") is not None or task.get("last_status"):
                break
            time.sleep(1)
        self.assertNotEqual(task.get("status_kind") or task.get("state", {}).get("status"), "error")

    def test_2_memucode_action_in_temp_folder(self) -> None:
        folder = tempfile.mkdtemp(prefix="memu-r29d-code-", dir=TMP)
        try:
            Path(folder, "secret_word.txt").write_text("PELICAN-42\n", encoding="utf-8")
            tid = self.save({"name": "Live MemuCode", "kind": "memucode", "model": self.model, "rule": self.FAR_RULE,
                             "project_path": folder, "permission_mode": "plan",
                             "prompt": "Прочитай файл secret_word.txt в этой папке и ответь только его содержимым."})
            row = self.run_and_wait(tid, timeout=420)
            self.assertTrue(row.get("ok"), row)
            self.assertIn("PELICAN-42", str(row.get("text")), row)
            meta = row.get("memucode") or {}
            self.assertEqual(os.path.realpath(meta.get("root", "")), os.path.realpath(folder))
            self.assertTrue(meta.get("session_id"), "the session id is kept for «memucode -r ID»")
            self.assertGreaterEqual(int(meta.get("tools") or 0), 1, "MemuCode used a tool to read the file")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_3_create_in_words(self) -> None:
        r = self.client.post(self.srv.pg + "/api/plan/parse", json={
            "text": "каждый день в 16:00 если запущена Dota 2 напоминай размяться", "lang": "ru", "model": self.model},
            timeout=180)
        self.assertEqual(r.status_code, 200, r.text[:400])
        body = r.json()
        when = body["rule"]["when"][0]
        self.assertEqual(when["type"], "daily")
        self.assertIn("16:00", when.get("times") or [])
        self.assertTrue(any(c.get("type") == "process" and "dota" in str(c.get("name", "")).lower()
                            for c in body["rule"].get("only_if") or []), body["rule"])
        self.assertTrue(body.get("summary"))

    @unittest.skipUnless(os.environ.get("MEMU_LIVE_DFY") == "1" and CHROMIUM,
                         "MEMU_LIVE_DFY=1 not set or no Chromium")
    def test_4_dfy_task_via_window(self) -> None:
        # The DFY browser refuses localhost / private literal addresses by design, and the sandbox has no direct
        # internet: a Chromium wrapper maps the made-up host «shop.memu.test» to a local page server.
        page = tempfile.mkdtemp(prefix="memu-r29d-dfy-", dir=TMP)
        Path(page, "index.html").write_text(
            "<html><head><title>Shop</title></head><body style='font:28px sans-serif;padding:40px'><h1>Shop</h1>"
            "<p>Your order <b>#58213</b> has status: <b>SHIPPED</b></p></body></html>", encoding="utf-8")

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=page, **k)

            def log_message(self, *a):
                pass

        httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        wrapper = Path(page, "chromium-wrapper")
        wrapper.write_text("#!/bin/sh\nexec %s --no-proxy-server --host-resolver-rules='MAP shop.memu.test 127.0.0.1' \"$@\"\n"
                           % CHROMIUM, encoding="utf-8")
        wrapper.chmod(0o755)
        old = os.environ.get("MEMUAI_BROWSER_EXECUTABLE")
        os.environ["MEMUAI_BROWSER_EXECUTABLE"] = str(wrapper)
        # MemuAI's own address check resolves the host first (private addresses are refused); the made-up
        # host is reported as a public address to it, while Chromium's resolver rule sends it to the page server.
        from app import automation_runtime
        real_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host, *a, **k):
            if host == "shop.memu.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", a[0] if a else 80))]
            return real_getaddrinfo(host, *a, **k)

        patch = mock.patch.object(automation_runtime.socket, "getaddrinfo", fake_getaddrinfo)
        patch.start()
        try:
            self.client.post(self.srv.pg + "/api/pg/preferences", json={
                "dfy_enabled": True, "dfy_model": self.vision, "dfy_confirm_mode": "never"})
            url = f"http://shop.memu.test:{httpd.server_address[1]}/"
            tid = self.save({"name": "Live DFY", "kind": "dfy", "model": self.vision, "rule": self.FAR_RULE,
                             "prompt": f"Открой {url} и сообщи статус заказа, указанный на странице."})
            row = self.run_and_wait(tid, timeout=420)
            self.assertTrue(row.get("ok"), row)
            self.assertEqual(row.get("kind"), "dfy")
            self.assertTrue(row.get("dfy_task"), "the history links the live DFY task")
            self.assertIn("SHIPPED", str(row.get("text")).upper(), row)
        finally:
            patch.stop()
            if old is None:
                os.environ.pop("MEMUAI_BROWSER_EXECUTABLE", None)
            else:
                os.environ["MEMUAI_BROWSER_EXECUTABLE"] = old
            httpd.shutdown()
            shutil.rmtree(page, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
