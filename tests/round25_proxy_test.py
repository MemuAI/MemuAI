"""Round 25 — MemuAI Proxy bug hunt: public keys (IP allow-list, client address), listener and tunnel
errors in both languages, the tunnel stopping with the server, the port field."""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import public_api, public_tunnel  # noqa: E402
from tests.round24_proxy_test import KeysCase  # noqa: E402

class _Request:
    def __init__(self, peer: str, headers: dict) -> None:
        self.client = type("C", (), {"host": peer})()
        self.headers = headers

class ClientAddressTest(unittest.TestCase):
    def test_only_the_last_forwarded_entry_counts(self) -> None:

        request = _Request("127.0.0.1", {"x-forwarded-for": "203.0.113.7, 198.51.100.20"})
        with patch.object(public_tunnel.tunnel, "running", lambda: False):
            self.assertEqual(public_api.client_ip(request), "198.51.100.20")

    def test_cf_header_needs_our_own_tunnel(self) -> None:
        request = _Request("127.0.0.1", {"cf-connecting-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.20"})
        with patch.object(public_tunnel.tunnel, "running", lambda: False):
            self.assertEqual(public_api.client_ip(request), "198.51.100.20", "a reverse proxy passes CF-Connecting-IP through")
        with patch.object(public_tunnel.tunnel, "running", lambda: True):
            self.assertEqual(public_api.client_ip(request), "203.0.113.7")
        self.assertEqual(public_api.client_ip(_Request("198.51.100.1", request.headers)), "198.51.100.1")

class IpListTest(KeysCase):
    def test_a_typo_is_refused_instead_of_opening_the_key(self) -> None:
        with self.assertRaises(ValueError) as caught:
            public_api.store.add_key({"ips": ["203.0.113.7/33"]})
        self.assertIn("203.0.113.7/33", str(caught.exception))
        self.assertEqual(public_api.store.data()["keys"], [], "nothing is created")
        key = public_api.store.add_key({"ips": ["203.0.113.0/24"]})
        with self.assertRaises(ValueError):
            public_api.store.edit_key(key["id"], {"ips": "10.0.0.1, 300.1.1.1"})
        self.assertEqual(public_api.store.get(key["id"])["ips"], ["203.0.113.0/24"], "the old list stays")
        self.assertEqual(public_api.store.edit_key(key["id"], {"ips": ""})["ips"], [], "an empty list is allowed")

    def test_admin_route_answers_400_in_both_languages(self) -> None:
        from app import proxy_tools_api
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(proxy_tools_api.router)
        with patch.object(proxy_tools_api, "_auth", lambda request: None):
            client = TestClient(app)
            bad = client.post("/api/admin/public/keys", json={"ips": ["nope"]})
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(bad.json()["error_en"], "Invalid IP addresses or subnets: nope")
            created = client.post("/api/admin/public/keys", json={"name": "a"}).json()["created"]
            edit = client.post(f"/api/admin/public/keys/{created['id']}", json={"ips": ["1.2.3.4/99"]})
            self.assertEqual(edit.status_code, 400, "a bad value is not 'key not found'")
            self.assertEqual(client.post("/api/admin/public/keys/missing", json={"name": "x"}).status_code, 404)

class BilingualErrorsTest(unittest.TestCase):
    def test_tunnel_errors_carry_english(self) -> None:
        tunnel = public_tunnel.Tunnel()
        temp = tempfile.mkdtemp()
        with patch.object(public_tunnel, "find_binary", lambda: ""), \
                patch.object(public_tunnel, "_conf_path", lambda: Path(temp) / "conf.json"):
            status = tunnel.start(8320)
        self.assertIn("не найден", status["error"])
        self.assertIn("was not found", status["error_en"])
        self.assertNotRegex(status["error_en"], "[А-Яа-я]")

    def test_busy_port_is_reported_in_both_languages(self) -> None:
        taken = socket.socket()
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        try:
            ru, en = public_api._port_busy("127.0.0.1", taken.getsockname()[1])
        finally:
            taken.close()
        self.assertIn("занят", ru)
        self.assertIn("taken by another program", en)

    def test_a_port_freed_a_moment_ago_is_not_busy(self) -> None:

        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = server.accept()
        accepted.close()
        client.close()
        server.close()
        if os.name != "nt":
            self.assertIsNone(public_api._port_busy("127.0.0.1", port))

class PortFieldTest(unittest.TestCase):
    def test_zero_or_empty_port_is_the_default_not_port_1(self) -> None:
        self.assertEqual(public_api.normalize({"port": 0})["port"], public_api.DEFAULTS["port"])
        self.assertEqual(public_api.normalize({"port": ""})["port"], public_api.DEFAULTS["port"])
        self.assertEqual(public_api.normalize({"port": 999999})["port"], 65535)

    def test_ui_does_not_save_an_invalid_port(self) -> None:
        source = (ROOT / "app" / "static" / "public.js").read_text(encoding="utf-8")
        self.assertFalse('field("#pub-port", "port", Number)' in source)
        self.assertIn("value < 1 || value > 65535", source)
        self.assertIn("S.fresh = null;", source, "rotation forgets an earlier fresh key first")
        self.assertIn("tn.error_en", source)
        self.assertIn("status.error_en", source)
        self.assertIn("if (await keyCall(key.id, body)) closeModal();", source,
                      "a refused edit (a bad IP) keeps the form open with what was typed")

class TunnelStopsWithServerTest(unittest.TestCase):
    def test_lifespan_stops_the_tunnel(self) -> None:
        from app import main
        stopped = []
        fake = type("T", (), {"running": lambda self: True, "stop": lambda self: stopped.append(True)})()

        async def go():
            app = main.create_app()
            noop = asyncio.sleep
            with patch.object(public_tunnel, "tunnel", fake), \
                    patch.object(public_api.listener, "apply", lambda: noop(0, {})), \
                    patch.object(main, "_cookie_checker", lambda: noop(3600)), \
                    patch.object(main, "_watchdog", lambda: noop(3600)), \
                    patch("app.scheduler.scheduler_loop", lambda: noop(3600)), \
                    patch("app.telegram_bot.telegram_loop", lambda: noop(3600)), \
                    patch("app.module_monitor.background_loop", lambda: noop(3600)), \
                    patch("app.tg_webapp.run_manager", lambda: noop(3600)):
                async with app.router.lifespan_context(app):
                    pass
        asyncio.run(go())
        self.assertEqual(stopped, [True])

if __name__ == "__main__":
    unittest.main()
