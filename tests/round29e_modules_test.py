# -*- coding: utf-8 -*-
"""Round 29 phase 2, wave E (r29e-modules): fixes found by importing and using AI-made modules in the real UI,
plus the leftovers of r29d-quick-modules (panels «Connect», Explorer scroll, Downloads PNG as an image)."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import modules_host, modules_validate as mv, pgdata

ROOT = Path(__file__).resolve().parents[1]


def _report(js: str):
    files = {"manifest.json": json.dumps({"id": "t-mod", "title": "T", "permissions": ["chat", "storage"]}),
             "index.html": "<div></div>", "style.css": "", "main.js": js, "i18n.json": json.dumps({"ru": {}, "en": {}})}
    return mv.validate_package(mv._from_files(files, "t"), check_syntax=False)


class ModuleWithoutModel(unittest.TestCase):
    """«Заметки» от gpt-oss: model = Memu.manifest.bridge.model || "" → раньше 400 «Выберите модель», «Суммировать» молчал."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        root = Path(self._t.name)
        (root / "data").mkdir()
        self.modules = root / "modules"
        self._p = [patch.object(pgdata, "MEMU_DIR", root / "data"),
                   patch("app.modules_host.default_root", return_value=self.modules)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        self._t.cleanup()

    def _complete(self, fallback):
        from app import characters_api
        seen = {}

        async def fake_lines(path, payload):
            seen["body"] = json.loads(payload)
            yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

        local = FastAPI()
        if fallback is not None:
            local.state.module_fallback_model = fallback
        characters_api.register(local, stream_lines=fake_lines)
        r = TestClient(local).post("/api/pg/modules/complete", json={"module_id": "characters", "model": "",
                                                                       "messages": [{"role": "user", "content": "hi"}]})
        return r, seen

    def test_complete_without_model_uses_the_fallback(self):
        async def fallback():
            return "ydx-deepseek"
        r, seen = self._complete(fallback)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(seen["body"]["model"], "ydx-deepseek")

    def test_complete_without_any_model_still_explains(self):
        async def fallback():
            return ""
        r, _ = self._complete(fallback)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Choose a model", r.json()["error"])

    def _fallback(self, models, default):
        from app.playground_api import create_app
        app = create_app()

        async def fake_request(client, method, url, **kwargs):
            if url.endswith("/api/admin/models"):
                return httpx.Response(200, json={"models": models})
            if url.endswith("/api/admin/pg/state"):
                return httpx.Response(200, json={"default_model": default, "image_models": ["img"]})
            return httpx.Response(404, json={})
        with patch.object(httpx.AsyncClient, "request", fake_request):
            return asyncio.run(app.state.module_fallback_model())

    def test_fallback_is_the_default_text_model_else_the_first_text_one(self):
        rows = [{"id": "img", "provider": "x"}, {"id": "b-text", "provider": "b"}, {"id": "a-text", "provider": "a"}]
        self.assertEqual(self._fallback(rows, "b-text"), "b-text")
        # an image model as the default is never used for text; the first text model in the list order is
        self.assertEqual(self._fallback(rows, "img"), "a-text")
        self.assertEqual(self._fallback(rows, ""), "a-text")
        self.assertEqual(self._fallback([{"id": "img", "provider": "x"}], ""), "")

    def test_request_and_stream_routes_use_it(self):
        src = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('model=str(body.get("model") or "").strip() or await module_fallback_model(),'), 2)


class SharedModuleCss(unittest.TestCase):
    def test_mu_split_is_two_panes_and_stacks_when_narrow(self):
        css = modules_host.BASE_CSS
        self.assertIn(".mu-split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);", css)
        narrow = css[css.index("@media (max-width:620px)"):][:200]
        self.assertIn(".mu-split{grid-template-columns:1fr}", narrow.replace(".mu-grid,", ""))


class SpaceKeyRule(unittest.TestCase):
    """«Змейка» от gpt-oss: {Space: "pause"}[e.key] — у пробела e.key = " ", пауза не работала."""

    def hits(self, js):
        return [p for p in _report(js)["problems"] if "space bar" in p.get("en", "")]

    def test_space_compared_with_e_key_warns(self):
        for js in ('if (e.key === "Space") pause();', "const map = {\n  ArrowUp: 1,\n  Space: 'pause'\n};\nonkeydown = (e) => map[e.key];"):
            hits = self.hits(js)
            self.assertEqual([h["level"] for h in hits], ["warn"], js)
        for js in ('if (e.code === "Space") pause();', "if (e.key === ' ') pause();", "const m = {Space: 1};\nconst a = m[e.code];"):
            self.assertFalse(self.hits(js), js)

    def test_pack_mentions_space_and_finally_and_empty_model(self):
        from app import modules_ai_pack as pack
        en = pack.build_pack("en", include_harness=False)
        ru = pack.build_pack("ru", include_harness=False)
        self.assertIn('Space bar: e.key === " "', en)
        self.assertIn("Do not wipe the error message in finally", en)
        self.assertIn("With no model (\"\"), MemuAI answers with its default chat model.", en)
        self.assertIn('Пробел: e.key === " "', ru)
        self.assertIn("Сообщение об ошибке не стирай в finally", ru)


def _js_function(source: str, name: str) -> str:
    start = source.index("function " + name + "(")
    depth, i, opened = 0, source.index("{", start), False
    while True:
        ch = source[i]
        if ch == "{":
            depth += 1
            opened = True
        elif ch == "}":
            depth -= 1
            if opened and depth == 0:
                return source[start:i + 1]
        i += 1


NODE = __import__("shutil").which("node")


class PanelConnectButton(unittest.TestCase):
    """r29d leftover: a «needs_connector:github» wall offered «Retry», which only failed again."""

    def setUp(self):
        self.js = (ROOT / "app" / "pgstatic" / "panels.js").read_text(encoding="utf-8")

    @unittest.skipUnless(NODE, "node is required")
    def test_wall_button_by_kind(self):
        import subprocess
        fn = _js_function(self.js, "wallButton")
        script = ("const t=(r,e)=>e, esc=(x)=>String(x);" + fn + ";console.log(JSON.stringify(["
                  "wallButton({status:'needs_connector:github'}), wallButton({status:'needs_connector:google_calendar'}),"
                  "wallButton({status:'needs_connector:usage'}), wallButton({status:'source_gone'}), wallButton(null)]))")
        out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30).stdout)
        self.assertIn('data-mp-act="connect" data-mp-connector="github">Connect<', out[0])
        self.assertIn('data-mp-connector="google_calendar"', out[1])
        for html in out[2:]:
            self.assertIn('data-mp-act="refresh">Retry<', html)

    def test_both_walls_use_it_and_connect_opens_the_service_page(self):
        self.assertEqual(self.js.count("${wallButton(panel.wall)}"), 2)
        self.assertEqual(self.js.count('<button type="button" data-mp-act="refresh">${t("Повторить", "Retry")}</button>'), 1)   # only in wallButton
        self.assertIn('else if (a === "connect") openIntegrations(actBtn.dataset.mpConnector || "");', self.js)
        body = _js_function(self.js, "openIntegrations")
        self.assertIn('openSettingsPage("apps", sub ? { sub } : {})', body)
        self.assertIn('"int-" + service', body)
        self.assertIn('handoff: { settings: "apps" }', body)       # the Quick Ask canvas / screen layer
        index = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/panels.js?v=1.7-r30", index)


if __name__ == "__main__":
    unittest.main()
