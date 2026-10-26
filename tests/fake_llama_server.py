"""A stand-in for llama-server used by tests: same command line, tiny HTTP API.

/health answers 503 for a moment (loading) and then 200; /v1/chat/completions
streams the last user message back word by word, prefixed with the alias.
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def arg(name: str, default: str = "") -> str:
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv else default

PORT = int(arg("--port", "0"))
ALIAS = arg("--alias", "model")
STARTED = time.time()
print(f"fake llama-server: model={arg('-m')} ctx={arg('-c')} ngl={arg('-ngl')}", flush=True)
if "--crash" in sys.argv:
    print("error: failed to allocate buffer", flush=True)
    sys.exit(3)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path == "/health":
            ready = time.time() - STARTED > 0.3
            self.send_response(200 if ready else 503)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}' if ready else b'{"status":"loading"}')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        last = next((m["content"] for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
        system = next((m["content"] for m in body.get("messages", []) if m.get("role") == "system"), "")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        words = [f"[{ALIAS}]", f"t={body.get('temperature')}", f"sys={system}"] + str(last).split()
        for word in words:
            chunk = {"choices": [{"delta": {"content": word + " "}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
