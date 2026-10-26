# -*- coding: utf-8 -*-
"""Round 29 (phase 2): «import by link» for modules — app/character_import.py.

* recognising links and ids exactly like SillyTavern's /importURL + /importUUID;
* SSRF protection (only public HTTPS:443, every DNS answer global, redirects re-checked, size limits,
  Cloudflare pages reported instead of being parsed);
* the host-specific resolvers against canned answers shaped like the real APIs (httpx.MockTransport);
* the ``net-import`` permission, the approval gate and the person's settings through the real routes.

Live check against the real sites (run by hand, needs the internet):
    MEMUAI_LIVE_IMPORT=1 python3 -m pytest -q tests/round29_characters_import_api_test.py -k live
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import character_import as ci
from app import modules_host, pgdata

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

def fake_dns(mapping):
    def resolver(host, port, *args, **kwargs):
        address = mapping.get(host, "93.184.216.34")
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port))]
    return resolver

def run(coro):
    return asyncio.run(coro)

class ParseInputTest(unittest.TestCase):
    def parse(self, text, hosts=ci.DEFAULT_HOSTS):
        out = ci.parse_input(text, hosts)
        return out["source"], out["id"]

    def test_sillytavern_examples(self):
        """The placeholder examples of SillyTavern's «Import from external URL» dialog."""
        self.assertEqual(self.parse("Anonymous/example-character"), ("chub", "Anonymous/example-character"))
        self.assertEqual(self.parse("lorebooks/bartleby/example-lorebook"), ("chub-lorebook", "lorebooks/bartleby/example-lorebook"))
        self.assertEqual(self.parse("ddd1498a-a370-4136-b138-a8cd9461fdfe_character-aqua-the-useless-goddess"),
                         ("janitor", "ddd1498a-a370-4136-b138-a8cd9461fdfe"))
        self.assertEqual(self.parse("a7ca95a1-0c88-4e23-91b3-149db1e78ab9"), ("pygmalion", "a7ca95a1-0c88-4e23-91b3-149db1e78ab9"))
        self.assertEqual(self.parse("AICC/aicharcards/the-game-master"), ("aicc", "aicharcards/the-game-master"))
        self.assertEqual(self.parse("https://realm.risuai.net/character/3ca54c71-6efe-46a2-b9d0-4f62df23d712"),
                         ("risu", "3ca54c71-6efe-46a2-b9d0-4f62df23d712"))
        self.assertEqual(self.parse("https://perchance.org/ai-character-chat?data=Loreena~cb3a1b531477378db7cad0148ba62d71.gz"),
                         ("perchance", "cb3a1b531477378db7cad0148ba62d71.gz"))
        self.assertEqual(self.parse("Loreena~cb3a1b531477378db7cad0148ba62d71.gz"), ("perchance", "cb3a1b531477378db7cad0148ba62d71.gz"))
        self.assertEqual(self.parse("https://files.catbox.moe/abc.png"), ("generic", "https://files.catbox.moe/abc.png"))

    def test_links(self):
        self.assertEqual(self.parse("https://chub.ai/characters/randbro/aqua-and-marina"), ("chub", "randbro/aqua-and-marina"))
        self.assertEqual(self.parse("https://www.characterhub.org/characters/a/b"), ("chub", "a/b"))
        self.assertEqual(self.parse("https://venus.chub.ai/characters/a/b-c_d"), ("chub", "a/b-c_d"))
        self.assertEqual(self.parse("https://chub.ai/lorebooks/BartlebyTheScrivener/example-lorebook"),
                         ("chub-lorebook", "lorebooks/BartlebyTheScrivener/example-lorebook"))
        self.assertEqual(self.parse("https://janitorai.com/characters/ddd1498a-a370-4136-b138-a8cd9461fdfe_character-x"),
                         ("janitor", "ddd1498a-a370-4136-b138-a8cd9461fdfe"))
        self.assertEqual(self.parse("https://jannyai.com/characters/DDD1498A-a370-4136-b138-a8cd9461fdfe"),
                         ("janitor", "ddd1498a-a370-4136-b138-a8cd9461fdfe"))
        self.assertEqual(self.parse("https://pygmalion.chat/character/a7ca95a1-0c88-4e23-91b3-149db1e78ab9"),
                         ("pygmalion", "a7ca95a1-0c88-4e23-91b3-149db1e78ab9"))
        self.assertEqual(self.parse("https://aicharactercards.com/cards/2575"), ("aicc", "cards/2575"))
        self.assertEqual(self.parse("AICC/3323/2575"), ("aicc", "3323/2575"))
        self.assertEqual(self.parse("https://cdn.example.org/c.png", ["*.example.org"]), ("generic", "https://cdn.example.org/c.png"))
        target = ci.parse_input("lorebooks/a/b")
        self.assertEqual(target["kind"], "lorebook")
        self.assertIn("api.chub.ai", target["hosts"])

    def test_refusals(self):
        cases = {
            "": "empty", "https://evil.example/x.png": "host_not_allowed", "http://files.catbox.moe/a.png": "bad_input",
            "https://user:pw@files.catbox.moe/a.png": "bad_input", "ftp://files.catbox.moe/a.png": "bad_input",
            "https://chub.ai/": "bad_input", "https://realm.risuai.net/other/x": "bad_input", "just words": "bad_input",
            "a/b/c/d": "bad_input", "../../etc/passwd": "bad_input", "https://perchance.org/ai-character-chat": "bad_input",
            "https://localhost/a.png": "host_not_allowed", "x" * 3000: "bad_input",
        }
        for text, code in cases.items():
            with self.assertRaises(ci.ImportError_, msg=text) as caught:
                ci.parse_input(text, ci.DEFAULT_HOSTS)
            self.assertEqual(caught.exception.code, code, text)
        self.assertIn("evil.example", ci.ImportError_("x", "a", "b", host="evil.example").payload()["host"])

    def test_hosts(self):
        self.assertEqual(ci.clean_hosts(["Files.Catbox.MOE", "https://raw.githubusercontent.com/x", "*.Example.org", "files.catbox.moe"]),
                         ["files.catbox.moe", "raw.githubusercontent.com", "*.example.org"])
        for bad in ("localhost", "127.0.0.1", "10.0.0.1", "[::1]", "printer.local", "nas.lan", "router", "a..b", "-x.com",
                    "intranet.internal", "x.localhost", "*.", ""):
            self.assertEqual(ci.clean_host(bad), "", bad)
        self.assertTrue(ci.host_allowed("a.b.example.org", ["*.example.org"]))
        self.assertTrue(ci.host_allowed("example.org", ["*.example.org"]))
        self.assertFalse(ci.host_allowed("badexample.org", ["*.example.org"]))
        self.assertFalse(ci.host_allowed("example.org.evil.com", ["example.org"]))

class SafeFetchTest(unittest.TestCase):
    """SSRF guards. The peer check needs a real socket, so it is patched only where MockTransport is used."""

    def test_only_public_https_443(self):
        with patch("socket.getaddrinfo", side_effect=fake_dns({})):
            self.assertEqual(ci.check_public_https("https://files.catbox.moe/a.png"), "https://files.catbox.moe/a.png")
            for url, code in (("http://files.catbox.moe/a.png", "not_https"), ("https://files.catbox.moe:8443/a", "bad_port"),
                              ("https://u:p@files.catbox.moe/", "bad_input"), ("file:///etc/passwd", "not_https"),
                              ("https:///nohost", "not_https")):
                with self.assertRaises(ci.ImportError_, msg=url) as caught:
                    ci.check_public_https(url)
                self.assertEqual(caught.exception.code, code, url)
        for address in ("127.0.0.1", "10.1.2.3", "192.168.1.5", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1",
                        "fe80::1", "fd00::5", "224.0.0.1"):
            with patch("socket.getaddrinfo", side_effect=fake_dns({"rebind.example": address})):
                with self.assertRaises(ci.ImportError_, msg=address) as caught:
                    ci.check_public_https("https://rebind.example/card.png")
                self.assertEqual(caught.exception.code, "private_address", address)

    def fetch(self, handler, url="https://files.catbox.moe/a.png", dns=None, **kwargs):
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await ci.safe_fetch(client, url, **kwargs)
        with patch("socket.getaddrinfo", side_effect=fake_dns(dns or {})), patch.object(ci, "_peer_is_public", lambda response: None):
            return run(go())

    def test_redirects_are_checked_every_hop(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            if request.url.host == "files.catbox.moe":
                return httpx.Response(302, headers={"location": "https://cdn.catbox.example/real.png"})
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        body, mime, final = self.fetch(handler)
        self.assertEqual((body, mime, final), (PNG, "image/png", "https://cdn.catbox.example/real.png"))
        self.assertEqual(len(seen), 2)
        for location, code in (("http://127.0.0.1/admin", "not_https"), ("https://internal.example/x", "private_address"),
                               ("https://files.catbox.moe:22/", "bad_port")):
            def to_private(request, location=location):
                return httpx.Response(302, headers={"location": location})
            with self.assertRaises(ci.ImportError_, msg=location) as caught:
                self.fetch(to_private, dns={"internal.example": "192.168.0.10"})
            self.assertEqual(caught.exception.code, code, location)

        def loop(request):
            return httpx.Response(302, headers={"location": "https://files.catbox.moe/again"})
        with self.assertRaises(ci.ImportError_) as caught:
            self.fetch(loop)
        self.assertEqual(caught.exception.code, "redirects")

    def test_limits_and_errors(self):
        big = lambda request: httpx.Response(200, content=b"x" * 5000)
        with self.assertRaises(ci.ImportError_) as caught:
            self.fetch(big, limit=1000)
        self.assertEqual(caught.exception.code, "too_large")
        declared = lambda request: httpx.Response(200, content=b"x", headers={"content-length": "999999999"})
        with self.assertRaises(ci.ImportError_) as caught:
            self.fetch(declared, limit=1000)
        self.assertEqual(caught.exception.code, "too_large")
        with self.assertRaises(ci.ImportError_) as caught:
            self.fetch(lambda request: httpx.Response(404, text="nope"))
        self.assertEqual(caught.exception.code, "not_found")
        cloudflare = lambda request: httpx.Response(403, text="<!DOCTYPE html><title>Just a moment...</title>",
                                                    headers={"cf-mitigated": "challenge"})
        with self.assertRaises(ci.ImportError_) as caught:
            self.fetch(cloudflare)
        self.assertEqual(caught.exception.code, "blocked")
        self.assertIn("Cloudflare", caught.exception.en)
        with self.assertRaises(ci.ImportError_) as caught:
            self.fetch(lambda request: httpx.Response(500, text="boom"))
        self.assertEqual((caught.exception.code, caught.exception.extra.get("http_status")), ("http", 500))

    def test_the_peer_check_runs_on_a_real_connection(self):
        """Without a real network stream the peer cannot be proven public — the fetch must fail closed."""
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=PNG))) as client:
                return await ci.safe_fetch(client, "https://files.catbox.moe/a.png")
        with patch("socket.getaddrinfo", side_effect=fake_dns({})):
            with self.assertRaises(ci.ImportError_) as caught:
                run(go())
        self.assertEqual(caught.exception.code, "private_address")

class ResolversTest(unittest.TestCase):
    def resolve(self, text, handler, hosts=ci.DEFAULT_HOSTS):
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await ci.fetch_import(ci.parse_input(text, hosts), client)
        with patch("socket.getaddrinfo", side_effect=fake_dns({})), patch.object(ci, "_peer_is_public", lambda response: None):
            return run(go())

    def test_chub_character_like_sillytavern(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            if request.url.host == "api.chub.ai":
                self.assertEqual(request.url.path, "/api/characters/randbro/aqua-and-marina")
                self.assertEqual(request.url.params.get("full"), "true")
                return httpx.Response(200, json={"node": {"id": 5004, "name": "Aqua and Marina", "fullPath": "randbro/aqua-and-marina",
                    "topics": ["Pokemon", "Cute"], "max_res_url": "https://avatars.charhub.io/avatars/randbro/aqua-and-marina/chara_card_v2.png",
                    "definition": {"name": "Aqua and Marina", "personality": "DESC", "tavern_personality": "PERS", "scenario": "SCN",
                                   "first_message": "FIRST", "example_dialogs": "EX", "description": "NOTES", "system_prompt": "SYS",
                                   "post_history_instructions": "PHI", "alternate_greetings": ["ALT"], "extensions": {"x": 1},
                                   "embedded_lorebook": {"entries": [{"keys": ["k"], "content": "c"}]}}}})
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        result = self.resolve("https://chub.ai/characters/randbro/aqua-and-marina", handler)
        card = json.loads(base64.b64decode(result["file"]["base64"]))
        data = card["data"]
        self.assertEqual(card["spec"], "chara_card_v2")
        self.assertEqual((data["description"], data["personality"], data["creator_notes"], data["first_mes"], data["mes_example"]),
                         ("DESC", "PERS", "NOTES", "FIRST", "EX"))
        self.assertEqual((data["tags"], data["creator"], data["alternate_greetings"]), (["Pokemon", "Cute"], "randbro", ["ALT"]))
        self.assertEqual(data["character_book"]["entries"][0]["content"], "c")
        self.assertEqual(data["extensions"]["x"], 1)
        self.assertEqual(base64.b64decode(result["avatar"]["base64"]), PNG)
        self.assertEqual((result["type"], result["source"]), ("character", "chub"))

    def test_chub_character_avatar_failure_is_not_fatal(self):
        def handler(request):
            if request.url.host == "api.chub.ai":
                return httpx.Response(200, json={"node": {"id": 1, "definition": {"name": "N"}, "max_res_url": "https://avatars.charhub.io/x.png"}})
            return httpx.Response(500)
        result = self.resolve("a/b", handler)
        self.assertIsNone(result["avatar"])

    def test_chub_lorebook_two_steps(self):
        book = {"entries": {"0": {"uid": 0, "key": ["a"], "content": "b"}}}

        def handler(request):
            if request.url.path == "/api/lorebooks/bartleby/example-lorebook":
                return httpx.Response(200, json={"node": {"id": 782, "name": "Example Lorebook"}})
            self.assertEqual(request.url.raw_path, b"/api/v4/projects/782/repository/files/raw%252Fsillytavern_raw.json/raw")
            return httpx.Response(200, json=book)
        result = self.resolve("lorebooks/bartleby/example-lorebook", handler)
        self.assertEqual((result["type"], result["name"]), ("lorebook", "Example Lorebook"))
        self.assertEqual(json.loads(base64.b64decode(result["file"]["base64"])), book)

    def test_janitor_blocked_and_ok(self):
        blocked = lambda request: httpx.Response(403, text="<html>Just a moment...</html>", headers={"cf-mitigated": "challenge"})
        with self.assertRaises(ci.ImportError_) as caught:
            self.resolve("ddd1498a-a370-4136-b138-a8cd9461fdfe_character-x", blocked)
        self.assertEqual(caught.exception.code, "blocked")

        def handler(request):
            if request.url.host == "api.jannyai.com":
                self.assertEqual(request.method, "POST")
                self.assertEqual(json.loads(request.content), {"characterId": "ddd1498a-a370-4136-b138-a8cd9461fdfe"})
                return httpx.Response(200, json={"status": "ok", "downloadUrl": "https://image.jannyai.com/x.png"})
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        result = self.resolve("ddd1498a-a370-4136-b138-a8cd9461fdfe_character-x", handler)
        self.assertEqual(result["file"]["mime"], "image/png")

    def test_pygmalion_perchance_risu_aicc_generic(self):
        def pyg(request):
            if request.url.host == "server.pygmalion.chat":
                return httpx.Response(200, json={"character": {"spec": "chara_card_v2", "data": {"name": "Pyg", "avatar": "https://assets.pygmalion.chat/a.png"}}})
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        result = self.resolve("a7ca95a1-0c88-4e23-91b3-149db1e78ab9", pyg)
        self.assertEqual(json.loads(base64.b64decode(result["file"]["base64"]))["data"]["name"], "Pyg")
        self.assertTrue(result["avatar"])

        packed = gzip.compress(json.dumps({"addCharacter": {"name": "Loreena", "roleInstruction": "RI", "reminderMessage": "RM",
                                                             "metaTitle": "MT", "metaDescription": "MD",
                                                             "avatar": {"url": "data:image/png;base64," + base64.b64encode(PNG).decode()}}}).encode())
        result = self.resolve("Loreena~cb3a1b531477378db7cad0148ba62d71.gz", lambda request: httpx.Response(200, content=packed))
        data = json.loads(base64.b64decode(result["file"]["base64"]))["data"]
        self.assertEqual((data["name"], data["description"], data["personality"], data["creator"], data["creator_notes"]),
                         ("Loreena", "RI", "RM", "MT", "MD"))
        self.assertEqual(base64.b64decode(result["avatar"]["base64"]), PNG)
        bomb = gzip.compress(b"{" + b" " * (ci.GZIP_LIMIT + 10) + b"}")
        with self.assertRaises(ci.ImportError_) as caught:
            self.resolve("Loreena~cb3a1b531477378db7cad0148ba62d71.gz", lambda request: httpx.Response(200, content=bomb))
        self.assertEqual(caught.exception.code, "too_large")

        risu = lambda request: httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        self.assertEqual(self.resolve("https://realm.risuai.net/character/3ca54c71-6efe-46a2-b9d0-4f62df23d712", risu)["file"]["mime"], "image/png")
        self.assertEqual(self.resolve("AICC/3323/2575", risu)["file"]["mime"], "image/png")

        page = lambda request: httpx.Response(200, text="<!DOCTYPE html><html>login</html>", headers={"content-type": "text/html"})
        with self.assertRaises(ci.ImportError_) as caught:
            self.resolve("https://files.catbox.moe/a.png", page)
        self.assertEqual(caught.exception.code, "blocked")
        book = lambda request: httpx.Response(200, json={"entries": {}})
        self.assertEqual(self.resolve("https://raw.githubusercontent.com/x/y/main/book.json", book)["type"], "lorebook")
        card = lambda request: httpx.Response(200, json={"spec": "chara_card_v2", "data": {"name": "x"}})
        self.assertEqual(self.resolve("https://raw.githubusercontent.com/x/y/main/c.json", card)["type"], "character")

class RoutesTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        root = Path(self._t.name)
        (root / "data").mkdir()
        self.modules = root / "modules"
        self._p = [patch.object(pgdata, "MEMU_DIR", root / "data"),
                   patch("app.modules_host.default_root", return_value=self.modules)]
        for p in self._p:
            p.start()
        from app.playground_api import create_app
        self.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        self._t.cleanup()

    def post(self, body):
        return self.client.post("/api/pg/modules/net-import", json=body)

    def test_permission_and_approval_gate(self):

        response = self.post({"module_id": "translator", "input": "a/b", "op": "resolve"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "permission")

        response = self.post({"module_id": "characters", "input": "https://chub.ai/characters/a/b", "op": "resolve"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["source"], response.json()["trusted"]), ("chub", False))

        with patch.object(ci, "fetch_import", side_effect=AssertionError("must not download")):
            response = self.post({"module_id": "characters", "input": "a/b"})
        self.assertEqual((response.status_code, response.json()["code"]), (403, "needs_approval"))

        async def fake(target, client=None):
            return {"ok": True, "source": target["source"], "type": "character", "file": {"name": "x.json", "base64": ""}}
        with patch.object(ci, "fetch_import", side_effect=fake):
            self.assertEqual(self.post({"module_id": "characters", "input": "a/b", "approved": True}).status_code, 200)
            self.assertEqual(self.post({"module_id": "characters", "input": "a/b", "approved": "yes"}).status_code, 403)

            settings = self.client.post("/api/pg/modules/net-import/settings", json={"id": "characters", "trust": "chub"}).json()
            self.assertEqual(settings["trusted"], ["chub"])
            self.assertEqual(self.post({"module_id": "characters", "input": "a/b"}).status_code, 200)
            self.assertEqual(self.post({"module_id": "characters", "input": "a7ca95a1-0c88-4e23-91b3-149db1e78ab9"}).json()["code"], "needs_approval")

        response = self.post({"module_id": "characters", "input": "lorebooks/a/b", "kind": "character", "op": "resolve"})
        self.assertEqual(response.json()["code"], "wrong_kind")
        response = self.post({"module_id": "characters", "input": "https://evil.example/x.png", "op": "resolve"})
        self.assertEqual((response.status_code, response.json()["code"], response.json()["host"]), (400, "host_not_allowed", "evil.example"))

    def test_settings_hosts(self):
        url = "/api/pg/modules/net-import/settings"
        state = self.client.post(url, json={"id": "characters"}).json()
        self.assertEqual(state["hosts"], list(ci.DEFAULT_HOSTS))
        self.assertFalse(state["custom_hosts"])
        self.assertIn("chub", state["labels"])
        state = self.client.post(url, json={"id": "characters", "hosts": ["cards.example.org", "127.0.0.1", "localhost", "*.cdn.example"]}).json()
        self.assertEqual(state["hosts"], ["cards.example.org", "*.cdn.example"])
        self.assertTrue(state["custom_hosts"])
        response = self.post({"module_id": "characters", "input": "https://x.cdn.example/c.png", "op": "resolve"})
        self.assertEqual(response.json()["source"], "generic")
        response = self.post({"module_id": "characters", "input": "https://files.catbox.moe/c.png", "op": "resolve"})
        self.assertEqual(response.json()["code"], "host_not_allowed")
        state = self.client.post(url, json={"id": "characters", "reset_hosts": True, "trusted": ["risu", "bogus"]}).json()
        self.assertEqual((state["hosts"], state["trusted"]), (list(ci.DEFAULT_HOSTS), ["risu"]))
        self.assertEqual(self.client.post(url, json={"id": "no-such-module"}).status_code, 404)

    def test_module_without_permission_list_does_not_get_net_import(self):
        manifest = modules_host.normalize_manifest({"id": "old-mod", "title": "O"}, module_id="old-mod")
        self.assertNotIn("net-import", manifest["permissions"])
        self.assertIn("net-import", modules_host.PERMISSIONS)
        self.assertEqual(modules_host.ACTION_PERMISSION["net_import"], "net-import")
        explicit = modules_host.normalize_manifest({"id": "m", "title": "M", "permissions": ["net-import"],
                                                    "net_import": {"sources": ["chub", "nope"], "hosts": ["a.example", "10.0.0.1"]}},
                                                   module_id="m")
        self.assertEqual(explicit["net_import"], {"sources": ["chub"], "hosts": ["a.example"]})

    def test_bridge_and_host_tables(self):
        self.assertIn('fetchImport: function (input, opts) {\n      return call("net_import"', modules_host.BRIDGE_JS)
        js = (Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        self.assertIn('net_import: "net-import"', js)

        start = js.index("async function runNetImport")
        body = js[start:js.index("async function netImportSettings")]
        self.assertNotIn("...payload", body)
        self.assertIn("approved: true", body)
        from app.modules_validate import method_permissions
        self.assertEqual(method_permissions()["fetchImport"], "net-import")

    @unittest.skipUnless(os.environ.get("MEMUAI_LIVE_IMPORT") == "1", "live check: set MEMUAI_LIVE_IMPORT=1")
    def test_live_sites(self):
        """Real sites, real network. JanitorAI is usually behind a Cloudflare challenge for datacenter IPs."""
        cases = {"Anonymous/example-character": "character", "lorebooks/bartleby/example-lorebook": "lorebook",
                 "a7ca95a1-0c88-4e23-91b3-149db1e78ab9": "character", "AICC/3323/2575": "character",
                 "https://realm.risuai.net/character/3ca54c71-6efe-46a2-b9d0-4f62df23d712": "character",
                 "Loreena~cb3a1b531477378db7cad0148ba62d71.gz": "character",
                 "https://raw.githubusercontent.com/SillyTavern/SillyTavern/release/default/content/default_Seraphina.png": "character"}
        for text, kind in cases.items():
            result = run(ci.fetch_import(ci.parse_input(text, ci.DEFAULT_HOSTS)))
            self.assertEqual(result["type"], kind, text)
            self.assertGreater(result["file"]["bytes"], 100, text)
        try:
            run(ci.fetch_import(ci.parse_input("ddd1498a-a370-4136-b138-a8cd9461fdfe_character-aqua-the-useless-goddess")))
        except ci.ImportError_ as exc:
            self.assertIn(exc.code, ("blocked", "not_found", "http"))

if __name__ == "__main__":
    unittest.main()
