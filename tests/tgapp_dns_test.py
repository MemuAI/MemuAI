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
import time
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


class EdgeDiscoveryClassificationTest(unittest.TestCase):
    def test_named_tunnel_regional_endpoints_are_not_global(self):
        for domain in ("us-region1.v2.argotunnel.com", "region1.v2.argotunnel.com.example"):
            with self.subTest(domain=domain):
                self.assertFalse(tg_webapp._edge_dns_failure(f"lookup {domain}: no such host", quick=False))
        self.assertTrue(tg_webapp._edge_dns_failure("lookup region1.v2.argotunnel.com: no such host", quick=False))


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


class EdgeDnsFailureWidenedTest(unittest.TestCase):
    """Real lines from cloudflared 2026.10.7 in a network namespace without
    DNS: the SRV discovery failure, its shutdown line, and a failed lookup
    of the quick-tunnel API. Before the fix the first two produced no
    hint at all and the third was misclassified as "no address"."""
    SRV_TIMEOUT = ('2026-11-03T12:00:00Z ERR edge discovery: error looking up Cloudflare edge IPs: '
                   'the DNS query failed error="lookup _v2-origintunneld._tcp.argotunnel.com on '
                   '[fd8b:4f84:7d32:99::1]:53: dial udp [fd8b:4f84:7d32:99::1]:53: connect: '
                   'cannot assign requested address"')
    SRV_SHUTDOWN = ('2026-11-03T12:00:01Z ERR Initiating shutdown error="Could not lookup srv records '
                    'on _v2-origintunneld._tcp.argotunnel.com: lookup _v2-origintunneld._tcp.argotunnel.com '
                    'on [fd8b:4f84:7d32:99::1]:53: dial udp [fd8b:4f84:7d32:99::1]:53: connect: '
                    'cannot assign requested address"')
    API_NXDOMAIN = ('2026-11-03T12:00:00Z ERR failed to request quick Tunnel: Post '
                    '"https://api.trycloudflare.com/tunnel": dial tcp: lookup api.trycloudflare.com: '
                    'no such host')

    def test_srv_timeout_triggers_the_fallback(self):
        self.assertTrue(tg_webapp._edge_dns_failure(self.SRV_TIMEOUT, quick=True))
        self.assertTrue(tg_webapp._edge_dns_failure(self.SRV_TIMEOUT, quick=False))

    def test_srv_shutdown_line_is_not_invisible(self):
        self.assertTrue(tg_webapp._edge_dns_failure(self.SRV_SHUTDOWN, quick=True))
        self.assertTrue(tg_webapp._edge_dns_failure(self.SRV_SHUTDOWN, quick=False))

    def test_api_lookup_failure_triggers_the_fallback_for_quick_tunnels(self):
        self.assertTrue(tg_webapp._edge_dns_failure(self.API_NXDOMAIN, quick=True))
        # A named tunnel never talks to api.trycloudflare.com.
        self.assertFalse(tg_webapp._edge_dns_failure(self.API_NXDOMAIN, quick=False))

    def test_srv_lines_name_the_host_in_the_hint(self):
        for line in (self.SRV_TIMEOUT, self.SRV_SHUTDOWN):
            with self.subTest(line=line[:40]):
                hint = tg_webapp._tunnel_error_hint(line)
                self.assertIn("_v2-origintunneld._tcp.argotunnel.com", hint)
                self.assertIn("не резолвится", hint)
                self.assertIn("Смените DNS", hint)

    def test_api_lookup_failure_is_a_dns_hint_not_a_no_address_hint(self):
        hint = tg_webapp._tunnel_error_hint(self.API_NXDOMAIN)
        self.assertIn("api.trycloudflare.com", hint)
        self.assertIn("не резолвится", hint)
        self.assertNotIn("не выдал адрес", hint)

    def test_tls_failure_still_says_no_address(self):
        hint = tg_webapp._tunnel_error_hint(
            '2026-11-03T12:00:00Z ERR failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": '
            'tls: first record does not look like a TLS handshake')
        self.assertIn("не выдал адрес", hint)

    def test_new_hint_texts_have_english_twins(self):
        for text in (
                "Компьютер не может найти серверы Cloudflare (DNS): не резолвится region1.v2.argotunnel.com — системный DNS не отдаёт адреса Cloudflare. Смените DNS на 1.1.1.1 или 8.8.8.8 либо используйте свой туннель",
                "Компьютер не может найти серверы Cloudflare (DNS) — системный DNS не отдаёт адреса Cloudflare. Смените DNS на 1.1.1.1 или 8.8.8.8 либо используйте свой туннель"):
            self.assertNotRegex(tg_webapp.status_en(text), "[А-Яа-яЁё]")


class _FakeDoHResponse:
    def __init__(self, answers=None, status=200):
        self._answers = answers or []
        self.status_code = status

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def json(self):
        return {"Answer": self._answers}


class _FakeDoHClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def get(self, endpoint, params=None, headers=None):
        self.calls.append((endpoint, params["name"], params["type"]))
        return self.handler(endpoint, params["name"], params["type"])

    async def aclose(self):
        pass


class EdgeDoHTest(DnsTestBase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        DnsTestBase.setUp(self)
        unittest.IsolatedAsyncioTestCase.setUp(self)

    async def test_first_doh_source_down_second_answers(self):
        def handler(endpoint, name, _type):
            if "1.1.1.1" in endpoint:
                raise httpx.ConnectError("down")
            return _FakeDoHResponse([{"name": name, "type": 1, "data": "198.41.192.7"},
                                     {"name": name, "type": 1, "data": "198.41.192.47"}])

        addresses, source = await tg_webapp._resolve_edge_addresses("4", client=_FakeDoHClient(handler))
        self.assertEqual(source, "doh:8.8.8.8")
        self.assertEqual(addresses, "198.41.192.7:7844,198.41.192.47:7844")
        self.assertEqual(addresses.source, "doh:8.8.8.8")

    async def test_all_doh_sources_down(self):
        def handler(*_args):
            raise httpx.ConnectError("down")

        addresses, source = await tg_webapp._resolve_edge_addresses("4", client=_FakeDoHClient(handler))
        self.assertEqual((addresses, source), ("", ""))

    async def test_broken_httpx_client_means_no_edge_not_an_exception(self):
        # The sandbox proxy env makes httpx.AsyncClient() itself raise
        # InvalidURL ("[::1]" in no_proxy): that must never kill the
        # tunnel loop, it just means "no edge from DoH".
        with mock.patch("httpx.AsyncClient", side_effect=httpx.InvalidURL("bad")):
            self.assertEqual(await tg_webapp._resolve_edge_addresses("4"), ("", ""))

    async def test_cloudflare_edge_addresses_stays_a_plain_string(self):
        def handler(endpoint, name, _type):
            return _FakeDoHResponse([{"name": name, "type": 1, "data": "198.41.192.7"}])

        addresses = await tg_webapp._cloudflare_edge_addresses("4", client=_FakeDoHClient(handler))
        self.assertEqual(addresses, "198.41.192.7:7844")


class EdgeCacheTest(DnsTestBase):
    def test_save_load_round_trip(self):
        tg_webapp.save_edge_cache("198.41.192.7:7844,198.41.192.47:7844", "doh:1.1.1.1")
        self.assertEqual(tg_webapp.load_edge_cache(),
                         ("198.41.192.7:7844,198.41.192.47:7844", "cache"))

    def test_expired_cache_is_ignored(self):
        tg_webapp.save_edge_cache("198.41.192.7:7844", "doh:1.1.1.1")
        path = tg_webapp._edge_cache_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["at"] -= 49 * 3600
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(tg_webapp.load_edge_cache(), ("", ""))

    def test_malformed_and_private_cache_is_rejected(self):
        path = tg_webapp._edge_cache_path()
        for bad in ("198.41.192.7:9999", "10.0.0.1:7844", "not-an-ip:7844", "198.41.192.7"):
            with self.subTest(bad=bad):
                path.write_text(json.dumps({"addresses": bad, "at": int(time.time())}),
                                encoding="utf-8")
                self.assertEqual(tg_webapp.load_edge_cache(), ("", ""))
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(tg_webapp.load_edge_cache(), ("", ""))


class _DeadProcess:
    """A cloudflared that exits at once with no output."""

    def __init__(self):
        import io

        self.stdout = io.StringIO("")
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 1
        return 1

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminate()


class _LineProcess:
    """A cloudflared that prints lines, then exits with code 1."""

    def __init__(self, lines):
        import io

        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 1
        return 1

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminate()


class TunnelPreflightTest(DnsTestBase):
    def _tunnel(self, **kwargs):
        started = []

        def popen(command, **kwargs):
            started.append(kwargs)
            return _DeadProcess()

        tunnel = tg_webapp.CloudflaredTunnel(
            Path("/nonexistent/cloudflared"), 8641, on_url=lambda u: None,
            on_state=lambda s, e: None, popen=popen,
            sleep=lambda seconds: asyncio.sleep(0), max_failures=1,
            auto_retry_delay=3600, max_auto_cycles=0, **kwargs)
        return tunnel, started

    async def _drive(self, tunnel, started):
        tunnel.start()
        deadline = asyncio.get_running_loop().time() + 5
        while not started and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        await tunnel.stop()

    def test_system_dns_ok_means_no_bypass(self):
        tunnel, started = self._tunnel()
        with mock.patch.object(tg_webapp, "resolve_host", new=mock.AsyncMock(return_value=True)):
            asyncio.run(self._drive(tunnel, started))
        self.assertEqual(len(started), 1)
        self.assertNotIn("env", started[0])
        self.assertEqual(tunnel.edge_source, "system")
        self.assertEqual(tunnel._edge_addresses, "")

    def test_preflight_never_delays_the_launch(self):
        # A slow or unavailable DoH resolver must not delay the launch. The
        # first attempt starts while DoH is still hanging — without
        # TUNNEL_EDGE — and the pre-flight delivers its addresses in the
        # background for the next attempt.
        tunnel, started = self._tunnel()
        gate = asyncio.Event()

        async def hanging_doh(family):
            await gate.wait()
            return "", ""

        with mock.patch.object(tg_webapp, "resolve_host", new=mock.AsyncMock(return_value=False)), \
                mock.patch.object(tg_webapp, "_resolve_edge_addresses", new=hanging_doh):
            asyncio.run(self._drive(tunnel, started))
        self.assertEqual(len(started), 1)
        self.assertNotIn("env", started[0])

    def test_background_preflight_applies_the_edge_and_writes_the_cache(self):
        tunnel, started = self._tunnel()
        edge = tg_webapp._EdgeAddresses("198.41.192.7:7844")
        edge.source = "doh:1.1.1.1"

        async def slow_doh(family):
            await asyncio.sleep(0.05)
            return edge, "doh:1.1.1.1"

        with mock.patch.object(tg_webapp, "resolve_host", new=mock.AsyncMock(return_value=False)), \
                mock.patch.object(tg_webapp, "_resolve_edge_addresses", new=slow_doh):
            async def drive():
                tunnel.start()
                deadline = asyncio.get_running_loop().time() + 5
                while tunnel._edge_addresses != "198.41.192.7:7844" \
                        and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.01)
                self.assertEqual(tunnel._edge_addresses, "198.41.192.7:7844")
                self.assertEqual(tunnel.edge_source, "doh:1.1.1.1")
                await tunnel.stop()

            asyncio.run(drive())
        self.assertEqual(tg_webapp.load_edge_cache(), ("198.41.192.7:7844", "cache"))

    def test_dead_doh_falls_back_to_the_cache(self):
        tg_webapp.save_edge_cache("198.41.192.47:7844", "doh:1.1.1.1")
        tunnel, started = self._tunnel()
        with mock.patch.object(tg_webapp, "resolve_host", new=mock.AsyncMock(return_value=False)), \
                mock.patch.object(tg_webapp, "_resolve_edge_addresses",
                                  new=mock.AsyncMock(return_value=("", ""))):
            asyncio.run(self._drive(tunnel, started))
        self.assertEqual(started[0]["env"]["TUNNEL_EDGE"], "198.41.192.47:7844")
        self.assertEqual(tunnel.edge_source, "cache")

    def test_explicit_tunnel_edge_skips_the_preflight(self):
        tunnel, started = self._tunnel()
        resolve = mock.AsyncMock(return_value=False)
        doh = mock.AsyncMock(return_value=("", ""))
        with mock.patch.dict(os.environ, {"TUNNEL_EDGE": "198.41.192.99:7844"}), \
                mock.patch.object(tg_webapp, "resolve_host", new=resolve), \
                mock.patch.object(tg_webapp, "_resolve_edge_addresses", new=doh):
            asyncio.run(self._drive(tunnel, started))
        resolve.assert_not_called()
        doh.assert_not_called()
        # No explicit env: the process inherits TUNNEL_EDGE from the
        # environment, so _run_once passes nothing.
        self.assertNotIn("env", started[0])


class TunnelRetryTest(DnsTestBase):
    def test_retry_keeps_the_edge_addresses(self):
        tunnel = tg_webapp.CloudflaredTunnel(Path("/nonexistent/cloudflared"), 8641,
                                             on_url=lambda u: None, on_state=lambda s, e: None)
        tunnel._edge_addresses = "198.41.192.7:7844"
        tunnel.failures = 6
        tunnel.gave_up = True
        tunnel.auto_cycles = 2
        tunnel._preflight_done = True
        tunnel.start = lambda: None  # the task itself is not under test here
        tunnel.retry()
        self.assertEqual(tunnel._edge_addresses, "198.41.192.7:7844")
        self.assertEqual(tunnel.failures, 0)
        self.assertFalse(tunnel.gave_up)
        self.assertEqual(tunnel.auto_cycles, 0)
        self.assertFalse(tunnel._preflight_done)

    def test_give_up_retries_itself_once(self):
        started = []

        def popen(command, **kwargs):
            started.append(command)
            return _DeadProcess()

        tunnel = tg_webapp.CloudflaredTunnel(
            Path("/nonexistent/cloudflared"), 8641, on_url=lambda u: None,
            on_state=lambda s, e: None, popen=popen,
            sleep=lambda seconds: asyncio.sleep(0), max_failures=1,
            auto_retry_delay=0.05, max_auto_cycles=1)

        async def drive():
            with mock.patch.object(tg_webapp, "resolve_host", new=mock.AsyncMock(return_value=True)):
                tunnel.start()
                deadline = asyncio.get_running_loop().time() + 5
                while len(started) < 2 and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.01)
                await tunnel.stop()

        asyncio.run(drive())
        self.assertGreaterEqual(len(started), 2)
        self.assertEqual(tunnel.auto_cycles, 1)


class TunnelWatchdogTest(DnsTestBase):
    def _manager(self, probe):
        manager = tg_webapp.Manager()
        manager.state = "online"
        manager.url = "https://quiet-river-cat.trycloudflare.com"
        manager.tunnel = mock.Mock()
        manager.tunnel.stop = mock.AsyncMock()
        manager.probe = probe
        return manager

    def test_three_unreachable_probes_restart_the_tunnel(self):
        manager = self._manager(lambda base: ("unreachable", "boom"))
        config = {"mode": "cloudflare"}

        async def drive():
            results = []
            for _ in range(3):
                manager._health_checked_at = float("-inf")
                results.append(await manager._watchdog(config))
            return results

        results = asyncio.run(drive())
        self.assertEqual(results, [False, False, True])
        self.assertEqual(manager._health_restarts, 1)
        self.assertIsNone(manager.tunnel)

    def test_dns_and_http_failures_never_restart(self):
        for kind in ("dns", "http"):
            with self.subTest(kind=kind):
                probes = []
                manager = self._manager(lambda base, kind=kind: probes.append(base) or (kind, "x"))
                config = {"mode": "cloudflare"}
                manager._health_checked_at = float("-inf")
                self.assertFalse(asyncio.run(manager._watchdog(config)))
                self.assertEqual(manager._health_failures, 0)
                self.assertIsNotNone(manager.tunnel)
                self.assertEqual(len(probes), 1)

    def test_ok_probe_resets_the_counter_and_custom_mode_is_skipped(self):
        manager = self._manager(lambda base: ("ok", ""))
        manager._health_failures = 2
        manager._health_checked_at = float("-inf")
        self.assertFalse(asyncio.run(manager._watchdog({"mode": "cloudflare"})))
        self.assertEqual(manager._health_failures, 0)
        manager._health_checked_at = float("-inf")
        self.assertFalse(asyncio.run(manager._watchdog({"mode": "custom"})))

    def test_snapshot_shows_tunnel_phase_and_resolver(self):
        manager = tg_webapp.Manager()
        tunnel = mock.Mock()
        tunnel.phase = "dns_check"
        tunnel.edge_source = "doh:8.8.8.8"
        tunnel.edge_diag = "край Cloudflare через doh:8.8.8.8"
        tunnel.attempts_left = 4
        tunnel.retry_at = 0.0
        tunnel.failures = 2
        tunnel.effective_protocol.return_value = "auto"
        manager.tunnel = tunnel
        snap = manager.snapshot()
        self.assertEqual(snap["phase"], "dns_check")
        self.assertEqual(snap["edge_source"], "doh:8.8.8.8")
        self.assertEqual(snap["attempts_left"], 4)
        self.assertEqual(snap["diagnostics"]["resolver"], "doh:8.8.8.8")
        self.assertEqual(snap["diagnostics"]["phase"], "dns_check")
        self.assertEqual(snap["diagnostics"]["dns_detail"], "край Cloudflare через doh:8.8.8.8")


class TunnelStickyErrorTest(DnsTestBase):
    """The last error stays on screen while the tunnel keeps
    retrying — it is never wiped by the next attempt's start."""

    DNS_LINE = ('2026-11-04T10:00:00Z ERR error="lookup _v2-origintunneld._tcp.argotunnel.com '
                'on 1.1.1.1:53: no such host" failed to dial to edge')

    def _tunnel(self, popen, **kwargs):
        return tg_webapp.CloudflaredTunnel(
            Path("/nonexistent/cloudflared"), 8641, on_url=lambda u: None,
            on_state=lambda s, e: None, popen=popen,
            sleep=lambda seconds: asyncio.sleep(0), max_failures=1,
            auto_retry_delay=3600, max_auto_cycles=0, **kwargs)

    def test_last_error_survives_the_next_attempt(self):
        calls = []

        def popen(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return _LineProcess([self.DNS_LINE])
            return _DeadProcess()

        tunnel = self._tunnel(popen)
        with mock.patch.object(tg_webapp, "resolve_host", new=mock.AsyncMock(return_value=False)), \
                mock.patch.object(tg_webapp, "_resolve_edge_addresses",
                                  new=mock.AsyncMock(return_value=("", ""))):
            async def drive():
                tunnel.start()
                deadline = asyncio.get_running_loop().time() + 5
                while not tunnel.gave_up and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.01)
                self.assertTrue(tunnel.gave_up, "the tunnel never gave up")
                self.assertEqual(len(tunnel.error_history), 1)
                self.assertEqual(tunnel.error_history[0]["attempt"], 1)
                self.assertIn("argotunnel", tunnel.error_history[0]["error"])
                self.assertIn("argotunnel", tunnel.last_error)
                self.assertGreater(tunnel.last_error_at, 0)
                self.assertEqual(tunnel.error_report()["last_error_kind"], "dns")
                tunnel.retry()
                deadline = asyncio.get_running_loop().time() + 5
                while len(tunnel.error_history) < 2 and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.01)
                self.assertEqual(len(tunnel.error_history), 2)
                # The second attempt died silently: its own entry says so,
                # but the sticky last error is still the DNS hint.
                self.assertIn("завершился (код 1)", tunnel.error_history[1]["error"])
                self.assertIn("argotunnel", tunnel.last_error)
                await tunnel.stop()

            asyncio.run(drive())

    def test_error_panel_ui_smoke(self):
        """The real settings card in jsdom (see the .cjs file for details):
        the sticky panel renders in state "starting", copy copies the full
        diagnostic text, dismiss hides it until a newer error arrives, and
        the English twins render. Exit code 2 means jsdom is not installed
        in this checkout — the smoke is skipped then, like the Playwright
        tests.
        """
        import subprocess

        root = Path(__file__).resolve().parent.parent
        done = subprocess.run(
            ["node", str(root / "tests" / "tgapp_settings_ui_test.cjs")],
            capture_output=True, text=True, timeout=180)
        if done.returncode == 2 and "jsdom not available" in done.stdout:
            self.skipTest("jsdom is not installed in this checkout")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("tunnel error panel UI smoke OK", done.stdout)

    def test_manager_keeps_the_error_while_starting(self):
        manager = tg_webapp.Manager()
        manager._set_state("error", "boom")
        manager._on_tunnel_state("starting", "")
        self.assertEqual(manager.state, "starting")
        self.assertEqual(manager.error, "boom")
        manager._on_tunnel_state("error", "new boom")
        self.assertEqual(manager.error, "new boom")
        manager._on_tunnel_state("connecting", "")
        self.assertEqual(manager.error, "new boom")

    def test_error_kinds_and_next_steps(self):
        tunnel = tg_webapp.CloudflaredTunnel(Path("/nonexistent/cloudflared"), 8641,
                                             on_url=lambda u: None, on_state=lambda s, e: None)
        cases = (
            ('2026-11-04T10:00:00Z ERR error="lookup _v2-origintunneld._tcp.argotunnel.com on 1.1.1.1:53: no such host" failed to dial', "dns"),
            ('2026-11-04T10:00:00Z ERR failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": tls: first record does not look like a TLS handshake', "quick_api"),
            ('2026-11-04T10:00:00Z ERR error="tls: failed to verify certificate: x509: certificate signed by unknown authority"', "tls"),
            ('2026-11-04T10:00:00Z ERR error="invalid tunnel secret" failed to run tunnel', "token"),
            ('2026-11-04T10:00:00Z ERR config.yaml found, cannot determine default configuration', "config"),
            ('2026-11-04T10:00:00Z ERR error="lookup 127.0.0.1:8641: connection refused"', "local"),
        )
        for line, kind in cases:
            with self.subTest(kind=kind):
                tunnel._line(line)
                report = tunnel.error_report()
                self.assertEqual(report["last_error_kind"], kind)
                self.assertTrue(report["next_step"])
                self.assertNotRegex(report["next_step_en"], "[А-Яа-яЁё]")
                self.assertIn("argotunnel" if kind == "dns" else "", report["recent_lines"][-1])

    def test_snapshot_and_diagnostics_expose_the_sticky_error(self):
        manager = tg_webapp.Manager()
        tunnel = tg_webapp.CloudflaredTunnel(Path("/nonexistent/cloudflared"), 8641,
                                             on_url=lambda u: None, on_state=lambda s, e: None)
        tunnel._line(self.DNS_LINE)
        manager.tunnel = tunnel
        snap = manager.snapshot()
        self.assertIn("argotunnel", snap["last_error"])
        self.assertEqual(snap["last_error_kind"], "dns")
        self.assertIn("VPN", snap["next_step"])
        self.assertIn("argotunnel", snap["diagnostics"]["last_error"])
        self.assertEqual(snap["diagnostics"]["last_error_kind"], "dns")
        # A stale status file still carries the error through.
        stale = tg_webapp._status_diagnostics(
            {"diagnostics": snap["diagnostics"]}, {"mode": "cloudflare"},
            "http://127.0.0.1:8641/tgapp/")
        self.assertIn("argotunnel", stale["last_error"])
        self.assertEqual(stale["last_error_kind"], "dns")


if __name__ == "__main__":
    unittest.main()
