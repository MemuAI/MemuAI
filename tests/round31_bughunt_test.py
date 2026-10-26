"""Round 31 bughunt: regression tests for the backend fixes.

* custom key pool: HTTP 429 on one key falls over to another key of the same pool
  (real local mock server, both pool modes, every key throttled → a 429 error);
* web search: ``_on_topic`` relevance gate and the spare source started at once when a
  racer answers off topic (sources mocked, no network);
* Work «Applied changes»: ``diffHtml`` joins block spans without "\n" (node, static fallback);
* RU/EN wording of the routing / rejected-account errors.
"""
import asyncio
import json
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port

class _MockServer:
    """Answers /v1/chat/completions: 429 for throttled keys, an SSE stream otherwise."""

    def __init__(self, throttled):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.throttled = set(throttled)
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                key = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                outer.calls.append(key)
                if key in outer.throttled:
                    body = json.dumps({"error": {"message": "Rate limit exceeded: free-models-per-day",
                                                 "code": 429}}).encode()
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                chunk = {"id": "x", "object": "chat.completion.chunk", "model": "m",
                         "choices": [{"index": 0, "delta": {"content": f"ok:{key}"}, "finish_reason": None}]}
                done = {"id": "x", "object": "chat.completion.chunk", "model": "m",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                payload = (f"data: {json.dumps(chunk)}\n\n" f"data: {json.dumps(done)}\n\n"
                           "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.port = _free_port()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

def _pool_account(port, keys, mode="random"):
    from app.state import Account
    return Account("custom", {"name": "pool", "base_url": f"http://127.0.0.1:{port}/v1",
                              "api_keys": "\n".join(keys), "key_pool_mode": mode,
                              "key_error_keywords": "balance is too low"})

async def _collect(provider, account):
    text = ""
    async for event in provider.chat_stream(account, "m", [{"role": "user", "content": "hi"}], {}):
        if isinstance(event, dict):
            text += str(event.get("text") or event.get("content") or event.get("delta") or "")
            if not text and event.get("choices"):
                text += str(((event["choices"][0] or {}).get("delta") or {}).get("content") or "")
    return text

@pytest.mark.parametrize("mode", ["random", "failover-on-match"])
def test_pool_429_rotates_to_idle_key(mode, monkeypatch):
    from app.providers.custom import CustomProvider
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    CustomProvider._rate_limited.clear()
    with _MockServer({"sk-busy"}) as server:
        account = _pool_account(server.port, ["sk-busy", "sk-idle"], mode)
        provider = CustomProvider()

        with patch("app.providers.custom.random.shuffle", lambda seq: seq.sort()), \
                patch("app.state.state.save_accounts"):
            text = asyncio.run(_collect(provider, account))
        assert "sk-idle" in text or server.calls[-1] == "sk-idle"
        assert server.calls[0] == "sk-busy" and "sk-idle" in server.calls

        assert not account.extra.get("disabled_api_key_hashes")

        server.calls.clear()
        with patch("app.providers.custom.random.shuffle", lambda seq: seq.sort()), \
                patch("app.state.state.save_accounts"):
            asyncio.run(_collect(provider, account))
        assert server.calls == ["sk-idle"]
    CustomProvider._rate_limited.clear()

def test_pool_all_keys_429_raises_rate_limit(monkeypatch):
    from app.providers.custom import CustomProvider
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    CustomProvider._rate_limited.clear()
    with _MockServer({"sk-a", "sk-b", "sk-c"}) as server:
        account = _pool_account(server.port, ["sk-a", "sk-b", "sk-c"])
        with patch("app.state.state.save_accounts"):
            with pytest.raises(Exception) as caught:
                asyncio.run(_collect(CustomProvider(), account))

        assert sorted(server.calls) == ["sk-a", "sk-b", "sk-c"]
        assert "429" in str(caught.value) or "limit" in str(caught.value).lower() \
            or "RateLimit" in type(caught.value).__name__

def test_single_key_429_is_not_retried(monkeypatch):
    from app.providers.custom import CustomProvider
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    CustomProvider._rate_limited.clear()
    with _MockServer({"sk-only"}) as server:
        account = _pool_account(server.port, ["sk-only"])
        with pytest.raises(Exception):
            asyncio.run(_collect(CustomProvider(), account))
        assert server.calls == ["sk-only"]

def test_on_topic_gate():
    from app.websearch import _on_topic
    query = "fastapi streaming response example"
    filler = [{"title": "WhatsApp Web", "snippet": "Message privately", "url": "https://web.whatsapp.com"},
              {"title": "CVE-2024-1234", "snippet": "Vulnerability in a router", "url": "https://cve.org"}]
    good = filler + [{"title": "StreamingResponse - FastAPI", "snippet": "Use StreamingResponse to stream",
                      "url": "https://fastapi.tiangolo.com/advanced/custom-response/"}]
    assert not _on_topic(query, filler)
    assert _on_topic(query, good)
    assert _on_topic("", filler) and _on_topic(query, [])
    assert _on_topic("питон", [{"title": "Питон — язык", "snippet": "", "url": ""}])

def test_free_search_skips_off_topic_racer_and_starts_spare():
    from app import websearch, search_engines
    query = "fastapi streaming response example"
    started = []

    def source(name, rows, delay):
        async def run(q, limit, proxy, snippet_chars=500):
            started.append(name)
            await asyncio.sleep(delay)
            return rows
        return run

    off = [{"title": "WhatsApp", "snippet": "chat", "url": "https://whatsapp.com"}]
    good = [{"title": "FastAPI StreamingResponse example", "snippet": "streaming", "url": "https://x.dev"}]
    fake = {"bing": source("bing", off, 0.01), "duckduckgo": source("duckduckgo", [], 3.0),
            "yahoo": source("yahoo", good, 0.05), "wikipedia": source("wikipedia", [], 0)}
    with patch.dict(websearch._FREE, fake, clear=True), patch.object(websearch, "_cold", {}), \
            patch.object(search_engines, "FREE_RACE", ("bing", "duckduckgo")), \
            patch.object(search_engines, "FREE_CHAIN", ("bing", "duckduckgo", "yahoo")):
        t0 = time.monotonic()
        rows = asyncio.run(websearch.free_search(query, 5))
        took = time.monotonic() - t0
    assert rows == good
    assert "yahoo" in started
    assert took < 2.0, f"the spare must start right away, not after the slow racer ({took:.2f}s)"

def test_free_search_off_topic_is_last_resort():
    from app import websearch, search_engines
    off = [{"title": "WhatsApp", "snippet": "chat", "url": "https://whatsapp.com"}]

    async def offsrc(q, limit, proxy, snippet_chars=500):
        return off

    async def empty(q, limit, proxy, snippet_chars=500):
        return []

    fake = {"bing": offsrc, "duckduckgo": empty, "yahoo": empty, "wikipedia": empty}
    with patch.dict(websearch._FREE, fake, clear=True), patch.object(websearch, "_cold", {}), \
            patch.object(search_engines, "FREE_RACE", ("bing", "duckduckgo")), \
            patch.object(search_engines, "FREE_CHAIN", ("bing", "duckduckgo", "yahoo")):
        rows = asyncio.run(websearch.free_search("fastapi streaming response example", 5))
    assert rows == off

def _diff_fn_source() -> str:
    src = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
    start = src.index("function diffHtml(text)")
    end = src.index("\n}\n", start) + 3
    return src[start:end]

def test_diffhtml_has_no_newlines_between_block_spans():
    fn = _diff_fn_source()
    assert '.join("")' in fn and '.join("\\n")' not in fn
    if not shutil.which("node"):
        pytest.skip("node not installed (static check done)")
    script = ("const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');\n" + fn +
              "\nprocess.stdout.write(diffHtml('--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-old\\n+new\\n\\n ctx'));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30).stdout
    assert "\n" not in out
    assert out.count("<span") == 7
    assert '<span class="d-add">+new</span>' in out and '<span class="d-del">-old</span>' in out
    assert '<span class=""> </span>' in out

def test_routing_messages_wording():
    src = (ROOT / "app/chat.py").read_text(encoding="utf-8")
    assert "нет такого префикса и нет однозначного совпадения" in src
    assert "и неоднозначного совпадения" not in src
    assert "Проверьте ключ или вход на вкладке «Аккаунты» MemuAI Proxy." in src

def test_rejected_account_message_en_has_no_cyrillic():
    """A custom account rejected with a Russian reason: RU text names it, EN text stays English."""
    from types import SimpleNamespace
    from app import chat as chat_mod
    from app.chat import ChatService, CompletionError
    from app.state import Account

    account = Account("custom", {"name": "xk", "base_url": "http://127.0.0.1:9/v1", "api_key": "sk-x"})
    account.status_message = "ключ отклонён (HTTP 401)"
    resolved = SimpleNamespace(provider="custom", model="some-model", prefix="xk", entry=None)
    settings = SimpleNamespace(proxy_enabled=True, strict_models=False, search_results=5, default_proxy="")
    svc = ChatService.__new__(ChatService)

    async def run():
        async for _ in svc.stream_completion("xk/some-model", [{"role": "user", "content": "hi"}], {}):
            pass

    with patch.object(chat_mod, "resolve", lambda model: resolved), \
            patch.object(ChatService, "_provider", lambda self, name: SimpleNamespace(title="Custom")), \
            patch.object(chat_mod.config_store, "settings", settings), \
            patch.object(chat_mod.config_store, "provider_cfg", lambda name: {}), \
            patch.object(chat_mod.pool, "ordered_candidates", lambda *a, **k: []), \
            patch.object(chat_mod.pool, "model_blocked", lambda *a, **k: False), \
            patch.object(chat_mod.state, "usable_accounts", lambda provider: []), \
            patch.object(chat_mod.state, "accounts_of", lambda provider: [account]), \
            patch.object(Account, "usable", lambda self: False):
        with pytest.raises(CompletionError) as caught:
            asyncio.run(run())
    ru, en = str(caught.value), caught.value.message_en
    assert "отклонён" in ru and "«Аккаунты»" in ru, ru
    assert not re.search(r"[А-Яа-яЁё]", en), en
    assert "the key was rejected" in en and "Accounts tab" in en, en

def test_thought_duration_unit_follows_ui_language():
    src = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
    start = src.index("function formatThoughtDuration(ms)")
    fn = src[start:src.index("\n}\n", start) + 3]
    assert '" s"' in fn and '" с"' in fn
    if not shutil.which("node"):
        pytest.skip("node not installed (static check done)")
    for lang, expect in (("ru", ["3.7 с", "42 с"]), ("en", ["3.7 s", "42 s"])):
        script = f"let PG_LANG={json.dumps(lang)};\n{fn}\nprocess.stdout.write(formatThoughtDuration(3700)+'|'+formatThoughtDuration(42000));"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30).stdout
        assert out.split("|") == expect, out

def test_narrow_topbar_keeps_model_picker():
    """≤520 px: the panels strip (flex 100) squeezed the model picker down to a bare «▾»."""
    css = (ROOT / "app/pgstatic/panels.css").read_text(encoding="utf-8")
    assert re.search(r"@media \(max-width: 520px\) \{ \.topbar\.appbar > \.panels-appbar \{ flex: 0 1 auto; \}"
                     r" \.topbar\.appbar #model-dd \{ min-width: 72px; \} \}", css)
