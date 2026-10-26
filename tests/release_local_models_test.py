"""Isolated ownership regressions for concurrent llama-server lifecycle calls."""
from concurrent.futures import ThreadPoolExecutor
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx

from app import local_models as lm

class Process:
    def __init__(self, *_args, **_kwargs):
        self.returncode = None
        self.stdout = io.BytesIO()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, **_kwargs):
        return self.returncode

    def kill(self):
        self.returncode = 1

class PoolLifecycleRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memuai-pool-fixture-")
        root = Path(self.temp.name)
        models = {}
        for slug in ("a", "b"):
            path = root / (slug + ".gguf")
            path.write_bytes(b"fixture")
            models[slug] = {"path": str(path), "meta": {}}
        self.pool = lm.Pool()
        self.pool.command_override = ["fixture"]
        patches = [patch.object(lm, "_load_catalog", return_value={
            "models": models, "settings": {"max_loaded": 1}, "runtime": {}}),
            patch.object(lm.subprocess, "Popen", Process),
            patch.object(lm, "_free_port", side_effect=[12341, 12342]),
            patch.object(lm, "_kill_with_proxy"),
            patch.object(self.pool, "_pump"),
            patch.object(self.pool, "_start_reaper"),
            patch.object(lm.time, "sleep")]
        for patched in patches:
            patched.start()
            self.addCleanup(patched.stop)
        self.addCleanup(self.temp.cleanup)

    def test_unload_during_unsuccessful_health_does_not_dereference_none(self):
        self._stop_during_health(503)

    def test_unload_during_successful_health_cannot_publish_ready(self):
        self._stop_during_health(200)

    def _stop_during_health(self, status):
        entered, release = threading.Event(), threading.Event()

        def health(*_args, **_kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return httpx.Response(status)

        with patch.object(httpx, "get", health), ThreadPoolExecutor(1) as workers:
            future = workers.submit(self.pool.ensure, "a", 5)
            try:
                self.assertTrue(entered.wait(3))
                self.pool.stop("a")
            finally:
                release.set()
            with self.assertRaises(lm.LocalModelError):
                future.result(timeout=3)
        self.assertEqual(self.pool.states()["a"]["state"], "stopped")
        self.assertEqual(self.pool.states()["a"]["error"], "")
        self.assertIsNone(self.pool._server("a").process)

    def test_competing_model_eviction_cancels_original_loader(self):
        entered, release = threading.Event(), threading.Event()

        def health(url, **_kwargs):
            if ":12341/" in url:
                entered.set()
                self.assertTrue(release.wait(3))
            return httpx.Response(200)

        with patch.object(httpx, "get", health), ThreadPoolExecutor(1) as workers:
            first = workers.submit(self.pool.ensure, "a", 5)
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.pool.ensure("b", 5), 12342)
            finally:
                release.set()
            with self.assertRaises(lm.LocalModelError):
                first.result(timeout=3)
        states = self.pool.states()
        self.assertEqual(states["a"]["state"], "stopped")
        self.assertEqual(states["b"]["state"], "ready")
        self.assertIsNotNone(self.pool._server("b").process)

    def test_eviction_before_process_assignment_closes_unpublished_process(self):
        entered, release = threading.Event(), threading.Event()
        processes = []

        def popen(args, **kwargs):
            if args[args.index("--alias") + 1] == "local-a":
                entered.set()
                self.assertTrue(release.wait(3))
            process = Process(args, **kwargs)
            processes.append(process)
            return process

        with patch.object(lm.subprocess, "Popen", popen), \
             patch.object(httpx, "get", return_value=httpx.Response(200)), \
             ThreadPoolExecutor(1) as workers:
            first = workers.submit(self.pool.ensure, "a", 5)
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.pool.ensure("b", 5), 12342)
            finally:
                release.set()
            with self.assertRaises(lm.LocalModelError):
                first.result(timeout=3)
        self.assertEqual(sum(process.poll() is None for process in processes), 1)
        self.assertEqual(self.pool.states()["a"]["state"], "stopped")
        self.assertEqual(self.pool.states()["b"]["state"], "ready")

if __name__ == "__main__":
    unittest.main()
