"""Round 33 (r33-models / r33c-models-vision): model names, the one catalogue, vision, image generation endpoints.

* name vectors agree in Python (app/model_names.py) and Node (pgstatic/memu-model-names.js); both JS copies identical;
* the catalogue (app/model_catalog.py): vision answer order, enrich(), pick_rows(), label();
* GET /api/pg/model-catalog and POST /api/pg/model-catalog/vision (app/model_catalog_api.py) with a fake Proxy;
* image model detection (wanx / t2i / imagine) and the chat ↔ /images/generations fallback (app/media_gen.py);
* vision-gate.js (window.MemuVisionGate): suggestions and «a model that sees images gets the picture at once»;
* the shipped module lists (MemuAI.spec, MemuAIProxy.spec, build.py) and the script tag.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r33-models-"))

from app import model_names, model_catalog, model_catalog_api, media_gen  # noqa: E402
from app.providers.base import ProviderError, ProviderServerError  # noqa: E402

VECTORS = json.loads((ROOT / "tests" / "round33_model_names_vectors.json").read_text(encoding="utf-8"))
NODE = shutil.which("node")


class NameVectorsTest(unittest.TestCase):
    def test_python_vectors(self):
        self.assertGreater(len(VECTORS["cases"]), 50)
        for case in VECTORS["cases"]:
            got = model_names.parse(case["id"], prefix=case.get("prefix", ""))
            for key in ("name", "base", "badges", "date"):
                self.assertEqual(got[key], case[key], f"{case['id']} {key}")

    def test_js_copies_byte_identical(self):
        a = (ROOT / "app" / "pgstatic" / "memu-model-names.js").read_bytes()
        b = (ROOT / "app" / "static" / "model-names.js").read_bytes()
        self.assertEqual(a, b)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_node_vectors(self):
        script = """
const api = require(process.argv[1]);
const data = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const bad = [];
for (const c of data.cases) {
  const g = api.parse(c.id, { prefix: c.prefix || '' });
  for (const k of ['name', 'base', 'date']) if (g[k] !== c[k]) bad.push(c.id + ' ' + k + ' ' + g[k]);
  if (JSON.stringify(g.badges) !== JSON.stringify(c.badges)) bad.push(c.id + ' badges');
}
if (api.pretty('a-b', { aliases: { 'a-b': ' Mine ' } }) !== 'Mine') bad.push('alias');
console.log(JSON.stringify(bad));
"""
        out = subprocess.run([NODE, "-e", script, str(ROOT / "app" / "pgstatic" / "memu-model-names.js"),
                              str(ROOT / "tests" / "round33_model_names_vectors.json")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout.strip()), [])

    def test_examples_from_the_user(self):
        self.assertEqual(model_names.pretty("prefix-claude-opus-4.6-thinking"), "Claude Opus 4.6 Thinking")
        self.assertEqual(model_names.pretty("my-gpt-9.2-mini", prefix="my"), "GPT-9.2 Mini")   # a future model
        self.assertEqual(model_names.pretty("x", aliases={"x": "Own name"}), "Own name")
        self.assertIn(" · reform-glm-5.3", model_names.label("reform-glm-5.3", prefix="reform"))


class VisionStateTest(unittest.TestCase):
    """One answer per model: probe → user → local → adapter → provider → catalogue → family → unknown."""

    def _run(self, *, probe=None, override=None, provider="custom", summary=None, hint=None):
        from app import model_capabilities as mc, model_info
        with mock.patch.object(mc, "probe_result", return_value=probe), \
                mock.patch.object(mc, "get_override", return_value=override or {}), \
                mock.patch.object(mc, "capabilities_for", return_value=summary or {}), \
                mock.patch.object(mc, "IMAGE_ADAPTERS", {"custom", "kiro"}), \
                mock.patch.object(model_info, "vision_hint", return_value=hint):
            return model_catalog.vision_state("reform-qwen3.8-flash", provider)

    def test_order(self):
        self.assertEqual(self._run(probe={"vision": False}, override={"vision": True}), {"state": "no", "src": "probe"})
        self.assertEqual(self._run(override={"vision": True}, hint=False), {"state": "yes", "src": "user"})
        self.assertEqual(self._run(provider="grok"), {"state": "no", "src": "adapter"})
        self.assertEqual(self._run(summary={"effective": {"vision": True}}, hint=False), {"state": "yes", "src": "provider"})
        self.assertEqual(self._run(hint=True), {"state": "yes", "src": "family"})
        self.assertEqual(self._run(summary={"vision_ref": True}), {"state": "yes", "src": "catalog"})
        self.assertEqual(self._run(hint=False), {"state": "no", "src": "family"})
        self.assertEqual(self._run(), {"state": "unknown", "src": "none"})


class CatalogTest(unittest.TestCase):
    def payload(self):
        return {"models": [
            {"id": "reform-qwen3.8-flash", "provider": "custom", "provider_label": "Reform", "caps": {"text": True}},
            {"id": "reform-wanx2.1-t2i-turbo", "provider": "custom", "provider_label": "Reform", "caps": {}},
            {"id": "xkiro-qwen/qwen3-vl-plus:free", "provider": "custom", "provider_label": "XKiro", "caps": {}},
            {"id": "reform-sora-2", "provider": "custom", "caps": {"video": True}},
        ], "image_models": ["reform-wanx2.1-t2i-turbo"], "video_models": ["reform-sora-2"]}

    def enriched(self):
        answers = {"reform-qwen3.8-flash": {"state": "no", "src": "probe"},
                   "xkiro-qwen/qwen3-vl-plus:free": {"state": "yes", "src": "family"}}
        with mock.patch.object(model_catalog, "vision_state", side_effect=lambda i, p, e=None: answers[i]):
            return model_catalog.enrich(self.payload())

    def test_enrich(self):
        rows = {r["id"]: r for r in self.enriched()["models"]}
        flash = rows["reform-qwen3.8-flash"]
        self.assertEqual(flash["name"], "Qwen3.8 Flash")
        self.assertEqual(flash["kind"], "chat")
        self.assertEqual(flash["account"], "Reform")
        self.assertEqual((flash["caps"]["vision_state"], flash["caps"]["vision"]), ("no", False))
        self.assertEqual(rows["reform-wanx2.1-t2i-turbo"]["kind"], "image")
        self.assertEqual(rows["reform-wanx2.1-t2i-turbo"]["caps"]["vision_state"], "no")
        self.assertEqual(rows["reform-sora-2"]["kind"], "video")
        vl = rows["xkiro-qwen/qwen3-vl-plus:free"]
        self.assertTrue(vl["caps"]["free"])
        self.assertEqual(vl["badges"], ["free"])
        self.assertTrue(vl["caps"]["vision"])

    def test_pick_rows_and_label(self):
        rows = self.enriched()["models"]
        ids = lambda **kw: [r["id"] for r in model_catalog.pick_rows(rows, **kw)]  # noqa: E731
        self.assertEqual(ids(kind="image"), ["reform-wanx2.1-t2i-turbo"])
        self.assertEqual(ids(kind="text"), ["reform-qwen3.8-flash", "xkiro-qwen/qwen3-vl-plus:free"])
        self.assertNotIn("reform-qwen3.8-flash", ids(need="vision"))
        self.assertNotIn("reform-sora-2", ids(hidden=["reform-sora-2"]))
        self.assertIn("reform-sora-2", ids(hidden=["reform-sora-2"], include_hidden=True))
        self.assertEqual(model_catalog.label({"id": "reform-glm-5.3", "name": "GLM-5.3"}), "GLM-5.3 · reform-glm-5.3")
        self.assertEqual(model_catalog.label({"id": "a", "name": "A"}, aliases={"a": "Mine"}, full=False), "Mine")

    def test_build_view(self):
        state = self.enriched()
        prefs = {"model_aliases": {"reform-qwen3.8-flash": "Fast"}, "hidden_models": ["reform-sora-2"],
                 "pinned_models": ["xkiro-qwen/qwen3-vl-plus:free"], "default_model": "reform-qwen3.8-flash"}
        out = model_catalog_api.build(state, prefs)
        self.assertEqual(out["models"][0]["id"], "xkiro-qwen/qwen3-vl-plus:free")      # pinned first
        flash = next(r for r in out["models"] if r["id"] == "reform-qwen3.8-flash")
        self.assertEqual((flash["display"], flash["default"]), ("Fast", True))
        self.assertNotIn("reform-sora-2", [r["id"] for r in out["models"]])
        self.assertIn("reform-sora-2", [r["id"] for r in model_catalog_api.build(state, prefs, include_hidden=True)["models"]])


class CatalogEndpointsTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.calls = []
        state = CatalogTest().enriched()

        async def proxy_json(method, path, body=None):
            self.calls.append((method, path, json.loads(body) if body else None))
            if path == "/api/admin/pg/state":
                return 200, state
            if path.endswith("/probe"):
                if json.loads(body).get("action") == "forget":
                    return 200, {"ok": True}
                return 200, {"vision": True, "verdict": "yes", "answer": "red"}
            if path.endswith("/override"):
                return 200, {"ok": True}
            return 404, {"error": "nope"}

        app = FastAPI()
        model_catalog_api.register(app, proxy_json=proxy_json)
        self.client = TestClient(app)

    def test_list(self):
        with mock.patch.object(model_catalog_api, "_prefs", return_value={}):
            data = self.client.get("/api/pg/model-catalog?kind=image").json()
            self.assertEqual([r["id"] for r in data["models"]], ["reform-wanx2.1-t2i-turbo"])
            data = self.client.get("/api/pg/model-catalog?kind=bogus&need=vision").json()
            self.assertNotIn("reform-qwen3.8-flash", [r["id"] for r in data["models"]])

    def test_vision_actions(self):
        r = self.client.post("/api/pg/model-catalog/vision", json={"model": "xkiro-qwen/qwen3-vl-plus:free", "action": "probe"}).json()
        self.assertEqual((r["ok"], r["vision"]), (True, True))
        self.assertEqual(self.calls[-1][1], "/api/admin/models/xkiro-qwen%2Fqwen3-vl-plus%3Afree/probe")
        self.assertEqual(self.calls[-1][2], {"confirm": True})
        r = self.client.post("/api/pg/model-catalog/vision", json={"model": "reform-glm-5.3", "action": "set", "value": False}).json()
        self.assertEqual(r, {"ok": True, "vision": False})
        self.assertEqual(self.calls[-1][1:], ("/api/admin/models/reform-glm-5.3/override", {"vision": False}))
        self.assertEqual(self.client.post("/api/pg/model-catalog/vision", json={"model": "m", "action": "set", "value": "x"}).status_code, 400)
        r = self.client.post("/api/pg/model-catalog/vision", json={"model": "reform-glm-5.3", "action": "forget"}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(self.calls[-2][2], {"action": "forget"})
        self.assertEqual(self.calls[-1][2], {"vision": None})
        bad = self.client.post("/api/pg/model-catalog/vision", json={"model": "", "action": "probe", "lang": "en"})
        self.assertEqual((bad.status_code, bad.json()["error"]), (400, "Bad request"))


class ImageModelsTest(unittest.TestCase):
    def test_detection(self):
        for name in ("wanx2.1-t2i-turbo", "grok-imagine-image-2.0", "gpt-imagine-2.5", "gpt-image-1", "flux-1.1-pro"):
            self.assertTrue(media_gen.is_image_name(name), name)
        for name in ("qwen3-vl-plus", "qwen3.8-flash", "glm-5.3", "sora-2"):
            self.assertFalse(media_gen.is_image_name(name), name)
        self.assertTrue(media_gen.is_video_name("sora-2"))
        self.assertTrue(media_gen.is_chat_image_model("grok-imagine-image-2.0"))
        self.assertFalse(media_gen.is_chat_image_model("wanx2.1-t2i-turbo"))

    def test_custom_media_ids_declared_and_catalog(self):
        account = SimpleNamespace(fields={"name": "Reform", "image_models": "odd-painter"})
        entries = [SimpleNamespace(id="reform-odd-painter", provider="custom", model="odd-painter"),
                   SimpleNamespace(id="reform-wanx2.1-t2i-turbo", provider="custom", model="wanx2.1-t2i-turbo"),
                   SimpleNamespace(id="reform-mystery", provider="custom", model="mystery"),
                   SimpleNamespace(id="reform-glm-5.3", provider="custom", model="glm-5.3")]
        with mock.patch.object(media_gen, "_catalog_kind", side_effect=lambda e: "image" if e.model == "mystery" else ""):
            images, videos = media_gen.custom_media_ids(entries, [account])
        self.assertEqual(images, ["reform-odd-painter", "reform-wanx2.1-t2i-turbo", "reform-mystery"])
        self.assertEqual(videos, [])

    def test_endpoint_fallback(self):
        provider = SimpleNamespace(base_of=lambda account: "https://api.example.test/v1")
        calls = []

        async def images_ok(account, prompt, model, aspect, refs):
            calls.append(("images", model))
            return ["data:image/png;base64,AAA"]

        async def chat_mismatch(*args, **kwargs):
            calls.append(("chat", args[3]))
            raise ProviderServerError("model_endpoint_mismatch: use /images/generations", 400)

        provider.generate_images = images_ok
        with mock.patch.object(media_gen, "_chat_image", chat_mismatch):
            out = asyncio.run(media_gen.generate_image(provider, object(), "a cat", "grok-imagine-image-2.0"))
        self.assertEqual(out, ["data:image/png;base64,AAA"])
        self.assertEqual(calls, [("chat", "grok-imagine-image-2.0"), ("images", "grok-imagine-image-2.0")])

        calls.clear()

        async def images_mismatch(account, prompt, model, aspect, refs):
            calls.append(("images", model))
            raise ProviderServerError("This model only supports /chat/completions", 400)

        async def chat_ok(*args, **kwargs):
            calls.append(("chat", args[3]))
            return ["data:image/png;base64,BBB"]

        provider.generate_images = images_mismatch
        with mock.patch.object(media_gen, "_chat_image", chat_ok):
            out = asyncio.run(media_gen.generate_image(provider, object(), "a cat", "wanx2.1-t2i-turbo"))
        self.assertEqual(out, ["data:image/png;base64,BBB"])
        self.assertEqual(calls, [("images", "wanx2.1-t2i-turbo"), ("chat", "wanx2.1-t2i-turbo")])

    def test_real_errors_are_not_retried(self):
        provider = SimpleNamespace(base_of=lambda account: "https://api.example.test/v1")

        async def images_quota(*args):
            raise ProviderServerError("quota exceeded", 429)

        provider.generate_images = images_quota
        with mock.patch.object(media_gen, "_chat_image", side_effect=AssertionError("no chat fallback")):
            with self.assertRaises(ProviderError):
                asyncio.run(media_gen.generate_image(provider, object(), "a cat", "wanx2.1-t2i-turbo"))
        self.assertFalse(media_gen.wrong_endpoint(ProviderServerError("Invalid API key", 401)))


class ReadableErrorsTest(unittest.TestCase):
    """English windows get English refusals from Proxy; a model is named instead of «custom»."""

    def test_admin_english(self):
        from app.admin_api import _err_en
        self.assertEqual(_err_en("Нет рабочего подключения для модели broken-x. Проверьте его в MemuAI Proxy."),
                         "No working connection for the model broken-x. Check it in MemuAI Proxy.")
        self.assertTrue(_err_en("Нет доступного аккаунта genspark для генерации").startswith("No usable genspark account"))
        self.assertEqual(_err_en("Модель reform-glm-5.3 не создаёт изображения. Выберите модель изображений."),
                         "The model reform-glm-5.3 does not create images. Pick an image model.")

    def test_generate_stream_uses_english(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import media_routing

        async def stream_lines(path, raw):
            failure = RuntimeError("Нет рабочего подключения для модели broken-x. Проверьте его в MemuAI Proxy.")
            failure.message_en = "No working connection for the model broken-x. Check it in MemuAI Proxy."
            raise failure
            yield ""  # pragma: no cover

        app = FastAPI()
        media_routing.register(app, stream_lines=stream_lines)
        client = TestClient(app)
        for lang, expected in (("en", "No working connection"), ("ru", "Нет рабочего подключения")):
            text = client.post("/api/pg/media/generate", json={"kind": "image", "model": "broken-x", "prompt": "a fox", "lang": lang}).text
            errors = [json.loads(line[6:])["error"] for line in text.splitlines()
                      if line.startswith("data: {") and '"error"' in line]
            self.assertTrue(errors and errors[0].startswith(expected), (lang, text[:300]))

    def test_js_wiring(self):
        js = (ROOT / "app" / "pgstatic" / "media-routing.js").read_text(encoding="utf-8")
        self.assertIn('(PG_LANG === "en" && failure.error_en)', js)
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuModelPicker.vision(m.model).state === \"yes\"", app_js)   # 503 of a vision model → «Try again»


@unittest.skipUnless(NODE, "node is not installed")
class VisionGateJsTest(unittest.TestCase):
    """vision-gate.js in a tiny fake window: suggestions and the «sees images → send directly» rule."""

    def run_js(self, body):
        harness = r"""
const vm = require('vm'), fs = require('fs');
const rows = [
  { id: 'reform-glm-5.3', account: 'Reform', kind: 'chat', caps: { vision_state: 'no', vision_src: 'family' } },
  { id: 'reform-qwen3.8-flash', account: 'Reform', kind: 'chat', caps: { vision_state: 'unknown', vision_src: 'none' } },
  { id: 'xkiro-qwen/qwen3-vl-plus:free', account: 'XKiro', kind: 'chat', caps: { vision_state: 'yes', vision_src: 'family' } },
  { id: 'aws9-gpt-6.1-sol', account: 'AWS9', kind: 'chat', caps: { vision_state: 'yes', vision_src: 'probe' } },
  { id: 'reform-wanx2.1-t2i-turbo', account: 'Reform', kind: 'image', caps: { vision_state: 'no', vision_src: 'kind' } },
];
const prefs = { image_send_mode: 'ask', hidden_models: [], pinned_models: [], default_model: '' };
const picker = {
  rows: () => rows, find: (id) => rows.find((r) => r.id === id) || null, kindOf: (r) => r.kind, recent: () => [],
  vision: (x) => { const r = typeof x === 'object' ? x : rows.find((y) => y.id === x); const c = (r && r.caps) || {};
    return { state: c.vision_state || 'unknown', src: c.vision_src || 'none', text: '', hint: 'x · y' }; },
  name: (id) => id, monogram: () => '', badgesHtml: () => '', svg: () => '', refreshAll: () => {},
};
let panels = 0;
const ctx = { window: {}, document: { querySelector: () => null, getElementById: () => null }, PG_LANG: 'en', fetch: null,
  setTimeout: (f) => f(), console };
ctx.window = ctx; ctx.MemuModelPicker = picker; ctx.pref = (k, d) => (k in prefs ? prefs[k] : d);
ctx.panel = () => { panels++; }; ctx.closePanel = () => {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const G = ctx.MemuVisionGate;
(async () => { const out = {}; BODY; console.log(JSON.stringify(out)); })();
""".replace("BODY", body)
        res = subprocess.run([NODE, "-e", harness, str(ROOT / "app" / "pgstatic" / "vision-gate.js")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout.strip())

    def test_suggest_and_choose(self):
        out = self.run_js("""
out.suggest = G.suggest('reform-glm-5.3');
out.helper = G.helper('reform-glm-5.3');
out.yes = await G.choose('aws9-gpt-6.1-sol');
out.image = await G.choose('reform-wanx2.1-t2i-turbo');
prefs.image_send_mode = 'fallback'; out.fallback = await G.choose('reform-glm-5.3');
prefs.image_send_mode = 'direct'; out.direct = await G.choose('reform-glm-5.3');
prefs.image_send_mode = 'ask'; G.choose('reform-glm-5.3'); out.panels = panels;
prefs.hidden_models = ['aws9-gpt-6.1-sol']; out.suggestHidden = G.suggest('reform-glm-5.3');
""")
        self.assertEqual(out["suggest"], "aws9-gpt-6.1-sol")          # verified by a probe beats a family guess
        self.assertEqual(out["helper"], "aws9-gpt-6.1-sol")
        self.assertEqual((out["yes"], out["image"], out["fallback"], out["direct"]), ("direct", "direct", "fallback", "direct"))
        self.assertEqual(out["panels"], 1)                            # «no» + «ask» → the dialog
        self.assertEqual(out["suggestHidden"], "xkiro-qwen/qwen3-vl-plus:free")


class WiringTest(unittest.TestCase):
    def test_specs_and_build(self):
        for name in ("MemuAI.spec", "MemuAIProxy.spec"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for module in ("app.model_names", "app.model_catalog", "app.model_catalog_api"):
                self.assertIn(f"'{module}'", text, f"{module} in {name}")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        for module in ("app.model_names", "app.model_catalog", "app.model_catalog_api"):
            self.assertGreaterEqual(build.count(f'"{module}"'), 2, module)

    def test_script_tags(self):
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        for src in ("/memu-model-names.js", "/model-picker.js", "/vision-gate.js", "/model-picker.css"):
            self.assertIn(src, html)
            self.assertTrue((ROOT / "app" / "pgstatic" / src.lstrip("/")).exists(), src)
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn("MemuVisionGate.choose(model)", app_js)
        self.assertIn('startsWith("switch:")', app_js)


if __name__ == "__main__":
    unittest.main()
