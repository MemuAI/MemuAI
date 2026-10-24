"""Black-box Round 47 smoke of frozen apps; no desktop/browser automation.

Runs MemuAI's existing --headless mode with a loopback fake provider and a
fake extension WebSocket client. Auth/accounts stay disabled. All data is
isolated below build; the user's registry protocol handler is not touched.
"""
from __future__ import annotations

import asyncio
import argparse
import base64
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "build/round47-frozen.log"
EXTENSION = "e" * 32
MODEL = "round47-frozen-fake"
EXTENSION_VERSION = "3.8.46"
_log_lock = threading.Lock()
SOURCE = False


def log(text):
    with _log_lock:
        value = str(text)
        if SOURCE:
            value = value.replace("frozen MemuAI:", "source MemuAI:")
        print(value, flush=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(value + "\n")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def app_is_running():
    if os.name != "nt":
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenMutexW(0x00100000, False, "Local\\MemuAI.SingleInstance")
    if handle:
        kernel.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5


def request(base, path, data=None, headers=None, *, expected=200, raw=False):
    payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=payload,
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST" if data is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=35) as response:
            status, content = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, content = exc.code, exc.read()
    require(status == expected, f"HTTP {path}: expected {expected}, got {status}")
    return content if raw else json.loads(content or b"{}")


class FakeProxy(BaseHTTPRequestHandler):
    calls = 0
    def log_message(self, *_args): pass
    def respond(self, data, content_type="application/json"):
        payload = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def do_GET(self):
        if self.path == "/health":
            return self.respond({"service":"memuai-proxy","proxy_enabled":True,"version":"offline-test"})
        model = {"id":MODEL,"provider":"test","model":MODEL,"name":"Offline Frozen Model","caps":{"text":True,"chat":True,"vision":True}}
        return self.respond({"models":[model],"accounts":[],"providers":[],"image_models":[]})
    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        require(size <= 3 * 1024 * 1024, "Fake provider request exceeded 3 MB")
        body = json.loads(self.rfile.read(size) or b"{}")
        if self.path not in ("/api/admin/playground/chat", "/v1/chat/completions"):
            return self.respond({"ok":True})
        type(self).calls += 1
        messages = body.get("messages") or []
        last = str(messages[-1].get("content") or "") if messages else ""
        if "Untrusted tool results" in last:
            text = "Frozen offline answer: the paired extension and shared chat work."
        else:
            text = '```memuai-tool\n[{"tool":"chrome_tabs","args":{}}]\n```'
        event = {"choices":[{"delta":{"content":text}}]}
        self.respond(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode(), "text/event-stream")


async def extension_checks(base, token):
    from websockets.asyncio.client import connect
    headers = {"Origin":"chrome-extension://" + EXTENSION,"X-MemuAI-Extension":EXTENSION,"Authorization":"Bearer " + token}
    counts = {"read":0,"tabs":0}
    async with connect(base.replace("http:","ws:") + "/api/pg/browser-extension/socket",
                       origin=headers["Origin"], proxy=None, open_timeout=10, close_timeout=5) as ws:
        await ws.send(json.dumps({"token":token}))
        require(json.loads(await asyncio.wait_for(ws.recv(), 10)).get("op") == "ready", "Frozen WebSocket auth failed")
        await ws.send(json.dumps({"op":"active_tab","value":{"tab_id":7,"url":"https://example.com/frozen","title":"Frozen Page"}}))
        async def worker():
            async for raw in ws:
                call = json.loads(raw)
                if call.get("op") == "pong": continue
                if not call.get("id"): continue
                op = call.get("op")
                if op == "read":
                    counts["read"] += 1
                    value = {"tab_id":7,"url":"https://example.com/frozen","title":"Frozen Page",
                             "text":"Frozen private page, served by a fake extension. No browser was opened.","elements":[]}
                elif op == "tabs":
                    counts["tabs"] += 1
                    value = {"tabs":[{"tab_id":7,"title":"Frozen Page","url":"https://example.com/frozen"}]}
                else:
                    await ws.send(json.dumps({"id":call["id"],"error":"Unexpected operation in offline test"}))
                    continue
                await ws.send(json.dumps({"id":call["id"],"value":value}))
        worker_task = asyncio.create_task(worker())
        try:
            models = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/models", None, headers)
            profile = models["profile_id"]
            prefs = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/model-preferences",
                {"profile_id":profile,"default_model":MODEL,"pin_model":MODEL,"pinned":True}, headers)
            require(prefs.get("default_model") == MODEL and MODEL in prefs.get("pinned_models", []), "Shared model preferences failed")
            log("PASS shared models: authenticated profile default and pinned-model delta saved")
            result = await asyncio.to_thread(request, base, "/api/pg/tools", {"tool":"chrome_read","args":{"tab_id":7}})
            require("Frozen private page" in result.get("value",{}).get("text",""), "Frozen shared tool did not receive extension RPC result")
            log("PASS frozen MemuAI: authenticated extension WebSocket -> standard chrome_read RPC")
            raw = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/chat",
                {"text":"Read my current page using the available tools","model":MODEL,"page_context":True,"tab_id":7}, headers,
                raw=True)
            events = [json.loads(line[5:].strip()) for line in raw.decode("utf-8").splitlines() if line.startswith("data:")]
            require(not any(e.get("event") == "error" for e in events), "Frozen side-panel stream returned an error")
            done = next((e for e in events if e.get("event") == "done"), None)
            require(done and done.get("id"), "Frozen side-panel stream did not finish")
            stored = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/chats?id=" + done["id"], None, headers)
            messages = (stored.get("chat") or {}).get("messages") or []
            require(messages and "Frozen offline answer" in messages[-1].get("content",""), "Side-panel answer was not persisted")
            require(stored["chat"].get("browser_only") is True, "Default browser conversation leaked to main history")
            require(messages[-1].get("tool_activity"), "Saved answer lost its tool timeline")
            main = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/chats?scope=main", None, headers)
            require(not any(row["id"] == done["id"] for row in main["chats"]), "Private browser conversation appeared in main scope")
            require(counts["read"] >= 2 and counts["tabs"] >= 1, "Frozen side panel did not execute both page and model tools")
            require(FakeProxy.calls >= 2, "Frozen side panel did not resume inference after a tool")
            log("PASS frozen MemuAI: side-panel SSE -> page context -> visible tool loop -> separate browser history")
            profile = (await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/chats", None, headers))["profile_id"]
            await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/settings", {"save_to_main":True}, headers)
            upload = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/attachment",
                {"profile_id":profile,"name":"release-notes.txt","data":"data:text/plain;base64," + base64.b64encode(b"Round 47 integration").decode("ascii")}, headers)
            require(upload.get("id"), "Desktop attachment staging failed")
            raw = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/chat",
                {"text":"Read this file","model":MODEL,"profile_id":profile,"attachments":[upload["id"]]},headers,raw=True)
            events = [json.loads(line[5:].strip()) for line in raw.decode("utf-8").splitlines() if line.startswith("data:")]
            require(not any(e.get("event") == "error" for e in events), "Attachment chat stream failed")
            done = next(e for e in events if e.get("event") == "done")
            saved = await asyncio.to_thread(request, base, "/api/pg/browser-extension/bridge/chats?id="+done["id"],None,headers)
            user = saved["chat"]["messages"][0]
            require(user["attachments"][0].get("text") == "Round 47 integration", "Attachment context/history lost")
            require(saved["chat"].get("browser_only") is False and saved["chat"].get("origin") == "browser_extension", "Opt-in shared history lost origin")
            log("PASS desktop attachment: actual extraction -> staged profile token -> streamed answer -> opt-in shared history")

        finally:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)


def check_memuai(data_root, env, proxy_url):
    if not SOURCE:
        require(not app_is_running(), "MemuAI is already running; refusing to activate or interfere with that instance")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    child_log = ROOT / "build/round47-frozen-child.log"
    arguments = ["--headless","--no-start-proxy","--proxy",proxy_url,"--port",str(port)]
    if SOURCE:
        bootstrap = ("import runpy,sys; import app.desktop as desktop; "
                     "original=desktop.acquire_instance_lock; "
                     "desktop.acquire_instance_lock=lambda name='':original('Round46HeadlessSmoke-" + uuid.uuid4().hex + "'); "
                     "desktop.message_box=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('Unexpected native dialog in headless smoke')); "
                     "sys.argv=['memuai.py',*sys.argv[1:]]; runpy.run_path('memuai.py',run_name='__main__')")
        command = [sys.executable,"-c",bootstrap,*arguments]
    else:
        command = [str(ROOT / "dist/MemuAI/MemuAI.exe"),*arguments]
    runtime_file = data_root / "memuai/memuai.json"
    process = None
    with child_log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 65
            while time.monotonic() < deadline:
                require(process.poll() is None, "Frozen MemuAI exited during startup; see build/round47-frozen-child.log")
                try:
                    if request(base,"/api/pg/ping").get("ok") and runtime_file.exists(): break
                except (OSError, AssertionError): pass
                time.sleep(.15)
            else:
                raise AssertionError("Frozen MemuAI did not publish its headless HTTP server")
            log("PASS frozen MemuAI: headless startup, isolated data, no Proxy/native UI launch")
            telegram = request(base,"/api/pg/integrations/telegram-use")
            require(telegram.get("configured") is False and telegram.get("connected") is False, "Frozen Telegram defaults are incorrect")
            rows = request(base,"/api/pg/integrations")["services"]
            lookup = {row["service"]:row for row in rows}
            require(len(lookup["telegram_use"]["tools"]) >= 13 and len(lookup["chrome_extension"]["tools"]) >= 7, "Frozen integration tool catalogs are missing")
            log("PASS frozen MemuAI: Telegram disabled status and complete connected-service tool catalogs")
            # Pure formatter preview: no Telegram login, send, media or model call.
            preview_path = "/api/pg/integrations/telegram-use/preview"
            preview = request(base, preview_path, {"text":"**Привет**\n\n*Курсив* и `literal \\n`\n> Цитата"})
            require(len(preview["parts"]) == len(preview["plain"]) == 1, "Telegram preview parts mismatch")
            require("<b>Привет</b>" in preview["parts"][0] and "\n\n" in preview["parts"][0],
                    "Telegram preview lost formatting or actual newlines")
            require("literal \\n" in preview["plain"][0] and not preview["truncated"], "Telegram preview altered literal escapes")
            unsafe = request(base, preview_path, {"text":'<img src="https://example.test/pixel"><script>alert(1)</script> [bad](javascript:alert(1))'})
            require(all("<img" not in part and "<script" not in part and 'href="javascript:' not in part for part in unsafe["parts"]),
                    "Telegram preview exposed active raw markup")
            caption = request(base, preview_path, {"text":"🧠" * 700, "kind":"caption"})
            require(caption["truncated"] and len(caption["plain"]) == 1 and len(caption["plain"][0].encode("utf-16-le")) // 2 <= 1024,
                    "Telegram caption preview did not respect the UTF-16 caption limit")
            request(base, preview_path, {"text":""}, expected=400)
            request(base, preview_path, {"text":"Hello"}, {"Origin":"https://example.test"}, expected=403)
            log("PASS Telegram read-only preview: real formatting/newlines, safe markup, caption bound, validation and origin isolation")
            archive = request(base,"/api/pg/browser-extension/download",raw=True)
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                manifest = json.loads(package.read("manifest.json"))
                require(manifest["manifest_version"] == 3, "Frozen extension manifest missing")
                require(manifest["version"] == EXTENSION_VERSION, "Downloaded extension has the wrong release version")
                for name in ("worker.js","page.js","panel.html","panel.js","options.html","options.js"):
                    require(name in package.namelist() and package.getinfo(name).file_size > 0, "Frozen extension asset missing: " + name)
            require(b"MemuTelegramUse" in request(base,"/telegram-use.js",raw=True), "Frozen Telegram settings JS missing")
            require(b"manifest_version" in request(base,"/browser-extension/manifest.json",raw=True), "Frozen Chrome assets not served")
            log("PASS frozen MemuAI: unpacked Chrome ZIP + packaged settings/extension assets")
            code = request(base,"/api/pg/browser-extension/pairing",{})["code"]
            headers = {"Origin":"chrome-extension://" + EXTENSION,"X-MemuAI-Extension":EXTENSION}
            token = request(base,"/api/pg/browser-extension/bridge/pair",{"code":code},headers)["token"]
            require(len(token) >= 32, "Frozen extension pairing failed")
            good_headers = {**headers,"Authorization":"Bearer " + token}
            require(request(base,"/api/pg/browser-extension/bridge/models",headers=good_headers)["models"][0]["id"] == MODEL,
                    "Frozen paired extension cannot list models")
            request(base,"/api/pg/browser-extension/bridge/models",headers={**good_headers,"Authorization":"Bearer bad"},expected=403)
            request(base,"/api/pg/integrations",headers=good_headers,expected=403)
            log("PASS frozen MemuAI: pairing, model bridge, wrong-token rejection and desktop CORS isolation")
            asyncio.run(extension_checks(base,token))
        finally:
            if process.poll() is None and runtime_file.exists():
                try:
                    instance = json.loads(runtime_file.read_text("utf-8"))
                    request(base,"/api/window/shutdown",{}, {"X-MemuAI-Shutdown-Token":instance["shutdown_token"]})
                except Exception: pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
    require(process.returncode == 0, "Frozen MemuAI did not exit cleanly; see child log")
    log("PASS frozen MemuAI: authenticated graceful shutdown; child process exited")


def archive_checks():
    """Read the actual frozen archive/assets without launching the occupied app."""
    from PyInstaller.archive.readers import CArchiveReader
    import importlib.util
    import types
    package = ROOT / "dist/MemuAI/MemuAI.pkg"
    reader = CArchiveReader(str(package if package.is_file() else ROOT / "dist/MemuAI/MemuAI.exe"))
    pyz = reader.open_embedded_archive("PYZ.pyz")
    names = ("app.telegram_use","app.telegram_use_ai","app.telegram_use_api","app.telegram_use_config",
             "app.browser_extension","app.browser_extension_chat","app.browser_extension_files","app.browser_extension_dfy","app.connected_services",
             "app.browser_extension_store","app.browser_extension_connect","app.browser_extension_preferences","app.browser_extension_tools",
             "app.telegram_use_arguments","app.telegram_use_voice","app.telegram_voice","app.telegram_format","app.playground_api",
             "app.voice_downloads","app.stt_providers","app.panels_proxy_usage","app.win_window_theme","app.cli.file_cache",
             "app.providers.opencode","app.providers.opencode_catalog","app.providers.opencode_native","app.opencode_runtime",
             "app.providers.antigravity","app.providers.antigravity_models",
             "app.dfy","app.dfy_api","app.schedule_rules_api","app.flow_engine",
             "app.desktop","app.win_titlebar","app.win_backdrop","app.module_appbar","app.module_apps_win",
             "app.modules_host","app.memufiles.desktop","app.memunote.desktop","app.memunote.winsys",
             "app.api_budget","app.model_refresh","app.pricing","app.chat","app.version",
             "telethon.network.connection.tcpmtproxy","telethon.tl.functions.messages","python_socks.async_.asyncio._proxy",
             "qrcode.image.pil","PIL.ImageOps")
    def signature(code):
        return (code.co_code, tuple(signature(c) if isinstance(c,types.CodeType) else c for c in code.co_consts),
                code.co_names,code.co_varnames,code.co_freevars,code.co_cellvars,code.co_argcount,
                code.co_posonlyargcount,code.co_kwonlyargcount,code.co_flags,code.co_exceptiontable,code.co_linetable)
    for name in names:
        require(name in pyz.toc, "Frozen PYZ module missing: " + name)
        packed = pyz.extract(name)
        if name.startswith("app."):
            source = ROOT / (name.replace(".","/") + ".py")
        else:
            spec = importlib.util.find_spec(name)
            source = Path(spec.origin)
        require(signature(packed) == signature(compile(source.read_bytes(),str(source),"exec",dont_inherit=True)),
                "Frozen PYZ module differs from current source: " + name)
    log(f"PASS frozen archive: {len(names)} new integration/runtime/dependency modules match current source code")
    base = ROOT / "app/pgstatic"
    packaged = ROOT / "dist/MemuAI/_internal/app/pgstatic"
    total = 0
    for source in base.rglob("*"):
        if not source.is_file(): continue
        target = packaged / source.relative_to(base)
        require(target.is_file(), "Frozen static asset missing: " + str(source.relative_to(base)))
        require(hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest(),
                "Frozen static asset differs: " + str(source.relative_to(base)))
        total += 1
    log(f"PASS frozen archive: all {total} packaged desktop/Chrome static files match source SHA-256")
    docs_count = 0
    for product in ("MemuAI", "MemuCode"):
        base = ROOT / "app/docs"
        packaged = ROOT / "dist" / product / "_internal/app/docs"
        for source in base.rglob("*"):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            target = packaged / source.relative_to(base)
            require(target.is_file() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest(),
                    "Frozen documentation differs: " + product + "/" + str(source.relative_to(base)))
            docs_count += 1
    log(f"PASS frozen documentation: {docs_count} packaged files in MemuAI/MemuCode match source SHA-256")


def check_cli(env):
    executable = ROOT / "dist/MemuCode/MemuCode.exe"
    for flag in ("--version","--help"):
        result = subprocess.run([str(executable),flag], cwd=ROOT, env=env, capture_output=True,
                                timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        require(result.returncode == 0 and result.stdout.strip(), "Frozen MemuCode " + flag + " failed")
    log("PASS frozen MemuCode: --version and --help exit 0 without network or interactive terminal")


def main():
    global SOURCE
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",action="store_true",help="test source headless entry with a test-only isolated mutex, then validate frozen archive")
    parser.add_argument("--archive-only",action="store_true",help="only inspect frozen archive and static hashes")
    args=parser.parse_args()
    SOURCE=args.source
    try: sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError: pass
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    build_log = ROOT / "build/round47-build.log"
    deadline = time.monotonic() + 1800
    while not build_log.exists() or "Всё готово" not in build_log.read_text("utf-8",errors="replace")[-12000:]:
        require(time.monotonic() < deadline, "Timed out waiting for completed Round 47 build")
        log("Waiting for build/round47-build.log completion...")
        time.sleep(20)
    if args.archive_only:
        archive_checks()
        return
    if SOURCE:
        log("SKIP frozen MemuAI startup: source mode requested; no existing desktop instance is stopped or activated")
        log("Running source headless entry with an isolated test-only mutex; no product files are changed")
    temp_root = Path(tempfile.mkdtemp(prefix="round47-frozen-data-", dir=ROOT / "build")).resolve()
    require(temp_root.parent == (ROOT / "build").resolve() and temp_root.name.startswith("round47-frozen-data-"), "Unsafe temporary target")
    server = ThreadingHTTPServer(("127.0.0.1",0),FakeProxy)
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True,name="Round46OfflineProxy").start()
    try:
        data_root = temp_root / "data"
        memu_dir = data_root / "memuai"; memu_dir.mkdir(parents=True)
        (memu_dir / "deeplinks.json").write_text(json.dumps({"enabled":False}),encoding="utf-8")
        (memu_dir / "preferences.json").write_text(json.dumps({"language":"en","start_proxy_with_memuai":False,"default_model":MODEL}),encoding="utf-8")
        env = dict(os.environ, MEMUAI_DATA_DIR=str(data_root),MEMUAI_WORK_DIR=str(temp_root / "work"),
                   NO_PROXY="127.0.0.1,localhost",PYTHONUTF8="1")
        for key in ("MEMUAI_TOKEN","MEMUAI_PROXY_URL","MEMUAI_PROGRAM_DIR"):
            env.pop(key,None)
        check_cli(env)
        check_memuai(data_root,env,f"http://127.0.0.1:{server.server_port}")
        if SOURCE:
            archive_checks()
            log("PASS Round 47 source headless + frozen archive/CLI checks; frozen MemuAI startup remains explicitly untested")
        else:
            log("PASS Round 47 frozen checks completed; no real accounts, providers, browser or desktop control used")
    finally:
        server.shutdown(); server.server_close()
        # The only recursive deletion target is the verified task directory created above.
        require(temp_root.parent == (ROOT / "build").resolve() and temp_root.name.startswith("round47-frozen-data-"), "Unsafe cleanup target")
        shutil.rmtree(temp_root)


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        log("FAIL frozen check: " + type(exc).__name__ + ": " + str(exc))
        raise
