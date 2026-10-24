"""Pinned gateway's background-probe regression against a loopback fake upstream.

No user accounts, desktop windows or external generations. Public metadata
GETs made by the gateway are allowed; every inference upstream is loopback.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.opencode_runtime import _manual_availability  # noqa: E402


def main():
    exe = ROOT / "build/round47-research/opencode2api.exe"
    assert exe.is_file(), "Run the verified Round 47 gateway research installation first"
    archive_manifest = ROOT / "build/round47-research/jason-release.json"
    assert archive_manifest.is_file()
    # Same binary inspected and installed in Round 47; require its identity.
    binary_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    assert binary_sha == "6df44c40b2c60f704a7c6663d96ba2a1651f41498d7992b88a9fadc8aaa2d36c"
    counters = {"automatic": {"get": 0, "post": 0}, "manual": {"get": 0, "post": 0}}
    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            lane = self.path.split("/")[1]
            if lane in counters: counters[lane]["get"] += 1
            self.reply({"data": [{"id": "big-pickle"}]})
        def do_POST(self):
            lane = self.path.split("/")[1]
            if lane in counters: counters[lane]["post"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.reply({"choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    children = []; logs = []
    try:
        with tempfile.TemporaryDirectory(prefix="round48-fake-upstream-", dir=ROOT / "build") as work:
            for mode in counters:
                directory = Path(work) / mode; directory.mkdir()
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
                upstream = f"http://127.0.0.1:{server.server_port}/{mode}/zen"
                config = {"listen": f"127.0.0.1:{port}", "anonymous": True, "server_keys": [secrets.token_hex(24)],
                          "zen_keys": [], "go_keys": [], "proxies": ["direct"],
                          "upstream": {"zen": upstream, "go": upstream + "/go", "anonymous": upstream},
                          "webui": {"enabled": False}, "logging": {"level": "error", "dump_request_bodies": False},
                          "models": {"free_only": True, "refresh_seconds": 300},
                          "retry": {"max_attempts": 1, "timeout_seconds": 10}}
                path = directory / "config.json"; path.write_text(json.dumps(config), "utf-8")
                if mode == "manual": _manual_availability(path, ("big-pickle",), upstream)
                log = (directory / "process.log").open("wb"); logs.append(log)
                children.append(subprocess.Popen([str(exe), "-config", str(path)], cwd=directory,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
            # Covers the initial check and the first 60-second scheduler tick.
            for _ in range(66):
                assert all(child.poll() is None for child in children), "Owned test gateway exited"
                time.sleep(1)
            result = {"gateway": "v1.4.3", "binary_sha256": binary_sha, "duration_seconds": 66,
                      "counters": counters, "external_generations": 0}
            assert counters["automatic"]["post"] > 0, counters
            assert counters["manual"]["get"] > 0 and counters["manual"]["post"] == 0, counters
            (ROOT / "build/round48-gateway-probe-check.json").write_text(json.dumps(result, indent=2), "utf-8")
            print("PASS pinned gateway: automatic background POSTs reproduced; manual registry suppresses them", flush=True)
            print(json.dumps(result), flush=True)
            for child in children:
                child.terminate(); child.wait(timeout=8)
            children.clear()
            for log in logs: log.close()
            logs.clear()
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=8)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        for log in logs: log.close()
        server.shutdown(); server.server_close()


if __name__ == "__main__": main()
