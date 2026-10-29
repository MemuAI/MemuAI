"""Frozen remote setup/download/SSH need the agent's standalone Python source."""
from __future__ import annotations

import base64
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import build
from app import dfy_remote
from tools import import_graph

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ITEM = "app/dfy_agent.py;app"
CONSUMERS = {"memuai", "proxy", "memucode", "proxy_only"}


class RemotePackagingTests(unittest.TestCase):
    def test_every_frozen_remote_consumer_declares_the_standalone_source(self):
        consumers = set()
        for key, target in build.TARGETS.items():
            graph = import_graph.analyse(target["entry"], target["hidden"] + target.get("collect", []),
                                         target.get("exclude", []))
            if "app.dfy_remote" in graph["app"]:
                consumers.add(key)
                with self.subTest(target=key):
                    self.assertIn(SOURCE_ITEM, target["data"])
        self.assertEqual(consumers, CONSUMERS)

    def test_frozen_location_supplies_exact_source_for_download_and_install_command(self):
        expected = (ROOT / "app" / "dfy_agent.py").read_text("utf-8")
        for key in sorted(CONSUMERS):
            with self.subTest(target=key), tempfile.TemporaryDirectory() as temporary:
                self.assertIn(SOURCE_ITEM, build.TARGETS[key]["data"])
                source, destination = SOURCE_ITEM.split(";", 1)
                folder = Path(temporary) / "_internal" / destination
                folder.mkdir(parents=True)
                (folder / Path(source).name).write_bytes((ROOT / source).read_bytes())
                with patch.object(dfy_remote, "__file__", str(folder / "dfy_remote.py")):
                    self.assertEqual(dfy_remote.agent_source(), expected)
                    commands = dfy_remote.install_command()
                packed = commands["paste"].split(" |", 1)[0].removeprefix("echo ")
                self.assertEqual(gzip.decompress(base64.b64decode(packed)).decode("utf-8"), expected)

    def test_shipped_source_runs_ssh_bootstrap_without_host_app_imports(self):
        source = dfy_remote.agent_source().encode("utf-8")
        request = json.dumps({"op": "ping", "params": {}}).encode("utf-8") + b"\n"
        with tempfile.TemporaryDirectory() as temporary:
            env = {**os.environ, "HOME": temporary, "USERPROFILE": temporary,
                   "MEMUAI_AGENT_HOME": str(Path(temporary) / "agent-state")}
            result = subprocess.run([sys.executable, "-I", "-c", dfy_remote.SshTransport.BOOTSTRAP],
                                    input=str(len(source)).encode("ascii") + b"\n" + source + request,
                                    cwd=temporary, env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        reply = json.loads(result.stdout.decode("utf-8"))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["result"]["version"], dfy_remote.AGENT_VERSION)
        self.assertIn("agent", reply["result"])


if __name__ == "__main__":
    unittest.main()
