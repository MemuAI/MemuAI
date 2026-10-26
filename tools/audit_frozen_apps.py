"""Smoke-test frozen MemuFiles/MemuNote over loopback, without opening a window.

Uses only temporary files and a fake model provider. Does not interact with
the desktop, the real Proxy, accounts, or a user's application data.
"""
from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]

class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = {"service": "memuai-proxy"} if self.path == "/health" else {"models": [
            {"id": "audit-chat", "name": "Audit Chat", "caps": {"text": True}},
            {"id": "audit-song", "caps": {"music": True, "text": False}}]}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body.get("messages") or []
        last = messages[-1]["content"]
        if 'tool_result name="propose_plan"' in last:
            text = "Review the text change before confirming."
        elif 'tool_result name="read_file"' in last:
            text = '<tool>{"name":"propose_plan","args":{"actions":[{"op":"edit","path":"note.txt","old_text":"old text","new_text":"new text"}]}}</tool>'
        else:
            text = '<tool>{"name":"read_file","args":{"path":"note.txt"}}</tool>'
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        item = {"choices": [{"delta": {"content": text}}]}
        self.wfile.write(("data: " + json.dumps(item) + "\n\ndata: [DONE]\n\n").encode())

def call(instance, prefix, path, body=None, raw=False):
    conn = http.client.HTTPConnection("127.0.0.1", instance["port"], timeout=20)
    try:
        headers = {prefix: instance["token"], "Content-Type": "application/json"}
        conn.request("POST" if body is not None else "GET", "/api/" + path,
                     json.dumps(body).encode() if body is not None else None, headers)
        response = conn.getresponse()
        data = response.read()
        assert response.status == 200, (path, response.status, data[:400])
        return data if raw else json.loads(data)
    finally:
        conn.close()

def check(name, service, header, proxy_url):
    with tempfile.TemporaryDirectory(prefix="memu-frozen-audit-") as tmp:
        root = Path(tmp)
        scope = root / "scope"
        scope.mkdir()
        path = scope / "note.txt"
        path.write_bytes(b"old text\r\n")
        data = root / "data"
        folder = data / service
        folder.mkdir(parents=True)
        (folder / "settings.json").write_text(json.dumps({"proxy_url": proxy_url, "ai_model": "audit-chat",
                                                         "ai_enabled": True, "language": "en", "keep_running": False}), encoding="utf-8")
        env = dict(os.environ, MEMUAI_DATA_DIR=str(data), MEMUFILES_TRASH_DIR=str(root / "trash"),
                   NO_PROXY="127.0.0.1,localhost")
        env.pop("MEMUAI_TOKEN", None)
        env.pop("MEMUAI_PROXY_URL", None)
        process = subprocess.Popen([str(ROOT / "dist" / name / (name + ".exe")), "--no-window", "--new-instance", str(scope)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        instance = None
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                assert process.poll() is None, f"{name} exited before publishing its server"
                try:
                    instance = json.loads((folder / "instance.json").read_text())
                    if instance.get("port"):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            assert instance and instance.get("port"), f"{name} did not start"
            status = call(instance, header, "ai/status")
            assert status["online"], status
            assert [m["id"] for m in status["models"]] == ["audit-chat"], status
            if service == "memufiles":
                raw = call(instance, header, "ai/chat", {"scope": str(scope), "model": "audit-chat",
                           "messages": [{"role": "user", "content": "Replace old text with new text"}]}, raw=True)
                events = [json.loads(line[6:]) for line in raw.decode().splitlines() if line.startswith("data: ")]
                assert not any(e["t"] == "error" for e in events), events
                plan = next(e["plan"] for e in events if e["t"] == "plan")
                assert path.read_bytes() == b"old text\r\n"
                result = call(instance, header, "ai/execute", {"plan_id": plan["id"], "selected": [0]})
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    ops = call(instance, header, "ops")["ops"]
                    if any(o["id"] in result["ops"] and o["status"] in ("done", "error") for o in ops):
                        break
                    time.sleep(0.03)
                assert path.read_bytes() == b"new text\r\n", ops
                print("PASS: frozen MemuFiles — startup, model filtering, read → review → edit, CRLF preservation")
            else:
                opened = call(instance, header, "open", {"path": str(path)})
                assert opened.get("encoding") == "utf-8", opened
                print("PASS: frozen MemuNote — startup, model filtering, opening a text file")
        finally:
            if instance and process.poll() is None:
                try:
                    call(instance, header, "remote", {"action": "shutdown"})
                except (OSError, AssertionError):
                    pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        check("MemuFiles", "memufiles", "X-MF-Token", url)
        check("MemuNote", "memunote", "X-MN-Token", url)
    finally:
        server.shutdown()
        server.server_close()

if __name__ == "__main__":
    main()
