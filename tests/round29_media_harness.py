"""Round 29 (r29b-media-routing): real MemuAI Proxy + MemuAI window server + offline media mock, in one process.

Used by tests/round29_media_routing_test.py (API level) and by manual/playwright checks:

    NO_PROXY=127.0.0.1,localhost python tests/round29_media_harness.py      # prints the URLs, serves until Ctrl+C

Real text models (XKIRO_*, YANDEX_* from the environment) are added as Custom connections when present; keys are
read from the environment only and never printed.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

class MediaServers:
    def __init__(self, data: str = "") -> None:
        self.data = data or os.environ.get("MEMUAI_DATA_DIR") or tempfile.mkdtemp(prefix="memu-media-")
        os.environ["MEMUAI_DATA_DIR"] = self.data
        for key in ("NO_PROXY", "no_proxy"):
            os.environ[key] = "127.0.0.1,localhost"
        self.servers = []

    def _run(self, app, port):
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        self.servers.append(server)
        for _ in range(400):
            if server.started:
                return
            time.sleep(0.05)
        raise RuntimeError(f"server on {port} did not start")

    def start(self, *, real: bool = True) -> "MediaServers":
        import httpx
        from tests import round29_media_mock
        mock, proxy, pg = free_port(), free_port(), free_port()
        self.mock = f"http://127.0.0.1:{mock}"
        self.proxy = f"http://127.0.0.1:{proxy}"
        self.pg = f"http://127.0.0.1:{pg}"
        os.environ["MEMUAI_PROXY_URL"] = self.proxy
        os.environ["MEMUAI_TOKEN"] = ""
        try:
            import webview  # noqa: F401
        except ImportError:
            import types
            sys.modules["webview"] = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10, SAVE_DIALOG=30)
        from app.main import create_app
        from app.playground_api import create_app as create_playground
        self._run(round29_media_mock.app, mock)
        self._run(create_app(), proxy)
        self._run(create_playground(), pg)
        with httpx.Client(timeout=60, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get(self.proxy + "/api/admin/settings").status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            client.post(self.proxy + "/api/admin/accounts", json={
                "provider": "custom", "label": "Media mock",
                "fields": {"name": "Media", "base_url": f"{self.mock}/v1", "api_key": "sk-media",
                           "models": "chat-small",
                           "image_models": "gpt-image-1, gemini-3-flash-image",
                           "video_models": "sora-2"}})
            if real and os.environ.get("XKIRO_BASE") and os.environ.get("XKIRO_KEY"):
                client.post(self.proxy + "/api/admin/accounts", json={
                    "provider": "custom", "label": "xkiro",
                    "fields": {"name": "xk", "base_url": os.environ["XKIRO_BASE"], "api_key": os.environ["XKIRO_KEY"],
                               "models": ", ".join(filter(None, [os.environ.get("XKIRO_MODEL", ""),
                                                                 "qwen/qwen3-vl-plus:free", "qwen/qwen3.7-flash:free"]))}})
            if real and os.environ.get("YANDEX_BASE") and os.environ.get("YANDEX_KEY"):
                client.post(self.proxy + "/api/admin/accounts", json={
                    "provider": "custom", "label": "yandex",
                    "fields": {"name": "ya", "base_url": os.environ["YANDEX_BASE"], "api_key": os.environ["YANDEX_KEY"],
                               "models": os.environ.get("YANDEX_MODEL", "")}})
        return self

    def stop(self) -> None:
        for server in self.servers:
            server.should_exit = True

if __name__ == "__main__":
    servers = MediaServers().start()
    print("data", servers.data)
    print("proxy", servers.proxy)
    print("memuai", servers.pg, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        servers.stop()
