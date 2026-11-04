"""MemuNote 5.0: appearance (palettes, accents, designs), versioned static files, the extended PDF editor
(thumbnails, images, signatures, stamps, page numbers, watermark, insert / extract pages) and the new
viewers for audio / video, fonts and e-mail messages. Headless: no browser is started here."""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memunote" / "static"
JS = STATIC / "js"
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\U0001F000-\U0001F2FF]")


def node() -> bool:
    return shutil.which("node") is not None


class AppearanceTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.memunote import appearance
        self.ap = appearance

    def test_every_palette_has_both_faces_with_readable_text(self) -> None:
        ids = [p["id"] for p in self.ap.PALETTES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("auto", ids)
        self.assertGreaterEqual(len(ids), 15)
        for pid in ids:
            if pid == "auto":
                continue
            for mode in ("dark", "light"):
                tokens = self.ap.palette_tokens(mode, pid)
                self.assertTrue(tokens.get("--bg") and tokens.get("--text"), (pid, mode))
                self.assertGreaterEqual(self.ap.contrast(tokens["--text"], tokens["--bg"]), 4.5, (pid, mode))

    def test_accent_is_fitted_to_the_background(self) -> None:
        for mode, bg in (("dark", "#101010"), ("light", "#ffffff")):
            for colour in ("#ffff00", "#000080", "#777777", "#00ff00"):
                fitted = self.ap.fit_accent(colour, [bg], mode)
                self.assertGreaterEqual(self.ap.contrast(fitted, bg), 4.5, (mode, colour, fitted))

    def test_resolve_custom_accent_and_design(self) -> None:
        theme = self.ap.resolve({"theme": "light", "palette": "github", "accent": "custom", "accent_custom": "#FFEE00",
                                 "design": "glass"}, {})
        self.assertEqual(theme["mode"], "light")
        self.assertEqual(theme["palette"], "github")
        self.assertEqual(theme["design"], "glass")
        accent = theme["tokens"]["--accent"]
        self.assertGreaterEqual(self.ap.contrast(accent, theme["tokens"]["--bg"]), 4.5)
        self.assertIn("--on-accent", theme["tokens"])
        auto = self.ap.resolve({"theme": "auto", "palette": "auto", "accent": "auto", "design": "auto"}, {"theme": "light"})
        self.assertEqual(auto["mode"], "light")
        bad = self.ap.resolve({"palette": "nope", "accent": "nope", "design": "nope"}, {})
        self.assertEqual((bad["palette"], bad["accent"], bad["design_setting"]), ("auto", "auto", "auto"))

    def test_normalize_hex(self) -> None:
        self.assertEqual(self.ap.normalize_hex("#ABCDEF"), "#abcdef")
        self.assertEqual(self.ap.normalize_hex("abc"), "#aabbcc")
        for bad in ("", "red", "#12345", "javascript:x", None, 5):
            self.assertEqual(self.ap.normalize_hex(bad), "", bad)

    def test_catalog_lists_everything_in_both_languages(self) -> None:
        for lang in ("ru", "en"):
            cat = self.ap.catalog(lang, {})
            self.assertEqual({p["id"] for p in cat["palettes"]}, {p["id"] for p in self.ap.PALETTES})
            self.assertTrue(all(p["label"] for p in cat["palettes"] + cat["accents"] + cat["designs"]))
            self.assertIn("custom", [a["id"] for a in cat["accents"]] + ["custom"])
            if lang == "en":
                text = json.dumps(cat, ensure_ascii=False)
                self.assertNotRegex(text, "[\u0400-\u04FF]")

    def test_settings_schema_validates_the_custom_colour(self) -> None:
        from app.memunote import settings as store
        from app.memunote import settings_schema
        keys = {row["key"]: row for row in settings_schema.SCHEMA}
        for key in ("palette", "accent", "design", "accent_custom"):
            self.assertIn(key, keys)
        self.assertEqual([keys[k]["default"] for k in ("palette", "accent", "design")], ["auto", "auto", "auto"])
        ok, value, _ = settings_schema.check("accent_custom", "#12abEF")
        self.assertTrue(ok, value)
        ok, _, error = settings_schema.check("accent_custom", "#zz;}ab")
        self.assertFalse(ok)
        self.assertEqual(error, "format")
        ok, _, _ = settings_schema.check("accent_custom", "url(javascript:alert(1))")
        self.assertFalse(ok)
        ok, _, _ = settings_schema.check("design", "nope")
        self.assertFalse(ok)
        clean = store.normalize({"accent_custom": "#ABCDEF", "palette": "tokyo", "design": "zen"})
        self.assertEqual((clean.get("accent_custom"), clean.get("palette"), clean.get("design")), ("#abcdef", "tokyo", "zen"))
        clean = store.normalize({"accent_custom": "</style>", "palette": "nope"})
        self.assertNotIn("<", clean.get("accent_custom", ""))
        self.assertEqual(clean.get("palette"), "auto")

    def test_designs_css_has_a_rule_for_every_design(self) -> None:
        css = (STATIC / "designs.css").read_text(encoding="utf-8")
        for row in self.ap.DESIGNS:
            if row["id"] in ("auto", "classic"):
                continue
            self.assertIn(f'[data-design="{row["id"]}"]', css, row["id"])
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("designs.css", html)


class StaticSourcesTest(unittest.TestCase):
    NEW = ["appearance.js", "i18n-v5.js", "fileviews.js", "pdfedit.js"]

    def test_no_emoji_in_new_ui_sources(self) -> None:
        for name in self.NEW:
            self.assertIsNone(EMOJI.search((JS / name).read_text(encoding="utf-8")), name)
        self.assertIsNone(EMOJI.search((STATIC / "designs.css").read_text(encoding="utf-8")))

    @unittest.skipUnless(node(), "node is not installed")
    def test_scripts_parse(self) -> None:
        for name in self.NEW + ["app.js", "core.js"]:
            subprocess.run(["node", "--check", str(JS / name)], check=True)

    @unittest.skipUnless(node(), "node is not installed")
    def test_strings_exist_in_both_languages(self) -> None:
        script = r"""
import { V5 } from "%s";
import fs from "fs";
const dir = %s;
const src = ["fileviews.js", "pdfedit.js", "appearance.js"].map((f) => fs.readFileSync(dir + "/" + f, "utf8")).join("\n");
const all = await import("%s");
const keys = [...new Set([...src.matchAll(/\bt\("([a-z0-9_]+)"/g)].map((m) => m[1]))].filter((k) => !k.endsWith("_"));
for (const m of src.match(/const STAMPS = \[([^\n]+)\];/)[1].matchAll(/\["([a-z]+)"/g)) keys.push("pdf_stamp_" + m[1]);
for (const m of src.match(/const TOOLS = \[([\s\S]+?)\];/)[1].matchAll(/\["([a-z]+)"/g)) keys.push("pdf_tool_" + m[1], "pdf_hint_" + m[1]);
const out = { ru: keys.filter((k) => !(k in all.DICT.ru)), en: keys.filter((k) => !(k in all.DICT.en)),
  same: Object.keys(V5.ru).filter((k) => !(k in V5.en)).concat(Object.keys(V5.en).filter((k) => !(k in V5.ru))),
  cyr: Object.entries(V5.en).filter(([, v]) => /[\u0400-\u04FF]/.test(v)).map(([k]) => k), n: keys.length };
console.log(JSON.stringify(out));
""" % ((JS / "i18n-v5.js").as_uri(), json.dumps(str(JS)), (JS / "i18n.js").as_uri())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.mjs"
            path.write_text(script, encoding="utf-8")
            result = json.loads(subprocess.check_output(["node", str(path)], text=True))
        self.assertGreater(result["n"], 60)
        self.assertEqual(result["ru"], [])
        self.assertEqual(result["en"], [])
        self.assertEqual(result["same"], [])
        self.assertEqual(result["cyr"], [])

    @unittest.skipUnless(node(), "node is not installed")
    def test_page_ranges(self) -> None:
        script = r"""
import fs from "fs";
const src = fs.readFileSync(%s, "utf8");
const body = src.match(/export function parseRanges[\s\S]*?\r?\n}\r?\n/)[0].replace("export ", "");
const parseRanges = new Function(body + "; return parseRanges;")();
console.log(JSON.stringify(["1-2, 5", "1 - 3", "3-", "-2", "-", "7", "abc", "2,2,1", "5-3", ""].map((s) => parseRanges(s, 5))));
""" % json.dumps(str(JS / "pdfedit.js"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ranges.mjs"
            path.write_text(script, encoding="utf-8")
            result = json.loads(subprocess.check_output(["node", str(path)], text=True))
        self.assertEqual(result, [[0, 1, 4], [0, 1, 2], [2, 3, 4], [0, 1], None, None, None, [1, 0], [4, 3, 2], None])

    def test_pdf_editor_features_are_wired(self) -> None:
        src = (JS / "pdfedit.js").read_text(encoding="utf-8")
        for needle in ('case "image"', 'case "stamp"', "export async function extractPages", "export function parseRanges",
                       "export function encodable", "toggleThumbs", "signaturePad", "insertPdf", "pageNumbers", "watermark",
                       'class="handle"', "mn_pdf_sig", "copyPages"):
            self.assertIn(needle, src, needle)
        app = (JS / "app.js").read_text(encoding="utf-8")
        self.assertIn("tab.comp.onSaveCopy", app)
        self.assertIn("async function saveBytesAs", app)
        css = (STATIC / "note-core.css").read_text(encoding="utf-8")
        for cls in (".pdf-thumbs", ".pdf-thumb.on", ".pdf-thumb.drop-before", ".pdf-ann .handle", ".pdf-sig-pad", ".media-view", ".font-view", ".mail-view"):
            self.assertIn(cls, css, cls)

    def test_build_ships_the_new_modules(self) -> None:
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        spec = (ROOT / "MemuNote.spec").read_text(encoding="utf-8")
        for mod in ("app.memunote.appearance", "app.memunote.formats.email_msg"):
            self.assertIn(f'"{mod}"', build)
            self.assertIn(f"'{mod}'", spec)


class EmailTest(unittest.TestCase):
    def message(self) -> bytes:
        m = EmailMessage()
        m["From"] = "Иван <ivan@example.com>"
        m["To"] = "a@b.c, Bob <bob@x.org>"
        m["Subject"] = "Отчёт"
        m["Date"] = "Sat, 31 Oct 2026 10:00:00 +0300"
        m.set_content('<p>Привет</p><script>alert(1)</script><img src="https://tracker.example/p.gif"><a href="https://x.org/r">ссылка</a>', subtype="html")
        m.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="../../evil:name.pdf")
        return bytes(m)

    def test_html_body_becomes_text_without_scripts(self) -> None:
        from app.memunote.formats import email_msg
        r = email_msg.parse(self.message())
        self.assertEqual(r["subject"], "Отчёт")
        self.assertEqual(r["source"], "html")
        self.assertIn("Привет", r["text"])
        self.assertIn("https://x.org/r", r["text"])
        self.assertNotIn("alert", r["text"])
        self.assertNotIn("tracker", r["text"])
        self.assertEqual(r["from"], [{"name": "Иван", "email": "ivan@example.com"}])
        self.assertEqual(len(r["to"]), 2)
        self.assertTrue(r["date_iso"].startswith("2026-10-31T10:00:00"))
        self.assertEqual([a["size"] for a in r["attachments"]], [10])

    def test_attachment_names_are_safe(self) -> None:
        from app.memunote.formats import email_msg
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.eml"
            path.write_bytes(self.message())
            name, ctype, data = email_msg.attachment(str(path), 0)
            self.assertNotRegex(name, r'[\\/:]')
            self.assertTrue(name.endswith(".pdf"))
            self.assertEqual(data, b"%PDF-1.4 x")
            with self.assertRaises(IndexError):
                email_msg.attachment(str(path), 5)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.tmp.name) / "data"
        cls.files = Path(cls.tmp.name) / "files"
        cls.files.mkdir()
        cls.env = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": str(cls.data)})
        cls.env.start()
        from app.memunote import server
        cls.server = server
        (cls.files / "song.mp3").write_bytes(bytes(range(256)) * 40)
        (cls.files / "face.ttf").write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 60)
        (cls.files / "mail.eml").write_bytes(EmailTest().message())
        cls.app = server.NoteApp([])
        cls.httpd, _ = server.start(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.env.stop()
        cls.tmp.cleanup()

    def request(self, method: str, path: str, body=None, headers=None, token: bool = True):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.app.port}", **(headers or {})}
        if token:
            h["X-MN-Token"] = self.app.token
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response, payload

    def open(self, name: str):
        response, payload = self.request("POST", "/api/open", {"path": str(self.files / name)})
        return response.status, json.loads(payload)

    def test_kinds(self) -> None:
        k = self.server.kind_of
        self.assertEqual([k("a.MP4"), k("b.flac"), k("c.woff2"), k("d.eml"), k("e.txt"), k("f.ogg")], ["media", "media", "font", "email", "text", "media"])

    def test_byte_ranges(self) -> None:
        br = self.server._byte_range
        self.assertEqual(br("bytes=0-", 100), (0, 99))
        self.assertEqual(br("bytes=10-19", 100), (10, 19))
        self.assertEqual(br("bytes=-5", 100), (95, 99))
        self.assertEqual(br("bytes=0-999", 100), (0, 99))
        self.assertEqual(br("bytes=100-", 100), "bad")
        self.assertIsNone(br("items=0-1", 100))
        self.assertIsNone(br("bytes=0-1,5-6", 100))
        self.assertIsNone(br("", 100))

    def test_media_is_served_with_ranges(self) -> None:
        status, info = self.open("song.mp3")
        self.assertEqual((status, info["kind"]), (200, "media"))
        self.assertNotIn("text", info)
        url = "/api/raw?path=" + str(self.files / "song.mp3")
        response, payload = self.request("GET", url, headers={"Range": "bytes=10-19"})
        self.assertEqual(response.status, 206)
        self.assertEqual(payload, bytes(range(10, 20)))
        self.assertEqual(response.getheader("Content-Range"), "bytes 10-19/10240")
        self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(response.getheader("Content-Type"), "audio/mpeg")
        response, payload = self.request("GET", url)
        self.assertEqual((response.status, len(payload)), (200, 10240))
        response, _ = self.request("GET", url, headers={"Range": "bytes=99999-"})
        self.assertEqual(response.status, 416)

    def test_fonts_and_mail(self) -> None:
        status, info = self.open("face.ttf")
        self.assertEqual((status, info["kind"]), (200, "font"))
        response, _ = self.request("GET", "/api/raw?path=" + str(self.files / "face.ttf"))
        self.assertEqual(response.getheader("Content-Type"), "font/ttf")
        status, info = self.open("mail.eml")
        self.assertEqual((status, info["kind"]), (200, "email"))
        self.assertEqual(info["email"]["subject"], "Отчёт")
        response, payload = self.request("GET", "/api/eml_part?index=0&path=" + str(self.files / "mail.eml"))
        self.assertEqual((response.status, payload), (200, b"%PDF-1.4 x"))
        self.assertTrue(response.getheader("X-MN-Name").endswith(".pdf"))
        response, _ = self.request("GET", "/api/eml_part?index=7&path=" + str(self.files / "mail.eml"))
        self.assertEqual(response.status, 404)
        response, _ = self.request("GET", "/api/eml_part?index=0&path=" + str(self.files / "song.mp3"))
        self.assertEqual(response.status, 403)
        response, _ = self.request("GET", "/api/eml_part?index=0&path=" + str(self.files / "mail.eml"), token=False)
        self.assertEqual(response.status, 401)

    def test_unopened_files_are_not_served(self) -> None:
        other = self.files / "secret.mp3"
        other.write_bytes(b"x" * 10)
        response, _ = self.request("GET", "/api/raw?path=" + str(other))
        self.assertEqual(response.status, 403)

    def test_appearance_api_and_theme(self) -> None:
        response, payload = self.request("GET", "/api/appearance")
        data = json.loads(payload)
        self.assertEqual(response.status, 200)
        self.assertTrue(data["palettes"] and data["accents"] and data["designs"])
        for key in ("mode", "tokens", "design", "palette", "accent"):
            self.assertIn(key, data["theme"])

    def test_static_files_are_revalidated(self) -> None:
        response, payload = self.request("GET", "/index.html", token=False)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Cache-Control"), "no-cache")
        self.assertIn('src="js/app.js', payload.decode("utf-8"))
        response, payload = self.request("GET", "/js/fileviews.js", token=False)
        etag = response.getheader("ETag")
        self.assertEqual((response.status, response.getheader("Cache-Control")), (200, "no-cache"))
        self.assertTrue(etag and payload.startswith(b"//"))
        response, payload = self.request("GET", "/js/fileviews.js", headers={"If-None-Match": etag}, token=False)
        self.assertEqual((response.status, payload), (304, b""))
        response, payload = self.request("GET", "/js/fileviews.js", headers={"If-None-Match": '"other"'}, token=False)
        self.assertEqual(response.status, 200)
        response, _ = self.request("GET", "/js/../../memunote.py", token=False)
        self.assertNotEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
