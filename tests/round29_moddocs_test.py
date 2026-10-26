"""Round 29 (r29-moddocs-keys): the modules «AI pack», package validation and the docs page.

* the pack builds in both languages, full and compact; it carries the bridge reference, every page of
  the «Modules» group (except the page about the pack itself), the examples and (full) the harness;
* every example module in app/docs/module-examples validates without errors, also as an «AI answer»
  (files in code blocks) and as a ZIP; broken packages get errors with file names;
* routes /api/pg/modules-ai/{pack,harness,validate} answer through the real MemuAI app, and the
  validated package imports through /api/pg/modules/import;
* the frontend files exist and are wired: index.html tags, docs.js memu-widget hook, modules.js
  renderImport hook; the English strings of modules-aipack.js contain no Cyrillic;
* the docs page modules-ai-pack exists in both languages, is in the «modules» group and has the widget;
  JavaScript blocks of the modules docs compile;
* build entries: MemuAI.spec and build.py ship app.modules_ai_pack and app.modules_validate.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29-moddocs-")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "app" / "docs" / "module-examples"
STATIC = ROOT / "app" / "pgstatic"
CYR = re.compile(r"[А-Яа-яЁё]")

def example_files(name: str):
    return {p.name: p.read_text(encoding="utf-8") for p in (EXAMPLES / name).iterdir() if p.is_file()}

class PackTest(unittest.TestCase):
    def test_pack_both_languages(self) -> None:
        from app import modules_ai_pack as pack
        for lang in ("ru", "en"):
            full = pack.build_pack(lang, include_harness=True)
            compact = pack.build_pack(lang, include_harness=False)
            self.assertGreater(len(full), len(compact))
            self.assertIn("----- BEGIN memu-module-test.js -----", full)
            self.assertNotIn("----- BEGIN memu-module-test.js -----", compact)
            self.assertIn("Memu.request", compact)
            pages = pack.module_doc_pages(lang)
            ids = [p["id"] for p in pages]
            self.assertIn("modules-bridge", ids)
            self.assertNotIn("modules-ai-pack", ids)
            for row in pack.examples():
                self.assertIn(row["id"], compact)
            self.assertTrue(pack.pack_name(lang).endswith(f"-{lang}.txt"))
            self.assertTrue(pack.pack_name(lang, False).endswith(f"-{lang}-compact.txt"))

    def test_harness_has_the_real_bridge(self) -> None:
        from app import modules_ai_pack as pack
        text = pack.harness_source()
        self.assertNotIn("/*@bridge*/null/*@end*/", text)
        if shutil.which("node"):
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "memu-module-test.js"
                target.write_text(text, encoding="utf-8")
                result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

class ValidateTest(unittest.TestCase):
    def test_examples_validate(self) -> None:
        from app import modules_validate as v
        for name in ("quick-notes", "model-chat", "doc-brief"):
            report = v.validate_package(v._from_files(example_files(name), name))
            self.assertTrue(report["ok"], (name, report["problems"]))
            self.assertEqual(report["package"]["format"], "memu-module")

    def test_answer_text_and_zip(self) -> None:
        from app import modules_validate as v
        files = example_files("model-chat")
        answer = "Sure, here is the module.\n\n" + "\n\n".join(
            f"### FILE: {name}\n```{name.rsplit('.', 1)[-1]}\n{text}\n```" for name, text in files.items())
        report = v.validate_package(v.from_answer_text(answer))
        self.assertTrue(report["ok"], report["problems"])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, text in files.items():
                archive.writestr(f"model-chat/{name}", text)
        report = v.validate_zip_base64(base64.b64encode(buffer.getvalue()).decode())
        self.assertTrue(report["ok"], report["problems"])

    def test_broken_package_reports_errors(self) -> None:
        from app import modules_validate as v
        files = example_files("quick-notes")
        manifest = json.loads(files["manifest.json"])
        manifest["id"] = "Bad Id"
        files["manifest.json"] = json.dumps(manifest)
        files["main.js"] += "\nfetch('https://example.com'); eval('1'); function (\n"
        report = v.validate_package(v._from_files(files, "broken"))
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["counts"]["error"], 2)
        fields = {row["field"] for row in report["problems"] if row["level"] == "error"}
        self.assertTrue(fields & {"manifest", "main.js"}, fields)
        for row in report["problems"]:
            self.assertTrue(row["ru"] and row["en"])
            self.assertFalse(CYR.search(row["en"]), row["en"])
        with self.assertRaises(ValueError):
            v.from_answer_text("no files here")

class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        cls.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def test_pack_routes(self) -> None:
        response = self.client.get("/api/pg/modules-ai/pack?lang=en&harness=0&format=json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["lang"], "en")
        self.assertFalse(data["harness"])
        self.assertGreater(data["pages"], 10)
        self.assertGreater(data["tokens"], 1000)
        response = self.client.get("/api/pg/modules-ai/pack?lang=ru")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers.get("content-disposition", ""))
        response = self.client.get("/api/pg/modules-ai/harness")
        self.assertEqual(response.status_code, 200)
        self.assertIn("memu-module-test.js", response.headers.get("content-disposition", ""))

    def test_validate_and_import(self) -> None:
        files = example_files("doc-brief")
        response = self.client.post("/api/pg/modules-ai/validate", json={"files": files})
        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertTrue(report["ok"], report["problems"])
        response = self.client.post("/api/pg/modules/import",
                                    json={"package": report["package"], "new_id": "r29-doc-brief-test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["module"]["id"], "r29-doc-brief-test")
        response = self.client.post("/api/pg/modules-ai/validate", json={"text": "hello"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json().get("load_error"))

    def test_static_files_served(self) -> None:
        for path in ("/modules-aipack.js", "/modules-aipack.css"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

class FrontendTest(unittest.TestCase):
    def test_wiring(self) -> None:
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("modules-aipack.js", "modules-aipack.css"):
            self.assertTrue((STATIC / name).is_file(), name)
            self.assertIn(f"/{name}?v=", index)
        self.assertLess(index.index("/docs.js"), index.index("/modules-aipack.js"))
        docs = (STATIC / "docs.js").read_text(encoding="utf-8")
        self.assertIn('lang === "memu-widget"', docs)
        self.assertIn("MemuModuleTools.hydrate(article)", docs)
        modules = (STATIC / "modules.js").read_text(encoding="utf-8")
        self.assertIn("MemuModuleTools.enhanceImport(host", modules)
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("modules_ai_pack.register(app)", api)

    def test_english_strings_have_no_cyrillic(self) -> None:
        source = (STATIC / "modules-aipack.js").read_text(encoding="utf-8")
        for match in re.finditer(r"\bL\(\s*(\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`)\s*,\s*(\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`)", source):
            self.assertFalse(CYR.search(match.group(2)), match.group(2))

    def test_node_check(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        for name in ("modules-aipack.js", "docs.js", "modules.js"):
            result = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, (name, result.stderr))

class DocsTest(unittest.TestCase):
    def test_page_registered(self) -> None:
        index = json.loads((ROOT / "app" / "docs" / "index.json").read_text(encoding="utf-8"))
        group = next(g for g in index["groups"] if g["id"] == "modules")
        self.assertIn("modules-ai-pack", group["pages"])
        for lang in ("ru", "en"):
            text = (ROOT / "app" / "docs" / lang / "modules-ai-pack.md").read_text(encoding="utf-8")
            self.assertIn("```memu-widget\nai-pack\n```", text)
        self.assertFalse(CYR.search((ROOT / "app" / "docs" / "en" / "modules-ai-pack.md").read_text(encoding="utf-8")))

    def test_bridge_chat_advice_is_real(self) -> None:
        for lang in ("ru", "en"):
            text = (ROOT / "app" / "docs" / lang / "modules-bridge-chat.md").read_text(encoding="utf-8")
            self.assertNotIn("Memu.env.default_model", text)

    def test_docs_javascript_compiles(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        bad = []
        for path in sorted((ROOT / "app" / "docs").glob("*/modules*.md")):
            text = path.read_text(encoding="utf-8")
            for number, match in enumerate(re.finditer(r"```(?:javascript|js)\n(.*?)```", text, re.S)):
                result = subprocess.run(["node", "--check", "--input-type=module", "-"], input=match.group(1),
                                        capture_output=True, text=True)
                if result.returncode:
                    bad.append(f"{path.parent.name}/{path.name} block {number}")
        self.assertEqual(bad, [])

class BuildTest(unittest.TestCase):
    def test_spec_and_build(self) -> None:
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        for module in ("app.modules_ai_pack", "app.modules_validate"):
            self.assertIn(f"'{module}'", spec)
            self.assertIn(f'"{module}"', build)

if __name__ == "__main__":
    unittest.main()
