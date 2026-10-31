"""Offline tests for standalone Proxy auto-updates.

No network, no installer launches: HTTP answers are faked, the installer
is a fixture, and the launch is a recording callable. In environments
without httpx the import of ``app.update_release`` is satisfied with a
minimal stub, because release selection itself never touches the network.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import httpx  # noqa: F401
except ImportError:  # pragma: no cover - only on machines without the runtime deps
    _httpx = types.ModuleType("httpx")
    _httpx.RequestError = type("RequestError", (Exception,), {})
    _httpx.HTTPError = Exception
    _httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    _httpx.Timeout = object
    _httpx.AsyncClient = object
    _httpx.Client = object
    sys.modules["httpx"] = _httpx

from app import proxy_updates, update_release
from app.proxy_updates import ProxyUpdater, UpdateError, edition_info, installer_name

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

EXE = b"MZ" + b"proxy installer fixture" * 50
EXE_SHA = hashlib.sha256(EXE).hexdigest()
PROXY_URL = "https://github.com/MemuAI/MemuAI/releases/download/v4.2/MemuAIProxy-Setup-4.2.exe"
SUITE_URL = "https://github.com/MemuAI/MemuAI/releases/download/v4.2/MemuAI-Setup-4.2.exe"


def proxy_payload(version="4.2", tag="v4.2", digest=EXE_SHA, size=None, extra=None):
    asset = {"name": f"MemuAIProxy-Setup-{version}.exe",
             "browser_download_url": PROXY_URL,
             "size": len(EXE) if size is None else size,
             "state": "uploaded"}
    if digest is not None:
        asset["digest"] = "sha256:" + digest
    payload = {"tag_name": tag, "assets": [asset]}
    if extra:
        payload["assets"].extend(extra)
    return payload


def suite_asset():
    return {"name": "MemuAI-Setup-4.2.exe", "browser_download_url": SUITE_URL,
            "size": len(EXE), "digest": "sha256:" + EXE_SHA, "state": "uploaded"}


def proxy_offer(payload=None, current="4.1"):
    return update_release.release_offer(proxy_payload() if payload is None else payload,
                                        current, installer="proxy")


class FakeResponse:
    def __init__(self, body, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = dict(headers or {})

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self, chunk_size=None):
        step = chunk_size or 1024
        for at in range(0, len(self._body), step):
            yield self._body[at:at + step]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeClient:
    """httpx.Client-shaped fake: stream() returns a context manager."""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.requested = []

    def stream(self, method, url, follow_redirects=False):
        self.requested.append(url)
        if url not in self.mapping:
            raise AssertionError(f"unexpected download URL: {url}")
        return self.mapping[url]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeStore:
    def __init__(self):
        self.settings = SimpleNamespace(updates_enabled=True, updates_auto_download=True,
                                        updates_interval_hours=24, updates_channel="stable")

    def apply_updates(self, updates):
        rejected = []
        for key, value in updates.items():
            if key == "updates_interval_hours" and value not in (6, 12, 24, 168):
                rejected.append(key)
            elif key == "updates_channel" and value not in ("stable", "preview"):
                rejected.append(key)
            elif key in ("updates_enabled", "updates_auto_download") and not isinstance(value, bool):
                rejected.append(key)
        if rejected:
            return rejected
        for key, value in updates.items():
            setattr(self.settings, key, value)
        return []


def make_updater(tmp, *, offer=None, checksum_url=None, client=None, current="4.1",
                 store=None, clock=None):
    fetcher = lambda cur, channel: (offer, checksum_url)
    updater = ProxyUpdater(root=Path(tmp) / "updates", current_version=current,
                           client_factory=lambda: client, clock=clock or (lambda: 1_700_000_000.0),
                           offer_fetcher=fetcher)
    if store is not None:
        patcher = patch.object(proxy_updates, "config_store", store)
        patcher.start()
        updater._store_patcher = patcher
    return updater


class ReleaseOfferTests(unittest.TestCase):
    def test_proxy_asset_is_offered_to_proxy_kind_only(self):
        offer = proxy_offer()
        self.assertTrue(offer["update_available"])
        self.assertEqual(offer["download_url"], PROXY_URL)
        self.assertEqual(offer["latest_version"], "4.2")
        self.assertEqual(offer["sha256"], EXE_SHA)

    def test_suite_offer_ignores_proxy_asset(self):
        payload = proxy_payload(extra=[suite_asset()])
        offer = update_release.release_offer(payload, "4.1")
        self.assertEqual(offer["download_url"], SUITE_URL)

    def test_proxy_offer_ignores_suite_asset(self):
        payload = {"tag_name": "v4.2", "assets": [suite_asset()]}
        offer = update_release.release_offer(payload, "4.1", installer="proxy")
        self.assertFalse(offer["update_available"])
        self.assertEqual(offer["status"], "missing_installer")

    def test_proxy_offer_without_asset_is_missing_installer(self):
        payload = {"tag_name": "v4.2", "assets": []}
        offer = update_release.release_offer(payload, "4.1", installer="proxy")
        self.assertFalse(offer["update_available"])
        self.assertEqual(offer["status"], "missing_installer")

    def test_custom_manifest_never_offers_suite_installer_as_proxy(self):
        offer = update_release.release_offer(
            {"version": "4.2", "download_url": SUITE_URL}, "4.1", installer="proxy")
        self.assertFalse(offer["update_available"])
        self.assertEqual(offer["status"], "missing_installer")
        suite = update_release.release_offer(
            {"version": "4.2", "download_url": SUITE_URL}, "4.1")
        self.assertTrue(suite["update_available"])

    def test_drafts_and_prereleases_are_excluded_for_proxy(self):
        for flag in ("draft", "prerelease"):
            payload = dict(proxy_payload(), **{flag: True})
            self.assertFalse(proxy_offer(payload)["update_available"])

    def test_invalid_digest_or_size_is_rejected_for_proxy(self):
        for fields in ({"digest": "sha256:broken"},
                       {"size": update_release.MAX_INSTALLER_SIZE + 1},
                       {"browser_download_url": "http://insecure.example/a.exe"}):
            payload = proxy_payload()
            payload["assets"][0].update(fields)
            with self.assertRaises(ValueError):
                proxy_offer(payload)

    def test_unknown_installer_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            update_release.release_offer(proxy_payload(), "4.1", installer="memufiles")


def write_memuai_json(path, url="http://127.0.0.1:8317", updated=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"url": url}
    if updated is not None:
        data["updated"] = updated
    path.write_text(json.dumps(data), encoding="utf-8")


class EditionInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = {"APPDATA": str(self.base)}
        self.suite_file = self.base / "MemuAI" / "memuai" / "memuai.json"

    def test_flag_only_is_proxy_only_without_memuai(self):
        info = edition_info(data_dir=self.base / "MemuAIProxy", environ={**self.env, "MEMUAI_PROXY_ONLY": "1"},
                            registry_check=lambda: False)
        self.assertEqual(info, {"edition": "proxy-only", "proxy_only": True, "memuai_present": False})

    def test_data_dir_name_alone_marks_proxy_only(self):
        info = edition_info(data_dir=self.base / "MemuAIProxy", environ=self.env,
                            registry_check=lambda: False)
        self.assertTrue(info["proxy_only"])
        self.assertFalse(info["memuai_present"])

    def test_memuai_json_file_marks_memuai_present(self):
        write_memuai_json(self.suite_file)
        info = edition_info(data_dir=self.base / "MemuAIProxy", environ=self.env,
                            ping=lambda url: False, registry_check=lambda: False)
        self.assertTrue(info["proxy_only"])
        self.assertTrue(info["memuai_present"])

    def test_suite_data_dir_without_flag_is_suite_edition(self):
        write_memuai_json(self.suite_file)
        info = edition_info(data_dir=self.base / "MemuAI", environ=self.env,
                            ping=lambda url: False, registry_check=lambda: False)
        self.assertEqual(info["edition"], "suite")
        self.assertFalse(info["proxy_only"])
        self.assertTrue(info["memuai_present"])

    def test_registry_alone_marks_memuai_present(self):
        info = edition_info(data_dir=self.base / "MemuAIProxy", environ=self.env,
                            registry_check=lambda: True)
        self.assertTrue(info["memuai_present"])

    def test_ping_saves_stale_runtime_file(self):
        write_memuai_json(self.suite_file, updated=1_700_000_000.0 - 40 * 24 * 3600)
        info = edition_info(data_dir=self.base / "MemuAIProxy", environ=self.env,
                            ping=lambda url: True, registry_check=lambda: False,
                            now=1_700_000_000.0)
        self.assertTrue(info["memuai_present"])

    def test_stale_file_with_dead_ping_and_no_registry_is_absent(self):
        write_memuai_json(self.suite_file, updated=1_700_000_000.0 - 40 * 24 * 3600)
        info = edition_info(data_dir=self.base / "MemuAIProxy", environ=self.env,
                            ping=lambda url: False, registry_check=lambda: False,
                            now=1_700_000_000.0)
        self.assertFalse(info["memuai_present"])

    def test_non_loopback_runtime_url_is_ignored(self):
        write_memuai_json(self.suite_file, url="http://example.com:8317")
        info = edition_info(data_dir=self.base / "MemuAIProxy", environ=self.env,
                            ping=lambda url: True, registry_check=lambda: False)
        self.assertFalse(info["memuai_present"])

    def test_edition_info_never_raises(self):
        info = edition_info(data_dir=object(), environ=None,
                            ping=lambda url: 1 / 0, registry_check=lambda: 1 / 0)
        self.assertIn(info["edition"], ("suite", "proxy-only"))


class SettingsTests(unittest.TestCase):
    def test_settings_dataclass_defaults_match_memumusic(self):
        from app.config import Settings
        settings = Settings()
        self.assertIs(settings.updates_enabled, True)
        self.assertIs(settings.updates_auto_download, True)
        self.assertEqual(settings.updates_interval_hours, 24)
        self.assertEqual(settings.updates_channel, "stable")

    def test_settings_validation_and_persistence(self):
        store = FakeStore()
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), store=store,
                                   client=FakeClient({}))
            try:
                for bad in ({"enabled": "yes"}, {"channel": "beta"}, {"interval_hours": 7},
                            {"interval_hours": True}, {"surprise": 1}, []):
                    with self.assertRaises(UpdateError) as caught:
                        updater.settings(bad)
                    self.assertEqual(caught.exception.code, "invalid_settings")
                status = updater.settings({"interval_hours": 12, "auto_download": False})
                self.assertEqual(status["config"]["interval_hours"], 12)
                self.assertIs(status["config"]["auto_download"], False)
                self.assertEqual(store.settings.updates_interval_hours, 12)
            finally:
                updater._store_patcher.stop()

    def test_channel_change_resets_cached_offer(self):
        store = FakeStore()
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), store=store,
                                   client=FakeClient({}))
            try:
                updater._check()
                self.assertEqual(updater.status()["phase"], "available")
                updater.settings({"channel": "preview"})
                status = updater.status()
                self.assertEqual(status["phase"], "idle")
                self.assertIsNone(status["latest"])
                self.assertEqual(status["last_checked"], 0)
            finally:
                updater._store_patcher.stop()


class UpdaterFlowTests(unittest.TestCase):
    def test_check_reports_available_and_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=FakeClient({}))
            self.assertTrue(updater._check())
            status = updater.status()
            self.assertEqual(status["phase"], "available")
            self.assertEqual(status["latest"]["version"], "4.2")
            self.assertEqual(status["current_version"], "4.1")
            self.assertEqual(status["last_checked"], 1_700_000_000.0)

            current_offer = update_release.release_offer(proxy_payload(), "4.2", installer="proxy")
            updater.offer_fetcher = lambda cur, channel: (current_offer, None)
            self.assertFalse(updater._check())
            self.assertEqual(updater.status()["phase"], "up_to_date")

    def test_missing_installer_is_an_error_code(self):
        offer = {"current_version": "4.1", "latest_version": "4.2", "update_available": False,
                 "release_url": update_release.RELEASES_URL, "status": "missing_installer"}
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=offer, client=FakeClient({}))
            with self.assertRaises(UpdateError) as caught:
                updater._live_offer()
            self.assertEqual(caught.exception.code, "installer_missing")
            updater._error(caught.exception)
            self.assertEqual(updater.status()["phase"], "error")
            self.assertEqual(updater.status()["error"], "installer_missing")

    def test_download_verifies_and_marks_ready(self):
        client = FakeClient({PROXY_URL: FakeResponse(EXE)})
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=client)
            updater._check()
            updater._download()
            status = updater.status()
            self.assertEqual(status["phase"], "ready")
            self.assertEqual(status["checksum"], EXE_SHA)
            self.assertTrue(updater.path.is_file())
            self.assertEqual(updater.path.read_bytes(), EXE)
            self.assertEqual(list(Path(tmp, "updates").glob("*.part")), [])

    def test_download_uses_checksum_file_when_offer_has_none(self):
        payload = proxy_payload(digest=None)
        offer = update_release.release_offer(payload, "4.1", installer="proxy")
        self.assertEqual(offer["sha256"], "")
        checksum_url = PROXY_URL + ".sha256"
        body = (EXE_SHA + "  " + installer_name("4.2") + "\n").encode()
        client = FakeClient({checksum_url: FakeResponse(body), PROXY_URL: FakeResponse(EXE)})
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=offer, checksum_url=checksum_url, client=client)
            updater._check()
            updater._download()
            self.assertEqual(updater.status()["phase"], "ready")
            self.assertEqual(updater.status()["checksum"], EXE_SHA)

    def test_corrupt_download_is_rejected_and_cleaned_up(self):
        bad = EXE[:-1] + b"!"
        client = FakeClient({PROXY_URL: FakeResponse(bad)})
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=client)
            updater._check()
            with self.assertRaises(UpdateError) as caught:
                updater._download()
            self.assertEqual(caught.exception.code, "checksum_mismatch")
            self.assertFalse(updater.path.exists())
            self.assertEqual(list(Path(tmp, "updates").glob("*.part")), [])

    def test_untrusted_download_url_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=FakeClient({}))
            updater._state["latest"] = {"version": "4.2", "release_url": update_release.RELEASES_URL,
                                        "download_url": "http://evil.example/setup.exe",
                                        "sha256": EXE_SHA, "size": len(EXE), "checksum_url": None}
            with self.assertRaises(UpdateError) as caught:
                updater._download()
            self.assertEqual(caught.exception.code, "untrusted_url")

    def test_download_without_check_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=FakeClient({}))
            with self.assertRaises(UpdateError) as caught:
                updater._download()
            self.assertEqual(caught.exception.code, "no_update")

    def test_install_is_unsupported_off_windows(self):
        client = FakeClient({PROXY_URL: FakeResponse(EXE)})
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=client)
            updater._check()
            updater._download()
            with self.assertRaises(UpdateError) as caught:
                updater.install(launcher=MagicMock())
            self.assertEqual(caught.exception.code, "unsupported")

    def test_install_without_download_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=FakeClient({}))
            with patch.object(sys, "platform", "win32"), \
                    patch.object(sys, "frozen", True, create=True):
                with self.assertRaises(UpdateError) as caught:
                    updater.install(launcher=MagicMock())
            self.assertEqual(caught.exception.code, "not_downloaded")

    def test_install_revalidates_and_launches_through_installer_environment(self):
        calls = {"fetcher": 0}
        offer = proxy_offer()

        def fetcher(cur, channel):
            calls["fetcher"] += 1
            return offer, None

        client = FakeClient({PROXY_URL: FakeResponse(EXE)})
        launched = {}

        def launcher(args, cwd=None, env=None, creationflags=0, close_fds=False):
            launched.update(args=args, cwd=cwd, env=env, creationflags=creationflags)
            child = MagicMock()
            child.poll.return_value = None
            return child

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"_PYI_TEST_MARKER": "1"}), \
                patch.object(sys, "platform", "win32"), \
                patch.object(sys, "frozen", True, create=True):
            updater = ProxyUpdater(root=Path(tmp) / "updates", current_version="4.1",
                                   client_factory=lambda: client,
                                   clock=lambda: 1_700_000_000.0, offer_fetcher=fetcher)
            updater._check()
            updater._download()
            result = updater.install(launcher=launcher)
            self.assertEqual(result, {"launched": True})
            self.assertEqual(launched["args"], [str(updater.path)])
            self.assertEqual(launched["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            self.assertNotIn("_PYI_TEST_MARKER", launched["env"])
            self.assertGreaterEqual(calls["fetcher"], 2)
            closed = []
            updater.close_after_handoff(lambda: closed.append(True))
            self.assertEqual(closed, [True])

    def test_close_after_handoff_without_launch_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), client=FakeClient({}))
            updater.close_after_handoff(lambda: None)
            self.assertEqual(updater.status()["error"], "launch_failure")

    def test_auto_respects_enabled_and_interval(self):
        store = FakeStore()
        store.settings.updates_enabled = False
        with tempfile.TemporaryDirectory() as tmp:
            updater = make_updater(tmp, offer=proxy_offer(), store=store,
                                   client=FakeClient({}))
            try:
                self.assertFalse(updater.auto())
            finally:
                updater._store_patcher.stop()


@unittest.skipUnless(_HAS_FASTAPI, "fastapi is not installed in this environment")
class ProxyUpdateRoutesTests(unittest.TestCase):
    def setUp(self):
        from app.config import config_store
        from app.main import create_app
        self.host_patch = patch.object(config_store, "_runtime_host", "127.0.0.1")
        self.port_patch = patch.object(config_store, "_runtime_port", 8317)
        self.host_patch.start()
        self.port_patch.start()
        self.addCleanup(self.host_patch.stop)
        self.addCleanup(self.port_patch.stop)
        self.app = create_app()
        self.fake = SimpleNamespace(
            status=lambda: {"phase": "available", "current_version": "4.1",
                            "latest": {"version": "4.2"}, "config": {"enabled": True},
                            "busy": False, "can_install": True},
            check=MagicMock(return_value=True),
            download=MagicMock(return_value=True),
            install=MagicMock(return_value={"launched": True}),
            settings=MagicMock(return_value={"phase": "idle", "config": {}}))
        self.app.state.proxy_updater = self.fake
        self.app.state.shutdown_callback = MagicMock()
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8317")
        self.addCleanup(self.client.close)

    def test_routes_are_registered(self):
        paths = {getattr(route, "path", "") for route in self.app.routes}
        for path in ("/api/admin/updates/status", "/api/admin/updates/check",
                     "/api/admin/updates/download", "/api/admin/updates/install",
                     "/api/admin/updates/settings"):
            self.assertIn(path, paths)

    def test_status_check_and_download_use_the_updater(self):
        self.assertEqual(self.client.get("/api/admin/updates/status").status_code, 200)
        response = self.client.post("/api/admin/updates/check")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["started"])
        self.fake.check.assert_called_once()
        response = self.client.post("/api/admin/updates/download")
        self.assertEqual(response.status_code, 200)
        self.fake.download.assert_called_once()

    def test_settings_validation_error_is_400(self):
        self.fake.settings.side_effect = UpdateError("invalid_settings")
        response = self.client.post("/api/admin/updates/settings", json={"channel": "beta"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_settings")

    def test_install_launches_and_schedules_graceful_close(self):
        timers = []

        class FakeTimer:
            def __init__(self, delay, fn, args=None, kwargs=None):
                timers.append((delay, fn, args))
                self.daemon = False

            def start(self):
                pass

        with patch.object(proxy_updates, "edition_info",
                          return_value={"edition": "proxy-only", "proxy_only": True,
                                        "memuai_present": False}), \
                patch.object(threading, "Timer", FakeTimer):
            response = self.client.post("/api/admin/updates/install")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"launched": True})
        self.fake.install.assert_called_once()
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0][0], 0.6)

    def test_install_is_refused_when_memuai_is_present(self):
        with patch.object(proxy_updates, "edition_info",
                          return_value={"edition": "proxy-only", "proxy_only": True,
                                        "memuai_present": True}):
            response = self.client.post("/api/admin/updates/install")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "suite_managed")
        self.fake.install.assert_not_called()

    def test_install_without_shutdown_callback_is_refused(self):
        self.app.state.shutdown_callback = None
        with patch.object(proxy_updates, "edition_info",
                          return_value={"edition": "proxy-only", "proxy_only": True,
                                        "memuai_present": False}):
            response = self.client.post("/api/admin/updates/install")
        self.assertEqual(response.status_code, 409)
        self.fake.install.assert_not_called()


class StaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "app" / "static"
        cls.html = (root / "index.html").read_text(encoding="utf-8")
        cls.app_js = (root / "app.js").read_text(encoding="utf-8")
        cls.i18n = (root / "settings-i18n.js").read_text(encoding="utf-8")

    def test_updates_group_is_hidden_inside_system_section(self):
        system = self.html[self.html.index('id="sg-sec-system"'):self.html.index('id="tab-logs"')]
        self.assertIn('id="sg-updates-group" hidden', system)
        self.assertLess(system.index("sg-updates-group"), system.index("sg-group sg-danger"))
        for control in ("upd-check", "upd-download", "upd-install", "upd-enabled",
                        "upd-autodownload", "upd-interval", "upd-channel",
                        "upd-status-label", "upd-current", "upd-last"):
            self.assertIn(f'id="{control}"', system)
        self.assertNotIn('data-setting="updates_', system)

    def test_i18n_keys_exist_in_both_languages(self):
        ru, en = self.i18n.split("Object.assign(I18N.en, {", 1)
        used = set(re.findall(r'data-i18n="(sg\.upd[^"]+)"', self.html))
        used |= set(re.findall(r't\("(sg\.upd[^"]+)"\)', self.app_js))
        used |= {"sg.upd.err." + code for code in (
            "network", "installer_missing", "checksum_mismatch", "checksum_invalid",
            "download_size", "untrusted_url", "update_changed", "not_downloaded",
            "no_update", "invalid_settings", "launch_failure", "busy", "unsupported",
            "invalid_response", "disk", "suite_managed")}
        used.add("sg.g.updates")
        for key in sorted(used):
            self.assertIn(f'"{key}"', ru, key)
            self.assertIn(f'"{key}"', en, key)

    def test_app_js_gates_section_on_overview_edition(self):
        self.assertIn("o.proxy_only && !o.memuai_present", self.app_js)
        self.assertIn('"/api/admin/updates/status"', self.app_js)
        self.assertIn('"/api/admin/updates/" + path', self.app_js)
        self.assertIn('proxyUpdatesAction("settings"', self.app_js)
        self.assertIn("15 * 60 * 1000", self.app_js)
        self.assertIn("upd-nav-badge", self.app_js)


if __name__ == "__main__":
    unittest.main()
