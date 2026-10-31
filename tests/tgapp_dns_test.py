"""Mini App DNS (4.5): the system resolver decides, DoH never gates the
settings, an inconclusive probe never kills the tunnel, and the settings
card always shows diagnostics + the local fallback.

Reproduction of the reported failure: with the old code, ``check_public_url``
asked DoH (1.1.1.1 / 8.8.8.8) first; when DoH was unreachable the result was
classified as ``"dns"``, ``_verify`` waited 150 s, killed the tunnel and
requested a new address — forever. The settings card only showed
"Waiting for DNS" with no host, no error and no local alternative.

Round 73 found the failure still present for three further reasons, all
covered below: ``doh_resolve`` returned ``False`` for every answered query,
so a SERVFAIL/REFUSED from a filtering resolver was misclassified as
"dns"; ``_verify`` still terminated the tunnel on a "dns" result (the
kill-on-dns path was never removed, only the "unreachable" path was
fixed); and ``_tunnel_error_hint`` reported the Cloudflare-DNS message
for *any* "no such host" line, including other hosts, and kept stale
hints across registration. The system-resolver wait was unbounded too.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app import config as app_config
from app import tg_webapp

TOKEN = "123456:TESTTOKEN-for-tests-only-aaaaaaaaaaaaaaaaaaaaaa"


class DnsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memu-tgapp-dns-")
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(app_config, "DATA_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        prefs = mock.patch.object(tg_webapp, "_preferences", lambda: {})
        prefs.start()
        self.addCleanup(prefs.stop)


def _gaierror(errno):
    return socket.gaierror(errno, "Name or service not known")


class ResolveHostTest(DnsTestBase):
    def test_resolves(self):
        with mock.patch("socket.getaddrinfo", return_value=[("x",)]):
            self.assertIs(asyncio.run(tg_webapp.resolve_host("fresh.trycloudflare.com")), True)

    def test_nxdomain_is_false(self):
        with mock.patch("socket.getaddrinfo", side_effect=_gaierror(socket.EAI_NONAME)):
            self.assertIs(asyncio.run(tg_webapp.resolve_host("fresh.trycloudflare.com")), False)

    def test_temporary_failure_is_inconclusive(self):
        with mock.patch("socket.getaddrinfo", side_effect=_gaierror(socket.EAI_AGAIN)):
            self.assertIs(asyncio.run(tg_webapp.resolve_host("fresh.trycloudflare.com")), None)


class CheckPublicUrlTest(DnsTestBase):
    _UNSET = object()

    def _run(self, handler, resolved, doh=_UNSET):
        async def fake_resolve(host):
            return resolved

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with mock.patch.object(tg_webapp, "resolve_host", fake_resolve):
                    if doh is not self._UNSET:
                        async def fake_doh(host, client_):
                            return doh

                        with mock.patch.object(tg_webapp, "doh_resolve", fake_doh):
                            return await tg_webapp.check_public_url("https://fresh.trycloudflare.com", client)
                    return await tg_webapp.check_public_url("https://fresh.trycloudflare.com", client)

        return asyncio.run(run())

    def test_system_answer_never_consults_doh(self):
        seen_hosts = []

        def handler(request):
            seen_hosts.append(request.url.host)
            return httpx.Response(200, json={"app": "memuai-tgapp", "nonce": tg_webapp.instance_nonce()})

        self.assertEqual(self._run(handler, True), ("ok", ""))
        self.assertNotIn("1.1.1.1", seen_hosts)
        self.assertNotIn("8.8.8.8", seen_hosts)

    def test_nxdomain_is_dns_without_doh(self):
        def handler(request):
            return httpx.Response(200, json={})

        self.assertEqual(self._run(handler, False)[0], "dns")

    def test_inconclusive_resolver_plus_dead_doh_is_unreachable(self):
        def handler(request):
            raise httpx.ConnectError("blocked", request=request)

        self.assertEqual(self._run(handler, None, doh=None)[0], "unreachable")


class VerifyFallbackTest(DnsTestBase):
    def test_inconclusive_probe_publishes_and_keeps_the_tunnel(self):
        async def scenario():
            manager = tg_webapp.Manager()
            manager.probe = lambda base: ("unreachable", "no route to DoH")
            manager.url = "https://fresh.trycloudflare.com"
            terminated = []
            manager.tunnel = mock.MagicMock(named=False, retry_at=0.0, failures=0,
                                            effective_protocol=lambda: "",
                                            _terminate=lambda: terminated.append(1))
            with mock.patch.object(tg_webapp, "VERIFY_EVERY", 0.01), \
                    mock.patch.object(tg_webapp, "VERIFY_TIMEOUT", 0.1), \
                    mock.patch.object(tg_webapp, "notify_bot", lambda url: None), \
                    mock.patch.object(tg_webapp, "remember_working_url", lambda url: None), \
                    mock.patch.object(manager, "write_status", lambda: None):
                manager._start_verify(publish=True)
                await asyncio.wait_for(manager._verify_task, 2)
            return manager, terminated

        manager, terminated = asyncio.run(scenario())
        self.assertEqual(manager.state, "online")
        self.assertEqual(terminated, [])
        self.assertIn("всё равно отправлен", manager.detail)

    def test_local_fallback_skips_the_public_check(self):
        async def scenario():
            manager = tg_webapp.Manager()
            manager.local_fallback = True
            calls = []
            manager.probe = lambda base: calls.append(base) or ("ok", "")
            manager.url = "https://fresh.trycloudflare.com"
            with mock.patch.object(tg_webapp, "notify_bot", lambda url: None), \
                    mock.patch.object(manager, "write_status", lambda: None):
                manager._start_verify(publish=True)
                await asyncio.wait_for(manager._verify_task, 2)
            return manager, calls

        manager, calls = asyncio.run(scenario())
        self.assertEqual(manager.state, "online")
        self.assertEqual(calls, [])
        self.assertIn("Локальный режим", manager.detail)

    def test_verified_address_is_cached(self):
        tg_webapp.remember_working_url("https://fresh.trycloudflare.com")
        stored = json.loads((Path(self.tmp.name) / "memuai" / "tgapp.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["last_working_url"], "https://fresh.trycloudflare.com")
        self.assertGreater(stored["last_working_at"], 0)
        self.assertTrue(stored["secret"])


class DohResolveTest(DnsTestBase):
    """Only a conclusive public-DNS answer counts. A DNS server that
    fails, refuses or answers SERVFAIL is inconclusive (None) — never
    proof the host does not exist — and the system resolver is never
    re-asked unboundedly as a fallback."""

    def _run(self, handler, *, forbid_getaddrinfo=True):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                if forbid_getaddrinfo:
                    def _boom(*args, **kwargs):
                        raise AssertionError("doh_resolve must not re-ask the system resolver")

                    with mock.patch("socket.getaddrinfo", side_effect=_boom):
                        return await tg_webapp.doh_resolve("fresh.trycloudflare.com", client)
                return await tg_webapp.doh_resolve("fresh.trycloudflare.com", client)

        return asyncio.run(run())

    @staticmethod
    def _json(status, answer=None):
        def handler(request):
            body = {"Status": status}
            if answer is not None:
                body["Answer"] = answer
            return httpx.Response(200, json=body)

        return handler

    def test_servfail_is_inconclusive(self):
        self.assertIsNone(self._run(self._json(2)))

    def test_refused_is_inconclusive(self):
        self.assertIsNone(self._run(self._json(5)))

    def test_formerr_is_inconclusive(self):
        self.assertIsNone(self._run(self._json(1)))

    def test_nxdomain_is_false(self):
        self.assertIs(self._run(self._json(3)), False)

    def test_noerror_without_records_is_inconclusive(self):
        self.assertIsNone(self._run(self._json(0, [])))

    def test_answer_is_true(self):
        self.assertIs(self._run(self._json(0, [{"type": 1, "data": "104.16.0.1"}])), True)

    def test_non_200_is_inconclusive(self):
        def handler(request):
            return httpx.Response(403, json={"Status": 3})

        self.assertIsNone(self._run(handler))

    def test_unreachable_doh_is_inconclusive(self):
        def handler(request):
            raise httpx.ConnectError("blocked", request=request)

        self.assertIsNone(self._run(handler))


class ResolveHostTimeoutTest(DnsTestBase):
    def test_hanging_resolver_returns_none_quickly(self):
        import time as _time

        def _hang(*args, **kwargs):
            _time.sleep(3)
            return [("x",)]

        async def scenario():
            started = _time.monotonic()
            result = await tg_webapp.resolve_host("fresh.trycloudflare.com")
            # Measured inside the coroutine: asyncio.run() itself waits for
            # the abandoned executor thread at shutdown, which is not the
            # probe's fault (the Proxy loop never shuts the executor down).
            return result, _time.monotonic() - started

        with mock.patch("socket.getaddrinfo", side_effect=_hang), \
                mock.patch.object(tg_webapp, "RESOLVE_TIMEOUT", 0.2):
            result, elapsed = asyncio.run(scenario())
        self.assertIsNone(result)
        self.assertLess(elapsed, 1.5)


class VerifyNeverKillsOnDnsTest(DnsTestBase):
    def test_dns_probe_publishes_and_keeps_the_tunnel(self):
        async def scenario():
            manager = tg_webapp.Manager()
            manager.probe = lambda base: ("dns", "Адрес ещё не появился в DNS — жду")
            manager.url = "https://fresh.trycloudflare.com"
            terminated = []
            manager.tunnel = mock.MagicMock(named=False, retry_at=0.0, failures=0,
                                            effective_protocol=lambda: "",
                                            _terminate=lambda: terminated.append(1))
            with mock.patch.object(tg_webapp, "VERIFY_EVERY", 0.01), \
                    mock.patch.object(tg_webapp, "VERIFY_TIMEOUT", 0.05), \
                    mock.patch.object(tg_webapp, "notify_bot", lambda url: None), \
                    mock.patch.object(tg_webapp, "remember_working_url", lambda url: None), \
                    mock.patch.object(manager, "write_status", lambda: None):
                manager._start_verify(publish=True)
                await asyncio.wait_for(manager._verify_task, 2)
            return manager, terminated

        manager, terminated = asyncio.run(scenario())
        self.assertEqual(manager.state, "online")
        self.assertEqual(terminated, [])
        self.assertEqual(manager.public_url(), "https://fresh.trycloudflare.com/tgapp/")
        self.assertIn("всё равно отправлен", manager.detail)


class TunnelHintScopeTest(DnsTestBase):
    def test_non_cloudflare_host_is_not_a_cloudflare_dns_error(self):
        hint = tg_webapp._tunnel_error_hint(
            '2026-10-31T10:00:00Z ERR error="lookup my-origin.example.com: no such host" failed to dial origin')
        self.assertEqual(hint, "")

    def test_cloudflare_host_names_the_host(self):
        hint = tg_webapp._tunnel_error_hint(
            '2026-10-31T10:00:00Z ERR error="lookup region1.argotunnel.com on 10.0.0.1:53: no such host" failed to dial')
        self.assertIn("region1.argotunnel.com", hint)
        self.assertIn("не резолвится", hint)

    def test_server_misbehaving_without_cloudflare_context_is_not_dns(self):
        hint = tg_webapp._tunnel_error_hint(
            '2026-10-31T10:00:00Z ERR error="server misbehaving" failed to dial')
        self.assertEqual(hint, "")

    def test_localhost_refused_is_not_a_cloudflare_error(self):
        hint = tg_webapp._tunnel_error_hint(
            '2026-10-31T10:00:00Z ERR Failed to connect to 127.0.0.1:8641: connection refused')
        self.assertIn("локальному MemuAI", hint)
        self.assertNotIn("Cloudflare", hint)

    def test_new_hint_texts_have_english_twins(self):
        import re

        for text in (
                "Компьютер не может найти серверы Cloudflare (DNS): не резолвится region1.argotunnel.com — проверьте интернет или VPN",
                "cloudflared не может подключиться к локальному MemuAI — проверьте, что MemuAI Proxy запущен"):
            self.assertNotRegex(tg_webapp.status_en(text), "[А-Яа-яЁё]")

    def test_stale_hint_is_cleared_on_registration(self):
        tunnel = tg_webapp.CloudflaredTunnel(
            Path("/nonexistent/cloudflared"), 8641,
            on_url=lambda u: None, on_state=lambda s, e: None)
        tunnel._line("2026-10-31T10:00:00Z INF Tunnel: https://fresh-abc123.trycloudflare.com")
        tunnel._line('2026-10-31T10:00:00Z ERR error="lookup region1.argotunnel.com: no such host" failed to dial to edge')
        self.assertIn("argotunnel", tunnel.last_error)
        tunnel._line("2026-10-31T10:00:00Z INF Registered tunnel connection connIndex=0")
        self.assertTrue(tunnel.registered)
        self.assertEqual(tunnel.last_error, "")


class DesktopStatusTest(DnsTestBase):
    def test_unreachable_host_opens_settings_with_diagnostics(self):
        # A stale status file, no working network, no cloudflared binary:
        # the card must still answer with a host, an error, a local URL and
        # the last working address — never raise.
        status_path = Path(self.tmp.name) / "memuai" / "tgapp-status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps({
            "state": "dns_pending", "error": "", "detail": "Адрес ещё не появился в DNS — жду",
            "url": "https://fresh.trycloudflare.com/tgapp/", "base": "https://fresh.trycloudflare.com",
            "port": 8641, "heartbeat": 9999999999.0}), encoding="utf-8")
        tg_webapp.save_config({"enabled": True})
        tg_webapp.remember_working_url("https://old.trycloudflare.com")
        with mock.patch.object(tg_webapp, "find_cloudflared", side_effect=OSError("no binary")):
            data = tg_webapp.desktop_status()
        self.assertEqual(data["status"]["state"], "dns_pending")
        diag = data["diagnostics"]
        self.assertEqual(diag["host"], "fresh.trycloudflare.com")
        self.assertIn("DNS", diag["error"])
        self.assertEqual(diag["local_url"], "http://127.0.0.1:8641/tgapp/")
        self.assertEqual(data["local_url"], "http://127.0.0.1:8641/tgapp/")
        self.assertEqual(diag["last_working_url"], "https://old.trycloudflare.com")
        self.assertEqual(diag["resolver"], "system")

    def test_status_never_raises(self):
        with mock.patch.object(tg_webapp, "load_config", side_effect=OSError("disk")), \
                mock.patch.object(tg_webapp, "read_status", side_effect=OSError("disk")), \
                mock.patch.object(tg_webapp, "find_cloudflared", side_effect=OSError("disk")):
            data = tg_webapp.desktop_status()
        self.assertIn("local_url", data)
        self.assertIn("diagnostics", data)
        self.assertIn("config", data)

    def test_local_fallback_round_trips_through_save_config(self):
        saved = tg_webapp.save_config({"local_fallback": True})
        self.assertTrue(saved["local_fallback"])
        self.assertTrue(tg_webapp.load_config()["local_fallback"])
        self.assertFalse(tg_webapp.save_config({"local_fallback": False})["local_fallback"])

    def test_stale_status_still_shows_the_local_address(self):
        # No Proxy running, no network, stale status file: the card must
        # still answer with the local address and the saved config, and
        # must not claim a host or a check that never happened.
        tg_webapp.save_config({"enabled": True})
        status_path = Path(self.tmp.name) / "memuai" / "tgapp-status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps({
            "state": "error",
            "error": "Компьютер не может найти серверы Cloudflare (DNS) — проверьте интернет или VPN",
            "url": "https://fresh.trycloudflare.com/tgapp/", "base": "https://fresh.trycloudflare.com",
            "port": 8641, "heartbeat": 1.0}), encoding="utf-8")
        data = tg_webapp.desktop_status()
        self.assertEqual(data["status"]["state"], "proxy_offline")
        self.assertTrue(data["config"]["enabled"])
        self.assertEqual(data["local_url"], "http://127.0.0.1:8641/tgapp/")
        self.assertEqual(data["diagnostics"]["local_url"], "http://127.0.0.1:8641/tgapp/")
        self.assertEqual(data["diagnostics"]["host"], "")


if __name__ == "__main__":
    unittest.main()
