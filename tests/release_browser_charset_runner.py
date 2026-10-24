"""Real hidden Edge text encoding and opaque-frame boundary checks."""
import asyncio
import gzip
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import urllib.parse

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
sys.path.insert(0, str(root))
isolated = tempfile.TemporaryDirectory(prefix="memuai-browser-charset-")
os.environ["APPDATA"] = str(Path(isolated.name) / "appdata")
os.environ["LOCALAPPDATA"] = str(Path(isolated.name) / "localappdata")
os.environ["MEMUAI_DATA_DIR"] = str(Path(isolated.name) / "data")
from app.automation_runtime import EdgeAutomation

private_hits = []
class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, *args):
        pass
class Private(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        private_hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"PRIVATE_SENTINEL")
    def log_message(self, *args):
        pass
private = Server(("127.0.0.2", 0), Private)
private_url = f"http://127.0.0.2:{private.server_port}"

expected = {"windows-1251": "Проверка: Привет, мир! Ёж и жёлтый чай.",
            "iso-8859-1": "Encoding: café Noël piñata Ångström £"}
def probe_document(scope):
    return (f"<!doctype html><body>{scope}<script>"
            f"parent.postMessage({json.dumps(scope)},'*');"
            f"try {{ new WebSocket({json.dumps(private_url.replace('http:', 'ws:') + '/ws-' + scope)}).onerror=()=>{{}}; }} catch(e) {{}}"
            f"fetch({json.dumps(private_url + '/fetch-' + scope)}).catch(()=>{{}});"
            "</script></body>")

class Allowed(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        compressed = self.path.startswith("/gzip/")
        encoding = self.path.rsplit("/", 1)[-1]
        if encoding in expected:
            content = ("<!doctype html><title>" + encoding + "</title><body>" + expected[encoding] + "</body>").encode(encoding)
            content_type = "text/html; charset=" + encoding
        elif self.path == "/frames":
            payloads = {scope: probe_document(scope) for scope in ("srcdoc", "about", "data", "blob")}
            payload_json = json.dumps(payloads).replace("<", "\\u003c")
            content = ("<!doctype html><title>Frames</title><body>"
                       "<script>window.executed=[];window.addEventListener('message',e=>executed.push(e.data));"
                       f"const payloads={payload_json};"
                       "for(const [kind,html] of Object.entries(payloads)){const frame=document.createElement('iframe');frame.id=kind;"
                       "if(kind==='srcdoc')frame.srcdoc=html;"
                       "else if(kind==='about'){document.body.append(frame);frame.contentDocument.open();frame.contentDocument.write(html);frame.contentDocument.close();continue;}"
                       "else if(kind==='data')frame.src='data:text/html;charset=utf-8,'+encodeURIComponent(html);"
                       "else frame.src=URL.createObjectURL(new Blob([html],{type:'text/html'}));"
                       "document.body.append(frame);}</script></body>").encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            content, content_type = b"LOCAL", "text/plain"
        if compressed:
            content = gzip.compress(content)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
    def log_message(self, *args):
        pass

allowed = Server(("127.0.0.1", 0), Allowed)
for server in (private, allowed):
    threading.Thread(target=server.serve_forever, daemon=True).start()

async def check():
    edge = EdgeAutomation()
    failures = []
    observed_responses = []
    original = edge.command
    async def observe(method, params=None, **kw):
        result = await original(method, params, **kw)
        if method == "Fetch.getResponseBody":
            observed_responses.append({"base64Encoded": result.get("base64Encoded"), "prefix": repr(result.get("body", "")[:100])})
        return result
    edge.command = observe
    try:
        for prefix in ("", "gzip/"):
            for encoding, text in expected.items():
                await edge.navigate(f"http://127.0.0.1:{allowed.server_port}/{prefix}{encoding}")
                state = await edge.snapshot(screenshot=False)
                if state["text"] != text:
                    failures.append((prefix + encoding, text, state["text"]))
                print(json.dumps({"encoding": prefix + encoding, "expected": text, "actual": state["text"]}, ensure_ascii=True))
        await edge.navigate(f"http://127.0.0.1:{allowed.server_port}/frames")
        await asyncio.sleep(.5)
        executed = await edge.command("Runtime.evaluate", {"expression": "JSON.stringify(executed)", "returnByValue": True})
        scopes = json.loads(executed["result"]["value"])
        assert sorted(scopes) == ["about", "blob", "data", "srcdoc"], scopes
        assert private_hits == [], ("opaque frames crossed boundary", private_hits)
        print("PASS: about:blank, srcdoc, data:, blob: scripts executed; private HTTP and WebSocket requests blocked")
        print(json.dumps({"responses": observed_responses, "executed_scopes": scopes, "private_hits": private_hits}, ensure_ascii=True))
        assert not failures, failures
        print("PASS: windows-1251 and ISO-8859-1 text preserved, raw and gzip")
    finally:
        await edge.close_browser()

try:
    asyncio.run(check())
finally:
    for server in (allowed, private):
        server.shutdown()
        server.server_close()
    isolated.cleanup()
