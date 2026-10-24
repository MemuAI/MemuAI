"""Managed gateway: verified install, isolation, lifecycle and provider wiring."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from app import opencode_runtime as oc
from app.providers import http
from app.providers.opencode import OpenCodeProvider
from app.providers.opencode_catalog import gateway_base, fingerprint
from app.providers.base import ProviderServerError
from tests.round46_opencode_test import account, seed, Response, sse


def archive_bytes(*, missing=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zip:
        for name, content in (("opencode2api.exe", b"fake executable"), ("LICENSE", b"GPL-3.0"), ("README.md", b"upstream readme")):
            if not missing or name != "LICENSE":
                zip.writestr("package/" + name, content)
        zip.writestr("../../not-extracted", b"bad")
    return stream.getvalue()


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="round47-install-")
        self.runtime = oc.GatewayRuntime(self.tmp.name)
        self.arch = patch.object(oc, "architecture", return_value="amd64"); self.arch.start()
    def tearDown(self):
        self.runtime.shutdown(); self.arch.stop(); self.tmp.cleanup()
    def install(self, data, *, missing=False):
        def transfer(files, directory, state, cancel):
            self.assertTrue(files[0]["url"].startswith(oc.PROJECT + "/releases/download/" + oc.VERSION))
            (directory / files[0]["name"]).write_bytes(data)
            state.update(progress=99, stage="installing", bytes_downloaded=len(data))
        with patch.object(oc, "ASSETS", {"amd64": (len(data), hashlib.sha256(data).hexdigest())}), patch.object(oc.voice_downloads, "transfer", transfer):
            self.runtime.install(); self.runtime._worker.join(5)
            return self.runtime.status()
    def test_installs_only_named_files_and_license_with_manifest(self):
        state = self.install(archive_bytes())
        self.assertTrue(state["installed"]); self.assertEqual(state["stage"], "done")
        self.assertTrue((self.runtime._directory() / "LICENSE").is_file())
        self.assertFalse((Path(self.tmp.name).parent / "not-extracted").exists())
        self.assertNotIn("key", state)
    def test_missing_license_is_not_an_installed_executable(self):
        state = self.install(archive_bytes(missing=True))
        self.assertFalse(state["installed"]); self.assertEqual(state["stage"], "error")
        self.assertEqual(list(Path(self.tmp.name).glob("install-*")), [])
    def test_binary_tampering_requires_reinstallation(self):
        data=archive_bytes()
        def transfer(files, directory, state, cancel): (directory / files[0]["name"]).write_bytes(data)
        with patch.object(oc, "ASSETS", {"amd64": (len(data), hashlib.sha256(data).hexdigest())}), patch.object(oc.voice_downloads, "transfer", transfer):
            self.runtime.install(); self.runtime._worker.join(5)
            exe=self.runtime.binary(); self.assertIsNotNone(exe)
            exe.write_bytes(b"modified")
            self.assertIsNone(self.runtime.binary())
    def test_cancel_does_not_mark_partial_download_installed(self):
        started=threading.Event()
        def transfer(files, directory, state, cancel):
            (directory / files[0]["name"]).write_bytes(b"partial"); started.set(); cancel.wait(3)
            raise asyncio.CancelledError()
        with patch.object(oc.voice_downloads, "transfer", transfer):
            self.runtime.install(); self.assertTrue(started.wait(2))
            self.runtime.cancel_install(); self.runtime._worker.join(3)
        self.assertEqual(self.runtime.status()["stage"], "cancelled")
        self.assertFalse(self.runtime.status()["installed"])
    def test_install_is_idempotent_while_worker_active(self):
        started=threading.Event()
        def transfer(files, directory, state, cancel): started.set(); cancel.wait(3); raise asyncio.CancelledError()
        with patch.object(oc.voice_downloads, "transfer", transfer):
            self.runtime.install(); self.assertTrue(started.wait(2)); worker=self.runtime._worker
            self.runtime.install(); self.assertIs(self.runtime._worker, worker)
            self.runtime.cancel_install(); worker.join(3)
    def test_unsupported_platform_is_clear_and_does_not_download(self):
        with patch.object(oc, "architecture", return_value=""):
            self.assertFalse(self.runtime.status()["supported"])
            with self.assertRaisesRegex(RuntimeError, "Windows"): self.runtime.install()


SERVER = '''import json,sys,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
c=json.load(open(sys.argv[1])); host,port=c['listen'].split(':'); started=time.monotonic()
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self):
  if self.headers.get('Authorization') != 'Bearer '+c['server_keys'][0]: self.send_response(401);self.end_headers();return
  data=json.dumps({'data':[] if time.monotonic()-started < .2 else [{'id':'free-model'}]}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
ThreadingHTTPServer((host,int(port)),H).serve_forever()
'''


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="round47-process-")
        self.runtime=oc.GatewayRuntime(self.tmp.name)
        self.script=Path(self.tmp.name)/"fake.py"; self.script.write_text(SERVER)
        real_popen=subprocess.Popen
        self.children=[]
        def popen(command, **kwargs):
            self.assertEqual(command[1], "-config")
            child=real_popen([sys.executable, str(self.script), command[2]], **kwargs);self.children.append(child);return child
        self.patches=[patch.object(self.runtime,"binary",return_value=Path(sys.executable)),patch.object(oc.subprocess,"Popen",side_effect=popen)]
        for p in self.patches:p.start()
    async def asyncTearDown(self):
        await asyncio.to_thread(self.runtime.shutdown)
        for child in self.children:self.assertIsNotNone(child.poll())
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    async def test_waits_for_nonempty_models_and_reuses_only_own_child(self):
        first=await self.runtime.ensure("one")
        second=await self.runtime.ensure("one")
        self.assertEqual(first,second);self.assertEqual(len(self.children),1)
        config=json.loads(next(Path(self.tmp.name).glob("runs/*/config.json")).read_text())
        self.assertTrue(config["listen"].startswith("127.0.0.1:"));self.assertFalse(config["webui"]["enabled"])
        self.assertEqual(config["zen_keys"],[]);self.assertEqual(config["go_keys"],[]);self.assertTrue(config["models"]["free_only"])
        self.assertNotIn(first["key"],json.dumps(self.runtime.status()))
    async def test_account_and_proxy_isolation_stop_and_restart(self):
        one,two=await asyncio.gather(self.runtime.ensure("one"),self.runtime.ensure("two"))
        self.assertNotEqual(one["key"],two["key"]);self.assertNotEqual(one["base_url"],two["base_url"])
        old=self.runtime._entries["one"]["process"]
        await self.runtime.ensure("one","http://127.0.0.1:19099")
        self.assertIsNotNone(old.poll())
        await asyncio.to_thread(self.runtime.stop,"one");self.assertEqual(self.runtime.status()["running"],1)
    async def test_shutdown_is_reusable_and_cleans_credentials(self):
        await self.runtime.ensure("one");await asyncio.to_thread(self.runtime.shutdown)
        self.assertEqual(list(Path(self.tmp.name).glob("runs/*/config.json")),[])
        await self.runtime.ensure("one");self.assertEqual(self.runtime.status()["running"],1)
    async def test_missing_install_explains_actual_setup_action(self):
        with patch.object(self.runtime,"binary",return_value=None):
            with self.assertRaisesRegex(RuntimeError,"Установить шлюз"):await self.runtime.ensure("missing")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_generation_uses_local_gateway_even_with_old_native_metadata(self):
        acc=seed(account(),model="free-model",dialect="gemini")
        response=Response(lines=sse({"choices":[{"delta":{"content":"OK"}}]},{"choices":[{"delta":{},"finish_reason":"stop"}]}))
        session=type("S",(),{"post":AsyncMock(return_value=response)})()
        with patch.object(oc.runtime,"ensure",AsyncMock(return_value={"base_url":"http://127.0.0.1:18080/v1","key":"local-key"})),patch.object(http,"get_session",AsyncMock(return_value=session)):
            events=[event async for event in OpenCodeProvider().chat_stream(acc,"free-model",[{"role":"user","content":"hi"}],{})]
        call=session.post.call_args
        self.assertEqual(call.args[0],"http://127.0.0.1:18080/v1/chat/completions")
        self.assertEqual(call.kwargs["headers"]["Authorization"],"Bearer local-key")
        self.assertTrue(any(event.get("text")=="OK" for event in events))
        self.assertNotIn("local-key",json.dumps(acc.extra))
    async def test_missing_install_is_friendly_provider_error(self):
        with patch.object(oc.runtime,"ensure",AsyncMock(side_effect=RuntimeError("Установить шлюз"))):
            with self.assertRaisesRegex(ProviderServerError,"Установить шлюз"):
                _ = [event async for event in OpenCodeProvider().chat_stream(seed(account()), "m", [{"role":"user","content":"hi"}], {})]
    async def test_gateway_webui_and_not_json_errors_explain_the_port(self):
        acc=account("gateway",base_url="http://127.0.0.1:8081")
        for response in (Response({},status=404),Response()):
            response.lines=["<html>dashboard</html>"]
            session=type("S",(),{"get":AsyncMock(return_value=response)})()
            with patch.object(http,"get_session",AsyncMock(return_value=session)):
                with self.assertRaisesRegex(ProviderServerError,"API|WebUI"):
                    await OpenCodeProvider()._get_json(acc,"http://127.0.0.1:8081/v1/models")
    def test_gateway_normalizes_common_pasted_urls(self):
        for url in ("http://127.0.0.1:8080","http://127.0.0.1:8080/","http://127.0.0.1:8080/v1/","http://127.0.0.1:8080/v1/models","http://127.0.0.1:8080/v1/chat/completions"):
            self.assertEqual(gateway_base(url),"http://127.0.0.1:8080/v1")
        self.assertEqual(gateway_base("https://host/custom/api"),"https://host/custom/api")
    def test_free_catalog_invalidates_pre_round47_transport(self):
        fields={"mode":"free","api_key":"","base_url":"","api_protocol":""}
        legacy=hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()
        self.assertNotEqual(fingerprint(fields),legacy)


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_requires_explicit_action_auth_and_hides_private_state(self):
        from fastapi import FastAPI
        import httpx
        from app import admin_api
        app=FastAPI();app.include_router(admin_api.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver") as client:
            with patch.object(admin_api,"_auth",return_value=None),patch.object(oc.runtime,"install",return_value={"installing":True}) as install,patch.object(oc.runtime,"status",return_value={"installed":False}):
                self.assertEqual((await client.get("/api/admin/opencode/runtime")).json(),{"installed":False})
                self.assertEqual((await client.post("/api/admin/opencode/runtime",json={"action":"install"})).status_code,400)
                install.assert_not_called()
                self.assertEqual((await client.post("/api/admin/opencode/runtime",json={"action":"install","confirmed":True})).status_code,200)
                install.assert_called_once()
            with patch.object(admin_api,"_auth",return_value=admin_api._err("denied",403)):
                self.assertEqual((await client.post("/api/admin/opencode/runtime",json={"action":"install","confirmed":True})).status_code,403)


if __name__ == "__main__": unittest.main()
