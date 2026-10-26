"""Media and handoff contracts that the shipped host and modules really honour.

The upstream double only stands in for the admin service. ``proxy_sse``, the
module message handler, ``downloadTextFile`` and the Design screen are the files
MemuAI ships, so the checks below run the real ones.
"""
from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app import pgdata
from app.playground_api import create_app

ROOT = Path(__file__).resolve().parents[1]
EMPTY_PROMPT = "Пустой промпт"
NO_ACCOUNT = "Нет доступного аккаунта"
INCOMPLETE = "Генерация не завершилась"
PROVIDER = "сбой провайдера"

def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

class MediaAndHandoffContractTest(unittest.TestCase):
    """The upstream double stands in for the Proxy; everything else is real."""

    def _upstream(self, port: int, scenario: str) -> threading.Thread:
        from fastapi.responses import JSONResponse

        upstream = FastAPI()

        @upstream.post("/api/admin/playground/chat")
        async def chat():
            async def events():
                yield 'data: {"choices":[{"delta":{"content":"ответ"}}]}\n\n'
                yield "data: [DONE]\n\n"
            return StreamingResponse(events(), media_type="text/event-stream")

        @upstream.get("/api/admin/models")
        async def models():
            return {"models": [{"id": "demo-model", "name": "Demo", "provider": "Demo",
                               "caps": {"vision": True}}]}

        async def media(request: Request, kind: str = "image"):
            body = json.loads(await request.body() or b"{}")
            async def events():
                if not str(body.get("prompt") or "").strip():

                    yield "data: " + json.dumps({"error": {"message": EMPTY_PROMPT}},
                                                ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if kind == "video":
                    yield "data: " + json.dumps(
                        {"status": "error", "error": NO_ACCOUNT}, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if scenario == "provider":
                    yield "data: " + json.dumps(
                        {"status": "error", "error": PROVIDER}, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                yield 'data: {"status":"queued"}\n\n'
                yield ('data: ' + json.dumps(
                    {"status": "done", "urls": ["data:image/png;base64,aaa"], "ms": 10}) + "\n\n")
                yield "data: [DONE]\n\n"
            return StreamingResponse(events(), media_type="text/event-stream")

        @upstream.post("/api/admin/pg/image")
        async def image(request: Request):
            return await media(request, "image")

        @upstream.post("/api/admin/pg/video")
        async def video(request: Request):
            return await media(request, "video")

        config = uvicorn.Config(upstream, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            threading.Event().wait(0.05)
        return thread

    def test_media_errors_and_handoff_go_through_the_shipped_host(self) -> None:
        port = _free_port()
        thread = self._upstream(port, "provider")
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                data = root / "data"
                data.mkdir()
                modules = root / "modules"
                home = root / "home"
                home.mkdir()
                blocked = root / "blocked"
                report_path = root / "report.json"
                with patch.object(pgdata, "MEMU_DIR", data), \
                        patch("app.modules_host.default_root", return_value=modules), \
                        patch("pathlib.Path.home", return_value=home), \
                        patch.dict("os.environ",
                                   {"MEMUAI_PROXY_URL": f"http://127.0.0.1:{port}"}):
                    app = create_app()
                    playground_port = _free_port()
                    config = uvicorn.Config(app, host="127.0.0.1", port=playground_port,
                                            log_level="error")
                    server = uvicorn.Server(config)
                    host_thread = threading.Thread(target=server.run, daemon=True)
                    host_thread.start()
                    for _ in range(200):
                        if server.started:
                            break
                        threading.Event().wait(0.05)
                    base = f"http://127.0.0.1:{playground_port}"
                    try:
                        with httpx.Client(timeout=30) as client:
                            client.post(base + "/api/pg/modules/configure",
                                        json={"id": "translator", "enabled": True})
                        completed = subprocess.run(
                            ["node", str(ROOT / "tests" / "modules_media_runner.cjs"),
                             str(ROOT / "app" / "pgstatic" / "modules.js"),
                             str(ROOT / "app" / "pgstatic" / "app.js"),
                             base, str(report_path), PROVIDER],
                            cwd=str(ROOT), capture_output=True, text=True, check=False,
                            timeout=120)
                        self.assertEqual(completed.returncode, 0,
                                         completed.stderr + completed.stdout)
                        report = json.loads(report_path.read_text(encoding="utf-8"))
                    finally:
                        server.should_exit = True
                        host_thread.join(6)

                self.assertNotIn("fatal", report, report)

                self.assertTrue(report["hasMediaConsumer"])
                self.assertEqual(report["okUrls"], 1)
                self.assertIn(INCOMPLETE, report["truncatedError"])
                self.assertIn(PROVIDER, report["providerError"])
                self.assertIn("provider blew up", report["statusErrorError"])
                self.assertEqual(report["emptyUrls"], 0)

                self.assertIn(EMPTY_PROMPT, report["imageError"])
                self.assertIn(NO_ACCOUNT, report["videoError"])
                self.assertIsNone(report["imageResult"])
                self.assertIsNone(report["videoResult"])

                self.assertEqual(report["requestError"], "", report)
                self.assertTrue(report["requestModel"], report)

                self.assertEqual(report["handoffError"], "")
                self.assertTrue(report["handoffPaths"], report)
                for path in report["handoffPaths"]:
                    self.assertTrue(Path(path).exists(), path)
        finally:
            thread.join(1)

    def test_design_refuses_to_claim_an_unsaved_bundle(self) -> None:
        """The Design screen must not say "package ready" unless files landed."""
        for mode in ("fail", "empty"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as temp:
                    report_path = Path(temp) / "design.json"
                    completed = subprocess.run(
                        ["node", str(ROOT / "tests" / "modules_design_runner.cjs"),
                         str(ROOT / "app" / "module_builtins" / "design" / "main.js"),
                         str(report_path), mode],
                        cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=60)
                    self.assertEqual(completed.returncode, 0,
                                     completed.stderr + completed.stdout)
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    self.assertNotIn("fatal", report, report)

                    self.assertTrue(report.get("handoffCalled"),
                                    f"{mode}: до Memu.handoff дело не дошло — {report}")
                    self.assertGreater(report.get("status") or "", "")
                    self.assertNotIn("Пакет готов", report["status"])
                    self.assertNotIn("Пакет готов", report["result"])

    def test_design_reports_a_real_package_when_files_land(self) -> None:
        """The opposite direction: a real bundle must be announced."""
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "design-ok.json"
            completed = subprocess.run(
                ["node", str(ROOT / "tests" / "modules_design_runner.cjs"),
                 str(ROOT / "app" / "module_builtins" / "design" / "main.js"),
                 str(report_path), "ok"],
                cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertNotIn("fatal", report, report)
            self.assertTrue(report.get("handoffCalled"), report)
            self.assertIn("Пакет готов", report["status"] + report["result"])

if __name__ == "__main__":
    unittest.main()
