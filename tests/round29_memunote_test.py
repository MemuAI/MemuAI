"""Round 29 — MemuNote: static app (index.html / CSS / i18n), HTTP API of the window server, document
formats, MemuAI → Settings → «MemuNote» (app/memunote_manager.py) and the build wiring.

Real browser checks (screenshots) are in .phase1/r29-memunote.md; here everything runs headless."""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memunote" / "static"
JS = STATIC / "js"

def node_available() -> bool:
    return shutil.which("node") is not None

class StaticAppTest(unittest.TestCase):
    def test_index_references_existing_files_and_no_inline_script(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="([^"#?]+)', html):
            self.assertTrue((STATIC / ref).is_file(), ref)
        self.assertIn('type="module" src="js/app.js"', html)

        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)[^>]*>")

    def test_css_defines_the_tokens_the_editor_uses(self) -> None:
        css = (STATIC / "memunote.css").read_text(encoding="utf-8")
        used = set(re.findall(r"var\((--[\w-]*\w)\b(?!-)", (JS / "editor.js").read_text(encoding="utf-8")))
        defined = set(re.findall(r"(--[\w-]+)\s*:", css))
        self.assertFalse(used - defined, used - defined)
        for cls in (".tabstrip", ".tab.active", ".toolbar", ".statusbar", ".ai-panel", ".ai-inline", ".doc-page", ".csv-view",
                    ".pdf-page-box", ".md-preview", ".palette", ".modal", ".menu", ".toast", ".settings-grid", ".banner"):
            self.assertIn(cls, css)
        self.assertIn(':root[data-theme="light"]', css)
        self.assertIn("@media print", css)

    @unittest.skipUnless(node_available(), "node is not installed")
    def test_every_string_is_translated_and_english_has_no_cyrillic(self) -> None:
        script = r"""
import { DICT } from "%s";
import fs from "fs";
const dir = %s;
const src = ["app.js", "core.js", "editor.js", "views.js", "ai.js"].map((f) => fs.readFileSync(dir + "/" + f, "utf8")).join("\n");
const keys = new Set([...src.matchAll(/\bt\(\s*"([^"]+)"/g)].map((m) => m[1]));
for (const m of src.matchAll(/(?:tog|sel|num)\("[a-z_]+", "([a-z_]+)"(?:, [^,)]*?)?(?:, "([a-z_]+)")?/g)) { keys.add(m[1]); if (m[2]) keys.add(m[2]); }
for (const m of src.matchAll(/label: "(ai_[a-z_]+)"/g)) keys.add(m[1]);
for (const m of src.matchAll(/\["[a-z]+", "(doc_style_[a-z0-9]+)"\]/g)) keys.add(m[1]);
for (const m of src.matchAll(/T\("(tool_[a-z0-9_]+)"/g)) keys.add(m[1]);
const params = /params: \[([^\]]+)\]/g; let p;
const tone = src.match(/id: "tone"[^\n]*params: \[([^\]]+)\]/)[1].match(/"(\w+)"/g).map((x) => "tone_" + x.slice(1, -1));
const lang = src.match(/id: "translate"[^\n]*params: \[([^\]]+)\]/)[1].match(/"(\w+)"/g).map((x) => "lang_" + x.slice(1, -1));
[...tone, ...lang].forEach((k) => keys.add(k));
["cm:", "lang_", "tone_"].forEach((k) => keys.delete(k));
const out = {};
for (const L of ["ru", "en"]) out[L] = [...keys].filter((k) => !(k in DICT[L]));
out.cyr = Object.entries(DICT.en).filter(([, v]) => /[\u0400-\u04FF]/.test(v)).map(([k]) => k);
out.only = Object.keys(DICT.ru).filter((k) => !(k in DICT.en) && !k.startsWith("cm:")).concat(Object.keys(DICT.en).filter((k) => !(k in DICT.ru)));
out.count = keys.size;
console.log(JSON.stringify(out));
""" % ((JS / "i18n.js").as_uri(), json.dumps(str(JS)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.mjs"
            path.write_text(script, encoding="utf-8")
            result = json.loads(subprocess.check_output(["node", str(path)], text=True))
        self.assertGreater(result["count"], 300)
        self.assertEqual(result["ru"], [])
        self.assertEqual(result["en"], [])
        self.assertEqual(result["cyr"], [], "English UI must not contain Cyrillic")
        self.assertEqual(result["only"], [], "both languages have the same keys")

    @unittest.skipUnless(node_available(), "node is not installed")
    def test_scripts_parse(self) -> None:
        for path in list(JS.glob("*.js")) + [ROOT / "app" / "pgstatic" / "memunote-settings.js"]:
            flag = ["--input-type=module", "--check"] if path.parent == JS else ["--check"]
            if path.parent == JS:
                subprocess.run(["node", "--check", str(path)], check=True)
            else:
                subprocess.run(["node", *flag[-1:], str(path)], check=True)

    def test_vendor_bundle_is_shipped(self) -> None:
        self.assertTrue((STATIC / "vendor" / "cm" / "cm.js").is_file())
        self.assertTrue((STATIC / "vendor" / "pdfjs" / "pdf.min.mjs").is_file())
        self.assertTrue((STATIC / "vendor" / "pdfjs" / "pdf.worker.min.mjs").is_file())

class ServerApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.tmp.name) / "data"
        cls.files = Path(cls.tmp.name) / "files"
        cls.files.mkdir()
        cls.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": str(cls.data)})
        cls.env.start()
        from app.memunote import server
        from app.memunote.formats import docx, richhtml
        (cls.files / "notes.md").write_text("# Title\n\n| a | b |\n| - | - |\n| 1 | 2 |\n", encoding="utf-8")
        (cls.files / "cp1251.txt").write_bytes("Привет, мир\r\nвторая строка\r\n".encode("cp1251"))
        (cls.files / "report.docx").write_bytes(docx.write_docx(richhtml.parse_html(
            "<h1>Отчёт</h1><p>Текст <b>жирный</b></p><table><tr><th>A</th></tr><tr><td>1</td></tr></table>"), "Report"))
        cls.app = server.NoteApp([])
        cls.httpd, _ = server.start(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.env.stop()
        cls.tmp.cleanup()

    def request(self, method: str, path: str, body=None, raw: bytes = None, token: bool = True, host: str = ""):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.app.port}"}
        if token:
            headers["X-MN-Token"] = self.app.token
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8"); headers["Content-Type"] = "application/json"
        elif raw is not None:
            data = raw; headers["Content-Type"] = "text/plain; charset=utf-8"
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response, payload

    def json(self, method: str, path: str, body=None, raw: bytes = None):
        response, payload = self.request(method, path, body, raw)
        return response.status, json.loads(payload or b"{}")

    def test_static_index_with_csp(self) -> None:
        response, payload = self.request("GET", "/index.html", token=False)
        self.assertEqual(response.status, 200)
        self.assertIn(b"MemuNote", payload)
        self.assertIn("script-src 'self'", response.getheader("Content-Security-Policy"))
        response, _ = self.request("GET", "/../memunote.py", token=False)
        self.assertEqual(response.status, 404)

    def test_api_needs_token_and_loopback_host(self) -> None:
        response, _ = self.request("GET", "/api/boot", token=False)
        self.assertIn(response.status, (401, 403))
        response, _ = self.request("GET", "/api/boot", host="evil.example:80")
        self.assertIn(response.status, (400, 403, 421))

    def test_boot_gives_settings_theme_and_language(self) -> None:
        status, data = self.json("GET", "/api/boot")
        self.assertEqual(status, 200)
        self.assertIn(data["lang"], ("ru", "en"))
        self.assertIn(data["theme"]["mode"], ("dark", "light"))
        self.assertIn("word_wrap", data["settings"])
        self.assertTrue(data["fonts"])

    def test_open_markdown_cp1251_and_docx(self) -> None:
        status, md = self.json("POST", "/api/open", {"path": str(self.files / "notes.md")})
        self.assertEqual((status, md["kind"]), (200, "markdown"))
        if "text" not in md:
            self.assertEqual(md["web_encoding"], "utf-8")
            response, payload = self.request("GET", f"/api/raw?path={self.files / 'notes.md'}&t={self.app.token}")
            md["text"] = payload.decode("utf-8")
        self.assertIn("| a | b |", md["text"])
        status, txt = self.json("POST", "/api/open", {"path": str(self.files / "cp1251.txt")})
        self.assertEqual(txt["encoding"], "cp1251")
        self.assertEqual(txt["eol"], "crlf")
        if "text" not in txt:
            response, payload = self.request("GET", f"/api/raw?path={self.files / 'cp1251.txt'}&t={self.app.token}")
            txt["text"] = payload.decode(txt["web_encoding"])
        self.assertTrue(txt["text"].startswith("Привет"))
        status, doc = self.json("POST", "/api/open", {"path": str(self.files / "report.docx")})
        self.assertEqual(doc["kind"], "docx")
        self.assertIn("<h1", doc["html"])
        self.assertIn("<table", doc["html"])

    def test_save_text_keeps_encoding_and_detects_conflicts(self) -> None:
        target = self.files / "saved.txt"
        query = f"/api/save_text?path={target}&encoding=cp1251&eol=crlf&bom=0&mtime=0&force=0"
        status, data = self.json("POST", query.replace("\\", "%5C"), raw="Строка 1\nСтрока 2".encode("utf-8"))
        self.assertEqual(status, 200, data)
        self.assertEqual(target.read_bytes(), "Строка 1\r\nСтрока 2".encode("cp1251"))
        stale = data["mtime"] - 100
        status, data = self.json("POST", f"/api/save_text?path={target}&encoding=utf-8&eol=lf&mtime={stale}&force=0",
                                 raw=b"other")
        self.assertTrue(data.get("conflict"), data)

    def test_rich_round_trip(self) -> None:
        target = self.files / "out.docx"
        status, data = self.json("POST", "/api/save_rich", {"path": str(target), "format": "docx", "force": True,
                                                            "html": "<h2>Итоги</h2><ul><li>один</li></ul>"})
        self.assertEqual(status, 200, data)
        status, doc = self.json("POST", "/api/open", {"path": str(target)})
        self.assertIn("Итоги", doc["html"])
        self.assertIn("<li", doc["html"])

    def test_ai_status_offline_without_proxy(self) -> None:
        with mock.patch.dict(os.environ, {"MEMUAI_PROXY_URL": "http://127.0.0.1:9"}):
            status, data = self.json("GET", "/api/ai/status?force=1")
        self.assertEqual(status, 200)
        self.assertIn("online", data)

def make_package(path: Path, version: str = "3.1", extra=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("memunote-package.json", json.dumps({"version": version, "exe": "MemuNote.exe"}))
        archive.writestr("MemuNote.exe", b"exe-" + version.encode())
        archive.writestr("_internal/app/memunote/static/index.html", "<html></html>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)

class ManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "MemuAI"
        self.root.mkdir()
        (self.root / "install.json").write_text(json.dumps({"product": "memuai", "components": ["proxy", "memuai"],
                                                            "entries": {"memuai": ["MemuAI"]}}), encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"MEMUAI_INSTALL_DIR": str(self.root),
                                                "MEMUAI_DATA_DIR": str(Path(self.tmp.name) / "data")})
        self.env.start()
        from app import memunote_manager
        self.m = memunote_manager
        self.runs = []
        self.run_patch = mock.patch.object(memunote_manager, "_run", side_effect=lambda exe, args, timeout=20: self.runs.append(args) or 0)
        self.run_patch.start()

    def tearDown(self) -> None:
        self.run_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_status_without_package(self) -> None:
        data = self.m.status()
        self.assertFalse(data["installed"])
        self.assertFalse(data["package"]["present"])
        self.assertFalse(data["dev"])
        with self.assertRaises(self.m.ManagerError) as ctx:
            self.m.install()
        self.assertEqual(ctx.exception.code, "no_package")

    def test_install_update_and_uninstall_round_trip(self) -> None:
        package = self.root / "Packages" / "MemuNote.zip"
        make_package(package, "3.1")
        self.assertTrue(self.m.status()["package"]["valid"])
        result = self.m.install()
        self.assertTrue(result["installed"])
        self.assertEqual(result["version"], "3.1")
        self.assertEqual((self.root / "MemuNote" / "MemuNote.exe").read_bytes(), b"exe-3.1")
        self.assertFalse(package.exists(), "the archive is replaced by the installed copy")
        state = json.loads((self.root / "install.json").read_text(encoding="utf-8"))
        self.assertIn("memunote", state["components"])
        self.assertEqual(state["entries"]["memunote"], ["MemuNote"])

        make_package(package, "3.2")
        self.assertTrue(self.m.status()["update_available"])
        result = self.m.install()
        self.assertTrue(result["updated"])
        self.assertIn(["--shutdown"], self.runs)
        self.assertEqual((self.root / "MemuNote" / "MemuNote.exe").read_bytes(), b"exe-3.2")

        result = self.m.uninstall()
        self.assertFalse(result["installed"])
        self.assertIn(["--unregister-open-with"], self.runs)
        self.assertFalse((self.root / "MemuNote").exists())
        self.assertTrue(package.is_file())
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(archive.read("MemuNote.exe"), b"exe-3.2")
        state = json.loads((self.root / "install.json").read_text(encoding="utf-8"))
        self.assertNotIn("memunote", state["components"])
        self.assertEqual(self.m.install()["version"], "3.2", "reinstall from the kept package")

    def test_unsafe_package_is_rejected_and_nothing_is_left(self) -> None:
        package = self.root / "Packages" / "MemuNote.zip"
        make_package(package, extra={"../evil.txt": "x"})
        with self.assertRaises(self.m.ManagerError) as ctx:
            self.m.install()
        self.assertEqual(ctx.exception.code, "package_invalid")
        self.assertFalse((self.root / "MemuNote").exists())
        self.assertFalse((self.root.parent / "evil.txt").exists())
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".~")], [])

    def test_routes(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        self.m.register(app)
        client = TestClient(app)
        self.assertFalse(client.get("/api/pg/memunote").json()["installed"])
        response = client.post("/api/pg/memunote/install")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "no_package")
        make_package(self.root / "Packages" / "MemuNote.zip")
        self.assertTrue(client.post("/api/pg/memunote/install").json()["installed"])
        with mock.patch.object(self.m.subprocess, "Popen") as popen:
            self.assertTrue(client.post("/api/pg/memunote/open", json={"files": ["C:\\a.md"]}).json()["ok"])
            self.assertTrue(popen.call_args[0][0][0].endswith("MemuNote.exe"))
            self.assertEqual(popen.call_args[0][0][1], "C:\\a.md")
        self.assertFalse(client.post("/api/pg/memunote/uninstall", json={}).json()["installed"])

    def test_source_checkout_runs_the_script(self) -> None:
        with mock.patch.dict(os.environ, {"MEMUAI_INSTALL_DIR": ""}):
            data = self.m.status()
            self.assertTrue(data["dev"])
            self.assertTrue(data["installed"])
            with mock.patch.object(self.m.subprocess, "Popen") as popen:
                self.m.open_app([])
            self.assertTrue(popen.call_args[0][0][1].endswith("memunote.py"))

class WiringTest(unittest.TestCase):
    def test_build_target_and_spec(self) -> None:
        import build
        target = build.TARGETS["memunote"]
        self.assertEqual(target["entry"], "memunote.py")
        modules = {"app.memunote." + p.stem for p in (ROOT / "app" / "memunote").glob("*.py") if p.stem != "__init__"}
        modules |= {"app.memunote.formats." + p.stem for p in (ROOT / "app" / "memunote" / "formats").glob("*.py")
                    if p.stem != "__init__"}
        self.assertFalse(modules - set(target["hidden"]), modules - set(target["hidden"]))
        self.assertIn("app/memunote/static;app/memunote/static", target["data"])
        spec = (ROOT / "MemuNote.spec").read_text(encoding="utf-8")
        for name in target["hidden"]:
            self.assertIn(repr(name), spec)
        self.assertIn("'memunote.py'", spec)
        self.assertTrue((ROOT / "memunote-icon.ico").is_file())
        self.assertIn("memunote", inspect_source(build.build_exes))
        for name in ("app.memunote_manager", "app.memunote.winsys"):
            self.assertIn(name, build.TARGETS["memuai"]["hidden"])
            self.assertIn(repr(name), (ROOT / "MemuAI.spec").read_text(encoding="utf-8"))

    def test_memuai_hooks(self) -> None:
        api = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        self.assertIn("memunote_manager.register(app)", api)
        index = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/memunote-settings.js?v=1.0-r38"></script>', index)
        self.assertIn('href="/memunote-settings.css?v=1.0-r29"', index)
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertIn('P("memunote", "app"', app_js)
        self.assertIn("MemuNoteSettings.mount(", app_js)
        self.assertIn('id="pane-memunote"', app_js)

    def test_docs_exist_in_both_languages(self) -> None:
        for lang in ("ru", "en"):
            path = ROOT / "app" / "docs" / lang / "memunote.md"
            self.assertTrue(path.is_file(), path)
        index = json.loads((ROOT / "app" / "docs" / "index.json").read_text(encoding="utf-8"))
        self.assertIn("memunote", json.dumps(index))
        english = (ROOT / "app" / "docs" / "en" / "memunote.md").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[\u0400-\u04FF]", english))

def inspect_source(func) -> str:
    import inspect
    return inspect.getsource(func)

if __name__ == "__main__":
    unittest.main()
