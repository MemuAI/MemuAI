"""Плагины провайдеров MemuAI Proxy: загрузчик, декларативные описания,
пример Kimi2API (парсинг/фрейминг на зафиксированных примерах) и шаблон.

Без сети: Kimi тестируется на зафиксированных ответах с моком HTTP,
декларативный плагин — против локального мок-сервера на 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app.providers import http as hub_http
from app.providers.base import (
    ProviderAuthError,
    ProviderServerError,
)
from app.providers import plugins as plugins_mod
from app.state import Account

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "app" / "providers_user_examples"

GOOD_PLUGIN = '''
PLUGIN_API = 1
from app.providers.base import Field, Provider

class GoodProvider(Provider):
    id = "good-test"
    title = "Good Test"
    icon = "T"
    description = "Test plugin"

    def account_fields(self):
        return [Field("api_key", "API-ключ", "password")]

    def seed_models(self):
        return [("model-a", "Model A")]

    async def check(self, account):
        return True, "OK"

    async def chat_stream(self, account, model, messages, params):
        yield {"type": "text", "text": "hi"}
'''

DECL_YAML = """plugin_api: 1
id: decl-mock
title: "Declarative Mock"
description: "Mock OpenAI-compatible service"
kind: openai-chat
base_url: http://127.0.0.1:{port}/v1
auth: bearer
models: [mock-model]
"""


class TempPluginsTest(unittest.TestCase):
    """Загрузчик с изолированной папкой плагинов (MEMUAI_PROVIDERS_DIR)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"MEMUAI_PROVIDERS_DIR": str(self.dir)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_python_plugin_loads(self):
        self._write("good.py", GOOD_PLUGIN)
        providers, statuses = plugins_mod.load_all()
        self.assertIn("good-test", providers)
        self.assertEqual(len(statuses), 1)
        self.assertTrue(statuses[0].ok)
        self.assertEqual(statuses[0].providers, ["good-test"])
        provider = providers["good-test"]
        self.assertEqual(provider.title, "Good Test")
        self.assertEqual([f.key for f in provider.account_fields()], ["api_key"])
        self.assertEqual(provider.seed_models(), [("model-a", "Model A")])
        events = asyncio.run(self._collect(provider))
        self.assertEqual(events, [{"type": "text", "text": "hi"}])

    async def _collect(self, provider):
        account = Account("good-test", {"api_key": "k"})
        return [event async for event in provider.chat_stream(
            account, "model-a", [{"role": "user", "content": "x"}], {})]

    def test_broken_plugin_does_not_crash_loader(self):
        self._write("broken.py", "PLUGIN_API = 1\nraise RuntimeError('boom at import')\n")
        self._write("good.py", GOOD_PLUGIN)
        providers, statuses = plugins_mod.load_all()
        self.assertIn("good-test", providers)
        by_file = {s.file: s for s in statuses}
        self.assertFalse(by_file["broken.py"].ok)
        self.assertIn("boom at import", by_file["broken.py"].error)

    def test_syntax_error_plugin_is_reported(self):
        self._write("bad.py", "def broken(:\n")
        providers, statuses = plugins_mod.load_all()
        self.assertEqual(providers, {})
        self.assertFalse(statuses[0].ok)
        self.assertIn("bad.py", statuses[0].file)

    def test_plugin_api_version_is_checked(self):
        self._write("noapi.py", GOOD_PLUGIN.replace("PLUGIN_API = 1\n", ""))
        self._write("future.py", GOOD_PLUGIN.replace("PLUGIN_API = 1", "PLUGIN_API = 99"))
        providers, statuses = plugins_mod.load_all()
        self.assertEqual(providers, {})
        by_file = {s.file: s for s in statuses}
        self.assertIn("PLUGIN_API", by_file["noapi.py"].error)
        self.assertIn("99", by_file["future.py"].error)

    def test_reserved_and_duplicate_ids_rejected(self):
        self._write("reserved.py", GOOD_PLUGIN.replace('"good-test"', '"grok"'))
        self._write("dup_a.py", GOOD_PLUGIN)
        self._write("dup_b.py", GOOD_PLUGIN.replace("Good Test", "Good Test 2"))
        providers, statuses = plugins_mod.load_all()
        self.assertEqual(list(providers), ["good-test"])
        by_file = {s.file: s for s in statuses}
        self.assertFalse(by_file["reserved.py"].ok)
        self.assertIn("занят", by_file["reserved.py"].error)
        self.assertFalse(by_file["dup_b.py"].ok)
        self.assertIn("уже занят", by_file["dup_b.py"].error)

    def test_underscore_files_are_skipped(self):
        self._write("_draft.py", GOOD_PLUGIN)
        providers, statuses = plugins_mod.load_all()
        self.assertEqual(providers, {})
        self.assertEqual(statuses, [])

    def test_declarative_yaml_loads(self):
        self._write("decl.yaml", DECL_YAML.format(port=1))
        providers, statuses = plugins_mod.load_all()
        self.assertIn("decl-mock", providers)
        self.assertTrue(statuses[0].ok)
        provider = providers["decl-mock"]
        self.assertEqual([f.key for f in provider.account_fields()], ["api_key"])
        self.assertEqual(provider.seed_models(), [("mock-model", "")])
        self.assertIsNone(provider.validate_fields({"api_key": "k"}))
        self.assertIsNotNone(provider.validate_fields({}))

    def test_declarative_spec_validation(self):
        self._write("bad.yaml", "plugin_api: 1\ntitle: Bad\nid: bad one!\nkind: openai-chat\n"
                                "base_url: http://127.0.0.1:1/v1\nmodels: [m]\n")
        self._write("badurl.yaml", "plugin_api: 1\ntitle: Bad URL\nid: bad-url\nkind: openai-chat\n"
                                   "base_url: not-a-url\nmodels: [m]\n")
        self._write("badkind.yaml", "plugin_api: 1\ntitle: Bad Kind\nid: bad-kind\nkind: anthropic\n"
                                    "base_url: http://127.0.0.1:1/v1\nmodels: [m]\n")
        self._write("notitle.yaml", "plugin_api: 1\nid: no-title\nkind: openai-chat\n"
                                    "base_url: http://127.0.0.1:1/v1\nmodels: [m]\n")
        providers, statuses = plugins_mod.load_all()
        self.assertEqual(providers, {})
        by_file = {s.file: s for s in statuses}
        self.assertFalse(by_file["bad.yaml"].ok)
        self.assertFalse(by_file["badurl.yaml"].ok)
        self.assertIn("chat", by_file["badkind.yaml"].error)
        self.assertIn("title", by_file["notitle.yaml"].error)

    def test_ensure_examples_copies_and_never_overwrites(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            plugins_mod.ensure_examples()
            for name in ("README.md", "kimi2api.py", "web_wrapper_template.py"):
                self.assertTrue((self.dir / name).is_file(), name)
            marker = self.dir / "kimi2api.py"
            marker.write_text("my edits", encoding="utf-8")
            plugins_mod.ensure_examples()
            self.assertEqual(marker.read_text(encoding="utf-8"), "my edits")
        finally:
            env.stop()

    def test_web_wrapper_template_loads_but_refuses_to_work(self):
        source = (EXAMPLES / "web_wrapper_template.py").read_text(encoding="utf-8")
        self._write("web_wrapper_template.py", source)
        providers, statuses = plugins_mod.load_all()
        self.assertIn("web-template", providers)
        self.assertTrue(statuses[0].ok)
        provider = providers["web-template"]
        account = Account("web-template", {"session_id": "abc"})
        ok, message = asyncio.run(provider.check(account))
        self.assertIsNone(ok)
        self.assertIn("не настроен", message)
        with self.assertRaises(ProviderServerError):
            asyncio.run(self._collect(provider))

    def test_registry_picks_up_plugins(self):
        from app.providers import init_registry, meta_list, plugin_ids, plugin_status
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "good.py").write_text(GOOD_PLUGIN, encoding="utf-8")
            with patch.dict(os.environ, {"MEMUAI_PROVIDERS_DIR": tmp}):
                init_registry()
                self.assertIn("good-test", plugin_ids())
                entries = [p for p in meta_list() if p["id"] == "good-test"]
                self.assertEqual(len(entries), 1)
                self.assertTrue(entries[0]["plugin"])
                self.assertEqual(entries[0]["fields"][0]["key"], "api_key")
                self.assertTrue(any(s["file"] == "good.py" and s["ok"]
                                    for s in plugin_status()))
        init_registry()  # env снова указывает на пустую папку setUp: только встроенные


class DeclarativeEndToEndTest(unittest.TestCase):
    """Декларативный плагин: полный путь через Custom-движок к мок-серверу."""

    def test_chat_stream_against_local_mock(self):
        async def handler(reader, writer):
            data = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in data.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            if length:
                await reader.readexactly(length)
            body = ('data: {"choices":[{"delta":{"content":"Привет"}}]}\n\n'
                    "data: [DONE]\n\n").encode("utf-8")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Content-Length: " + str(len(body)).encode()
                         + b"\r\n\r\n" + body)
            await writer.drain()
            writer.close()

        async def run():
            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    Path(tmp, "decl.yaml").write_text(
                        DECL_YAML.format(port=port), encoding="utf-8")
                    with patch.dict(os.environ, {"MEMUAI_PROVIDERS_DIR": tmp}):
                        providers, statuses = plugins_mod.load_all()
                    self.assertTrue(statuses[0].ok, statuses[0].error)
                    provider = providers["decl-mock"]
                    account = Account("decl-mock", {"api_key": "test-key"})
                    events = [event async for event in provider.chat_stream(
                        account, "mock-model",
                        [{"role": "user", "content": "hi"}], {})]
                    self.assertEqual(
                        "".join(e["text"] for e in events if e["type"] == "text"),
                        "Привет")
            finally:
                server.close()
                await hub_http.close_all()

        asyncio.run(run())


def _load_kimi_module():
    """Пример Kimi2API как обычный модуль (путь — из папки примеров)."""
    name = "kimi2api_test_mod"
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / "kimi2api.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
       ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
       ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")


class FakeKimiResponse:
    def __init__(self, status=200, payload=None, chunks=()):
        self.status_code = status
        self.payload = payload
        self.chunks = list(chunks)

    async def aiter_content(self):
        for chunk in self.chunks:
            yield chunk

    def json(self):
        return self.payload


class FakeKimiSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.seen = []

    async def get(self, url, **kwargs):
        self.seen.append(("GET", url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    async def post(self, url, **kwargs):
        self.seen.append(("POST", url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


class FakeKimiHttp:
    def __init__(self, session):
        self.session = session

    async def get_session(self, *args, **kwargs):
        return self.session


class KimiPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kimi = _load_kimi_module()

    def test_extract_jwt_variants(self):
        extract = self.kimi.extract_kimi_jwt
        self.assertEqual(extract(JWT), JWT)
        self.assertEqual(extract(f"kimi-auth={JWT}; other=1"), JWT)
        self.assertEqual(extract(f"Cookie: kimi-auth={JWT}; x=1"), JWT)
        self.assertEqual(extract(f"Authorization: Bearer {JWT}"), JWT)
        self.assertIsNone(extract(""))
        self.assertIsNone(extract("not-a-token"))
        self.assertIsNone(extract("kimi-auth=short"))

    def test_connect_frames_split_and_joined(self):
        pack = self.kimi.pack_connect_frame
        parser = self.kimi.ConnectFrameParser()
        payload = json.dumps({"op": "set", "mask": "block.text"},
                             ensure_ascii=False).encode("utf-8")
        frame = pack(payload)
        self.assertEqual(parser.feed(frame[:3]), [])
        self.assertEqual(parser.feed(frame[3:]), [payload])
        # Два фрейма в одном чанке.
        parser2 = self.kimi.ConnectFrameParser()
        self.assertEqual(parser2.feed(pack(b"{}") + pack(b'{"a":1}')),
                         [b"{}", b'{"a":1}'])

    def test_oversized_frame_rejected(self):
        parser = self.kimi.ConnectFrameParser()
        import struct
        with self.assertRaises(ProviderServerError):
            parser.feed(b"\x00" + struct.pack(">I", 9 * 1024 * 1024))

    def test_translate_event_fixtures(self):
        translate = self.kimi.translate_event
        self.assertEqual(translate({"op": "set", "mask": "block.text",
                                    "block": {"text": {"content": "Привет"}}}),
                         [("text", "Привет")])
        self.assertEqual(translate({"op": "append", "mask": "block.text.content",
                                    "block": {"text": {"content": " мир"}}}),
                         [("text", " мир")])
        self.assertEqual(translate({"op": "append", "mask": "block.think.content",
                                    "block": {"think": {"content": "думаю"}}}),
                         [("reasoning", "думаю")])
        self.assertEqual(translate({"result": {"choices": [{"delta": {
            "content": "x", "reasoning_content": "y"}}]}}),
            [("text", "x"), ("reasoning", "y")])
        self.assertEqual(translate({"op": "heartbeat"}), [])
        self.assertEqual(translate({"message": {"role": "assistant",
                                                "status": "COMPLETED"}}), [])
        with self.assertRaises(ProviderServerError):
            translate({"error": {"code": "invalid_argument", "message": "bad"}})

    def test_is_end_of_stream(self):
        is_end = self.kimi.is_end_of_stream
        self.assertTrue(is_end({"message": {"role": "assistant", "status": "COMPLETED"}}))
        self.assertFalse(is_end({"message": {"role": "assistant", "status": "RUNNING"}}))
        self.assertFalse(is_end({"op": "append", "mask": "block.text.content"}))

    def test_build_chat_payload_marks_thinking(self):
        payload = self.kimi.build_chat_payload(
            "kimi-k2.6-thinking",
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}], {})
        self.assertEqual(payload["model"], "kimi-k2.6-thinking")
        self.assertTrue(payload["options"]["thinking"])
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "sys"})
        plain = self.kimi.build_chat_payload(
            "kimi-k2.6", [{"role": "user", "content": "hi"}], {})
        self.assertFalse(plain["options"]["thinking"])

    def test_validate_fields_rejects_bad_jwt(self):
        provider = self.kimi.Kimi2ApiProvider()
        self.assertIsNone(provider.validate_fields({"kimi_auth": JWT}))
        self.assertIsNotNone(provider.validate_fields({"kimi_auth": "nope"}))
        self.assertEqual(provider.seed_models()[0][0], "kimi-k2.6")

    def _provider_with(self, session):
        # Методы плагина читают hub_http из глобалов СВОЕГО модуля —
        # подменяем там (экземплярный атрибут методы не увидят).
        self.kimi.hub_http = FakeKimiHttp(session)
        return self.kimi.Kimi2ApiProvider()

    def test_check_ok_and_invalid(self):
        account = Account("kimi2api", {"kimi_auth": JWT})
        ok, message = asyncio.run(self._provider_with(
            FakeKimiSession(FakeKimiResponse(200, {"name": "Nisa"}))).check(account))
        self.assertTrue(ok)
        self.assertIn("Nisa", message)
        ok, message = asyncio.run(self._provider_with(
            FakeKimiSession(FakeKimiResponse(401, {}))).check(account))
        self.assertFalse(ok)
        self.assertIn("kimi-auth", message)

    def test_chat_stream_parses_framed_events(self):
        pack = self.kimi.pack_connect_frame
        events = [
            {"op": "set", "mask": "block.text",
             "block": {"text": {"content": "Привет"}}},
            {"op": "append", "mask": "block.think.content",
             "block": {"think": {"content": "думаю"}}},
            {"op": "append", "mask": "block.text.content",
             "block": {"text": {"content": " мир"}}},
            {"message": {"role": "assistant", "status": "COMPLETED"}},
        ]
        chunks = [pack(json.dumps(e, ensure_ascii=False).encode("utf-8"))
                  for e in events]
        # Первый фрейм рвётся пополам между чанками: парсер обязан склеить.
        chunks = [chunks[0][:4], chunks[0][4:], *chunks[1:]]
        session = FakeKimiSession(FakeKimiResponse(200, chunks=chunks))
        provider = self._provider_with(session)
        account = Account("kimi2api", {"kimi_auth": JWT})

        async def collect():
            return [event async for event in provider.chat_stream(
                account, "kimi-k2.6", [{"role": "user", "content": "hi"}], {})]

        got = asyncio.run(collect())
        self.assertEqual(got, [{"type": "text", "text": "Привет"},
                               {"type": "reasoning", "text": "думаю"},
                               {"type": "text", "text": " мир"}])
        method, url, kwargs = session.seen[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, self.kimi.CHAT_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {JWT}")
        self.assertEqual(kwargs["headers"]["Cookie"], f"kimi-auth={JWT}")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/connect+json")

    def test_chat_stream_auth_error(self):
        provider = self._provider_with(
            FakeKimiSession(FakeKimiResponse(401, {})))
        account = Account("kimi2api", {"kimi_auth": JWT})
        with self.assertRaises(ProviderAuthError):
            asyncio.run(self._anext(provider, account))

    def test_chat_stream_empty_is_server_error(self):
        provider = self._provider_with(
            FakeKimiSession(FakeKimiResponse(200, chunks=[])))
        account = Account("kimi2api", {"kimi_auth": JWT})
        with self.assertRaises(ProviderServerError):
            asyncio.run(self._anext(provider, account))

    async def _anext(self, provider, account):
        async for event in provider.chat_stream(
                account, "kimi-k2.6", [{"role": "user", "content": "hi"}], {}):
            return event


if __name__ == "__main__":
    unittest.main()


class AdminPluginsApiTest(unittest.TestCase):
    """Новые админ-эндпоинты: статус плагинов и перезагрузка без рестарта."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "good.py").write_text(GOOD_PLUGIN, encoding="utf-8")
        self.env = patch.dict(os.environ, {"MEMUAI_PROVIDERS_DIR": str(self.dir)})
        self.env.start()
        from app.config import config_store
        self.host_patch = patch.object(config_store, "_runtime_host", "127.0.0.1")
        self.port_patch = patch.object(config_store, "_runtime_port", 8317)
        self.host_patch.start()
        self.port_patch.start()
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8317")

    def tearDown(self) -> None:
        self.client.close()
        self.port_patch.stop()
        self.host_patch.stop()
        self.env.stop()
        self.tmp.cleanup()
        from app.providers import init_registry
        init_registry()

    def test_plugins_status_and_reload(self):
        response = self.client.get("/api/admin/providers/plugins")
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["plugins"]
        self.assertTrue(any(r["file"] == "good.py" and r["ok"] for r in rows))

        response = self.client.get("/api/admin/providers")
        self.assertEqual(response.status_code, 200, response.text)
        entries = [p for p in response.json()["providers"] if p["id"] == "good-test"]
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["plugin"])

        # Новый файл подхватывается перезагрузкой без рестарта процесса.
        (self.dir / "second.yaml").write_text(
            "plugin_api: 1\ntitle: Second\nid: second-decl\nkind: openai-chat\n"
            "base_url: http://127.0.0.1:1/v1\nmodels: [m2]\n", encoding="utf-8")
        response = self.client.post("/api/admin/providers/reload", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        ids = [p["id"] for p in response.json()["providers"]]
        self.assertIn("good-test", ids)
        self.assertIn("second-decl", ids)
        rows = response.json()["plugins"]
        self.assertTrue(any(r["file"] == "second.yaml" and r["ok"] for r in rows))
