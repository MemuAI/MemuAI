"""Focused contracts for desktop startup, activation, and shutdown."""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main as proxy_entry
import memuai as desktop_entry
from app import desktop
import app.config as config_module
import app.main as proxy_app
import app.storage as storage_module
from app.config import ConfigStore, Settings, hash_password
from app.storage import load_json_with_recovery, mutate_json, save_json


def _increment_json_worker(path: str, start, iterations: int) -> None:
    start.wait()
    for _ in range(iterations):
        def increment(value):
            value["count"] += 1
            return True
        mutate_json(path, {"count": 0}, increment, backup=False)


def _nested_json_worker(path: str) -> None:
    def nested(value):
        save_json(path, {"inner": True}, backup=False)
        load_json_with_recovery(path, {})
        value["outer"] = True
        return True
    mutate_json(path, {}, nested, backup=False)


def _preference_worker(data_dir: str, start, update: dict) -> None:
    os.environ["MEMUAI_DATA_DIR"] = data_dir
    from app import pgdata
    start.wait()
    for _ in range(20):
        pgdata.save_preferences(update)


class _BindSocket:
    def __init__(self, family: int, calls: list[tuple[int, tuple]], *, busy: bool = False) -> None:
        self.family = family
        self.calls = calls
        self.busy = busy

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def bind(self, address: tuple) -> None:
        self.calls.append((self.family, address))
        if self.busy:
            raise OSError("busy")


class PortSelectionContractTest(unittest.TestCase):
    def test_proxy_port_probe_uses_ipv6_for_ipv6_bind(self) -> None:
        calls: list[tuple[int, tuple]] = []
        with patch.object(proxy_entry.socket, "socket",
                          side_effect=lambda family, _kind: _BindSocket(family, calls)):
            self.assertEqual(proxy_entry.pick_port(8317, "::1"), 8317)
        self.assertEqual(calls, [(proxy_entry.socket.AF_INET6, ("::1", 8317))])

    def test_port_scan_never_runs_past_65535_or_returns_a_busy_port(self) -> None:
        calls: list[tuple[int, tuple]] = []
        with patch.object(proxy_entry.socket, "socket",
                          side_effect=lambda family, _kind: _BindSocket(family, calls, busy=True)):
            with self.assertRaises(RuntimeError):
                proxy_entry.pick_port(65535, "127.0.0.1")
        self.assertEqual([address[1] for _, address in calls], [65535])

        calls.clear()
        with patch.object(desktop_entry.socket, "socket",
                          side_effect=lambda family, _kind: _BindSocket(family, calls, busy=True)):
            with self.assertRaises(RuntimeError):
                desktop_entry.pick_port(65535)
        self.assertEqual([address[1] for _, address in calls], [65535])


class RuntimeDiscoveryContractTest(unittest.TestCase):
    def test_runtime_files_follow_custom_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
                os.environ, {"MEMUAI_DATA_DIR": temp, "OMNIHUB_DATA_DIR": ""}):
            expected = Path(temp) / "memuai" / "proxy.json"
            self.assertEqual(Path(proxy_entry._runtime_file("proxy.json")), expected)
            self.assertEqual(desktop_entry._runtime_file("proxy.json"), expected)

    def test_runtime_url_is_structurally_local_and_supports_ipv6(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime.json"
            with patch.object(desktop_entry, "_runtime_file", return_value=runtime):
                runtime.write_text(json.dumps({
                    "url": "http://127.0.0.1:8317@evil.example",
                }), encoding="utf-8")
                self.assertEqual(desktop_entry._read_runtime_url("runtime.json", "fallback"), "fallback")

                runtime.write_text(json.dumps({"url": "http://[::1]:8317"}), encoding="utf-8")
                self.assertEqual(desktop_entry._read_runtime_url("runtime.json"), "http://[::1]:8317")

            with patch.object(proxy_entry, "_runtime_file", return_value=str(runtime)):
                runtime.write_text(json.dumps({
                    "url": "http://attacker@127.0.0.1:8317/redirect",
                }), encoding="utf-8")
                self.assertEqual(proxy_entry._read_local_runtime("runtime.json"), {})

                runtime.write_text(json.dumps({"url": "http://[::1]:8317"}), encoding="utf-8")
                self.assertEqual(
                    proxy_entry._read_local_runtime("runtime.json")["url"],
                    "http://[::1]:8317",
                )

    def test_second_launch_reports_failed_window_activation(self) -> None:
        with patch.object(desktop_entry, "_read_runtime_url", return_value="http://127.0.0.1:8600"), \
                patch.object(desktop_entry, "_is_online", return_value=True), \
                patch.object(desktop_entry.urllib.request, "urlopen", side_effect=OSError("closed")):
            self.assertFalse(desktop_entry._activate_memuai())

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return b'{"ok": false}'

        with patch.object(desktop_entry, "_read_runtime_url", return_value="http://127.0.0.1:8600"), \
                patch.object(desktop_entry, "_is_online", return_value=True), \
                patch.object(desktop_entry.urllib.request, "urlopen", return_value=Response()):
            self.assertFalse(desktop_entry._activate_memuai())

    def test_second_launch_activates_existing_window(self) -> None:
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self) -> bytes: return b'{"ok": true}'

        with patch.object(desktop_entry, "_read_runtime_url", return_value="http://127.0.0.1:8600"), \
                patch.object(desktop_entry, "_is_online", return_value=True), \
                patch.object(desktop_entry.urllib.request, "urlopen", return_value=Response()):
            self.assertTrue(desktop_entry._activate_memuai())

    def test_second_proxy_launch_reuses_healthy_process(self) -> None:
        class Response:
            status = 200
            def __init__(self, body: bytes = b''): self.body = body
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self) -> bytes: return self.body
            def close(self) -> None: pass

        with patch.object(proxy_entry, "_read_local_runtime",
                          return_value={"url": "http://127.0.0.1:8317"}), \
                patch.object(proxy_entry.urllib.request, "urlopen",
                             side_effect=[Response(b'{"service":"memuai-proxy"}'), Response()]):
            self.assertTrue(proxy_entry.activate_running_proxy())


class ServerLifecycleContractTest(unittest.TestCase):
    def test_readiness_wait_stops_when_server_thread_has_exited(self) -> None:
        dead_thread = SimpleNamespace(is_alive=lambda: False)
        calls = 0

        def failed_probe() -> bool:
            nonlocal calls
            calls += 1
            raise OSError("not listening")

        with patch.object(desktop.time, "sleep") as sleep:
            self.assertFalse(desktop.wait_for_service(
                failed_probe, dead_thread, attempts=100, interval=0.2))
        self.assertEqual(calls, 1)
        sleep.assert_not_called()
        self.assertFalse(desktop.wait_for_service(
            lambda: True, dead_thread, attempts=1, interval=0))

    def test_server_shutdown_joins_and_escalates_only_after_timeout(self) -> None:
        server = SimpleNamespace(should_exit=False, force_exit=False)

        class SlowThread:
            def __init__(self) -> None:
                self.joins: list[float] = []

            def join(self, timeout: float) -> None:
                self.joins.append(timeout)

            def is_alive(self) -> bool:
                return len(self.joins) == 1

        thread = SlowThread()
        desktop.stop_server(server, thread, timeout=3.0)
        self.assertTrue(server.should_exit)
        self.assertTrue(server.force_exit)
        self.assertEqual(thread.joins, [3.0, 1.0])

    async def _exercise_failed_lifespan(self) -> tuple[AsyncMock, AsyncMock]:
        async def idle_forever() -> None:
            import asyncio
            await asyncio.Event().wait()

        provider = SimpleNamespace(close=AsyncMock())
        close_all = AsyncMock()
        with patch.object(proxy_app, "init_registry"), \
                patch.object(proxy_app.hub_http, "configure"), \
                patch.object(proxy_app, "all_providers", return_value={"test": provider}), \
                patch.object(proxy_app.hub_http, "close_all", close_all), \
                patch("app.scheduler.scheduler_loop", idle_forever), \
                patch("app.telegram_bot.telegram_loop", idle_forever), \
                patch.object(proxy_app, "_cookie_checker", idle_forever):
            app = proxy_app.create_app()
            with self.assertRaisesRegex(RuntimeError, "serving failed"):
                async with app.router.lifespan_context(app):
                    raise RuntimeError("serving failed")
        return provider.close, close_all

    def test_lifespan_always_cleans_tasks_and_provider_clients(self) -> None:
        import asyncio
        provider_close, close_all = asyncio.run(self._exercise_failed_lifespan())
        provider_close.assert_awaited_once()
        close_all.assert_awaited_once()


class ConfigRecoveryContractTest(unittest.TestCase):
    @staticmethod
    def _valid_settings(**updates) -> dict:
        settings = asdict(Settings())
        digest, salt = hash_password("not-default")
        settings.update({"admin_password_hash": digest, "admin_password_salt": salt})
        settings.update(updates)
        return settings

    def test_missing_or_wrong_shape_main_recovers_valid_config_backup(self) -> None:
        for broken_main in (None, []):
            with self.subTest(broken_main=broken_main), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "config.json"
                expected = self._valid_settings(api_keys=["keep-me"], port=9123)
                save_json(path, expected)
                if broken_main is None:
                    path.unlink()
                else:
                    path.write_text(json.dumps(broken_main), encoding="utf-8")
                with patch.object(config_module, "CONFIG_PATH", str(path)):
                    store = ConfigStore()
                self.assertEqual(store.settings.api_keys, ["keep-me"])
                self.assertEqual(store.settings.port, 9123)
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_malformed_security_field_uses_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            expected = self._valid_settings(api_keys=["keep-me"])
            save_json(path, expected)
            malformed = dict(expected)
            malformed["api_keys"] = "broken"
            path.write_text(json.dumps(malformed), encoding="utf-8")
            with patch.object(config_module, "CONFIG_PATH", str(path)):
                store = ConfigStore()
            self.assertEqual(store.settings.api_keys, ["keep-me"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["api_keys"], ["keep-me"])

    def test_recovered_backup_is_returned_even_if_main_cannot_be_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            save_json(path, {"kept": True})
            path.write_text("broken", encoding="utf-8")
            with patch.object(storage_module, "_write_unlocked", side_effect=PermissionError("read-only")):
                recovered = load_json_with_recovery(path, {})
            self.assertEqual(recovered, {"kept": True})

    def test_runtime_endpoint_never_overwrites_persisted_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            with patch.object(config_module, "CONFIG_PATH", str(path)):
                store = ConfigStore()
                self.assertEqual(store.apply_updates({"host": "0.0.0.0", "port": 8317}), [])
                store.set_runtime_endpoint("127.0.0.1", 8318)
                store.apply_updates({"log_requests": False})
                persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((persisted["host"], persisted["port"]), ("0.0.0.0", 8317))
            self.assertEqual((store.effective_host, store.effective_port), ("127.0.0.1", 8318))

    def test_malformed_updates_are_rejected_without_clearing_security_state(self) -> None:
        store = ConfigStore.__new__(ConfigStore)
        store._lock = threading.RLock()
        store._runtime_host = None
        store._runtime_port = None
        store.settings = Settings(
            api_keys=["keep"], provider_settings={"custom": {"token": "keep"}})
        self.assertEqual(store.apply_updates(["wrong-root"]), ["settings"])
        rejected = store.apply_updates({
            "api_keys": "broken",
            "provider_settings": [],
            "host": "http://127.0.0.1:8317",
            "port": 70_000,
            "default_provider": ["custom"],
        })
        self.assertEqual(set(rejected), {
            "api_keys", "provider_settings", "host", "port", "default_provider"})
        self.assertEqual(store.settings.api_keys, ["keep"])
        self.assertEqual(store.settings.provider_settings, {"custom": {"token": "keep"}})
        self.assertEqual(store.settings.host, "127.0.0.1")
        self.assertEqual(store.settings.port, 8317)


class CrossProcessStorageContractTest(unittest.TestCase):
    def test_multiprocess_mutation_preserves_every_increment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "counter.json")
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            workers = [
                context.Process(target=_increment_json_worker, args=(path, start, 30))
                for _ in range(4)
            ]
            for worker in workers:
                worker.start()
            start.set()
            for worker in workers:
                worker.join(20)
                self.assertFalse(worker.is_alive(), "storage transaction deadlocked")
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(load_json_with_recovery(path, {})["count"], 120)

    def test_nested_storage_calls_are_reentrant(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "nested.json")
            context = multiprocessing.get_context("spawn")
            worker = context.Process(target=_nested_json_worker, args=(path,))
            worker.start()
            worker.join(10)
            self.assertFalse(worker.is_alive(), "nested JSON transaction deadlocked")
            self.assertEqual(worker.exitcode, 0)
            self.assertEqual(load_json_with_recovery(path, {}), {"outer": True})

    def test_multiprocess_preference_merges_do_not_lose_unrelated_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
                os.environ, {"MEMUAI_DATA_DIR": temp}):
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            updates = [
                {"language": "en"},
                {"theme": "light"},
                {"font_scale": 125},
                {"telegram_notify": False},
            ]
            workers = [
                context.Process(target=_preference_worker, args=(temp, start, update))
                for update in updates
            ]
            for worker in workers:
                worker.start()
            start.set()
            for worker in workers:
                worker.join(20)
                self.assertFalse(worker.is_alive(), "preference merge deadlocked")
                self.assertEqual(worker.exitcode, 0)
            saved = json.loads((Path(temp) / "memuai" / "preferences.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["language"], "en")
            self.assertEqual(saved["theme"], "light")
            self.assertEqual(saved["font_scale"], 125)
            self.assertFalse(saved["telegram_notify"])

    def test_relative_path_is_supported(self) -> None:
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                save_json("relative.json", {"ok": True}, backup=False)
                self.assertEqual(load_json_with_recovery("relative.json", {}), {"ok": True})
            finally:
                os.chdir(original)


class TrayFallbackContractTest(unittest.TestCase):
    class EventHook:
        def __iadd__(self, _callback):
            return self

    class FakeWindow:
        def __init__(self) -> None:
            self.hidden = False
            self.events = SimpleNamespace(closing=TrayFallbackContractTest.EventHook())

        def hide(self) -> None:
            self.hidden = True

    def test_close_to_tray_does_not_hide_when_tray_is_unavailable(self) -> None:
        shell = desktop.DesktopShell("http://127.0.0.1", close_to_tray=True)
        shell.window = self.FakeWindow()
        shell.tray = None
        self.assertTrue(shell._on_closing())
        self.assertFalse(shell.window.hidden)

        shell.tray = object()
        self.assertFalse(shell._on_closing())
        self.assertTrue(shell.window.hidden)

    def test_background_start_shows_window_if_tray_failed(self) -> None:
        created: dict = {}

        def create_window(*_args, **kwargs):
            created.update(kwargs)
            return self.FakeWindow()

        fake_webview = SimpleNamespace(create_window=create_window, start=lambda **_kwargs: None)
        shell = desktop.DesktopShell(
            "http://127.0.0.1", show_tray=True, show_on_start=False)
        with patch.dict(sys.modules, {"webview": fake_webview}), \
                patch.object(shell, "_start_tray", return_value=False):
            shell.run()
        self.assertFalse(created["hidden"])


if __name__ == "__main__":
    unittest.main()
