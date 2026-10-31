"""Дата выпуска из релиза на GitHub: парсинг, кэш, фолбэк, неблокировка.

Только стандартная библиотека и моки: реальной сети здесь нет, ответы
GitHub изображает поддельный ``urllib.request.urlopen``. Тесты всегда
передают временный каталог данных, поэтому каталог пользователя не
трогается, а ``app.config`` не импортируется вовсе.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from app import release_date
from app.version import APP_VERSION, RELEASE_DATE


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _github_response(published_at: str) -> _FakeResponse:
    body = json.dumps({"tag_name": "v" + APP_VERSION,
                       "published_at": published_at}).encode("utf-8")
    return _FakeResponse(body)


class ReleaseDateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        # Состояние фоновых потоков — на уровне модуля; каждый тест
        # начинает с чистого листа, иначе неудача одного теста душила
        # бы повторные попытки следующего.
        release_date._refresh_thread = None
        release_date._last_failed_at = float("-inf")

    def tearDown(self):
        thread = release_date._refresh_thread
        if thread is not None:
            thread.join(timeout=5)
        self.temp.cleanup()

    def _cache_file(self) -> Path:
        return self.data_dir / "release_date.json"

    def _seed_cache(self, version: str = APP_VERSION, date: str = "2026-10-30",
                    fetched_at: float | None = None) -> None:
        if fetched_at is None:
            fetched_at = time.time()
        self._cache_file().write_text(
            json.dumps({"version": version, "release_date": date,
                        "fetched_at": fetched_at}), encoding="utf-8")

    def test_parse_published_at(self):
        parse = release_date.parse_published_at
        self.assertEqual(parse("2026-10-30T18:22:10Z"), "2026-10-30")
        self.assertEqual(parse("2026-10-30T18:22:10+03:00"), "2026-10-30")
        self.assertEqual(parse("2026-10-30"), "2026-10-30")
        self.assertEqual(parse("2026-10-30 18:22:10"), "2026-10-30")
        for bad in ("", None, "not a date", "2026-13-01T00:00:00Z",
                    "2026-10-30garbage", "2026-10-30T25:00:00Z",
                    "30.10.2026", 20261030):
            self.assertEqual(parse(bad), "", repr(bad))

    def test_release_url_points_at_the_tagged_release(self):
        self.assertEqual(
            release_date.release_url(),
            "https://api.github.com/repos/MemuAI/MemuAI/releases/tags/v" + APP_VERSION)
        self.assertEqual(
            release_date.release_url("4.1"),
            "https://api.github.com/repos/MemuAI/MemuAI/releases/tags/v4.1")

    def test_cache_roundtrip_expiry_and_wrong_version(self):
        now = 1_700_000_000.0
        self.assertEqual(
            release_date.read_cached_release_date(self.data_dir, now=now), "")
        self._seed_cache(fetched_at=now)
        self.assertEqual(
            release_date.read_cached_release_date(self.data_dir, now=now),
            "2026-10-30")
        self.assertEqual(
            release_date.read_cached_release_date(
                self.data_dir, now=now + release_date.CACHE_TTL_SECONDS + 1), "")
        self.assertEqual(
            release_date.read_cached_release_date(
                self.data_dir, version="9.9", now=now), "")

    def test_corrupt_cache_falls_back(self):
        self._cache_file().write_text("{not json", encoding="utf-8")
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("offline")) as _mock:
            date, source = release_date.resolve_release_date(self.data_dir)
            thread = release_date._refresh_thread
            if thread is not None:
                thread.join(timeout=5)
        self.assertEqual((date, source), (RELEASE_DATE, "fallback"))

    def test_fresh_cache_wins_and_network_is_never_touched(self):
        self._seed_cache()
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("network must not be used")):
            date, source = release_date.resolve_release_date(self.data_dir)
        self.assertEqual((date, source), ("2026-10-30", "github"))

    def test_offline_falls_back_to_version_py(self):
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("offline")):
            started = time.monotonic()
            date, source = release_date.resolve_release_date(self.data_dir)
            elapsed = time.monotonic() - started
            # Мок должен действовать, пока фоновый поток не завершится,
            # иначе поток уйдёт в настоящую сеть после выхода из контекста.
            thread = release_date._refresh_thread
            if thread is not None:
                thread.join(timeout=5)
        self.assertEqual((date, source), (RELEASE_DATE, "fallback"))
        self.assertLess(elapsed, 1.0, "resolve must not wait for the network")
        self.assertFalse(self._cache_file().exists(),
                         "a failed fetch must not be cached")

    def test_background_fetch_does_not_block_the_caller(self):
        calls = []
        main_thread = threading.get_ident()

        def slow_fetch(request, timeout=None):
            calls.append(threading.get_ident())
            time.sleep(0.5)
            return _github_response("2026-11-02T09:00:00Z")

        with patch("urllib.request.urlopen", side_effect=slow_fetch):
            started = time.monotonic()
            date, source = release_date.resolve_release_date(self.data_dir)
            elapsed = time.monotonic() - started
            thread = release_date._refresh_thread
            self.assertIsNotNone(thread)
            # Мок действует до завершения потока: загрузка идёт в фоне,
            # а вызов уже вернулся с фолбэком.
            thread.join(timeout=5)
        self.assertEqual((date, source), (RELEASE_DATE, "fallback"))
        self.assertLess(elapsed, 0.3, "the caller must return before the fetch")
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], main_thread,
                            "the fetch must run in a background thread")
        self.assertFalse(thread.is_alive())
        # Фоновая загрузка записала дату в кэш: следующий вызов берёт её
        # оттуда и в сеть уже не ходит.
        self.assertEqual(
            release_date.read_cached_release_date(self.data_dir), "2026-11-02")
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("network must not be used")):
            date, source = release_date.resolve_release_date(self.data_dir)
        self.assertEqual((date, source), ("2026-11-02", "github"))

    def test_second_call_while_refreshing_does_not_start_another_fetch(self):
        calls = []

        def slow_fetch(request, timeout=None):
            calls.append(1)
            time.sleep(0.3)
            return _github_response("2026-11-02T09:00:00Z")

        with patch("urllib.request.urlopen", side_effect=slow_fetch):
            first = release_date.resolve_release_date(self.data_dir)
            second = release_date.resolve_release_date(self.data_dir)
            thread = release_date._refresh_thread
            self.assertIsNotNone(thread)
            thread.join(timeout=5)
        self.assertEqual(first, (RELEASE_DATE, "fallback"))
        self.assertEqual(second, (RELEASE_DATE, "fallback"))
        self.assertEqual(len(calls), 1, "one refresh in flight, not two")


if __name__ == "__main__":
    unittest.main()
