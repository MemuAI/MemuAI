"""Round 33 (#15): video guides next to the in-app instructions + accuracy of the instructions themselves.

Static checks (files, sizes, captions, wiring, spec/build coverage, corrected texts) and a browser check of the
player in MemuAI and MemuAI Proxy (skipped when playwright/chromium are missing)."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PG = ROOT / "app" / "pgstatic"
PX = ROOT / "app" / "static"
GUIDES = ["telegram-bot", "google-client", "github-token", "notion-connection", "proxy-api-key"]

def guide_table():
    text = (PG / "guide-video.js").read_text(encoding="utf-8")
    block = text[text.index("const GUIDES = {"):text.index("};", text.index("const GUIDES = {"))]
    return dict(re.findall(r'"([a-z-]+)": \{[^}]*seconds: (\d+)', block))

def read_vtt(path):
    text = path.read_text(encoding="utf-8")
    cues = re.findall(r"(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})[^\n]*\n(.+)", text)
    to_s = lambda v: int(v[:2]) * 3600 + int(v[3:5]) * 60 + float(v[6:])
    return text, [(to_s(a), to_s(b), body.strip()) for a, b, body in cues]

class GuideFilesTest(unittest.TestCase):
    def test_player_files_identical_in_both_apps(self):
        for name in ("guide-video.js", "guide-video.css"):
            self.assertTrue((PG / name).is_file(), name)
            self.assertEqual((PG / name).read_bytes(), (PX / name).read_bytes(), f"{name} must be the same in pgstatic and static")

    def test_manifest_lists_every_clip(self):
        self.assertEqual(sorted(guide_table()), sorted(GUIDES))

    def test_clips_exist_small_and_identical(self):
        total = 0
        for gid in GUIDES:
            for base in (PG, PX):
                clip = base / "guides" / f"{gid}.webm"
                self.assertTrue(clip.is_file(), clip)
                self.assertLess(clip.stat().st_size, 1_500_000, f"{clip} is larger than 1.5 MB")
                self.assertEqual(clip.read_bytes()[:4], b"\x1a\x45\xdf\xa3", f"{clip} is not WebM/Matroska")
            self.assertEqual((PG / "guides" / f"{gid}.webm").read_bytes(), (PX / "guides" / f"{gid}.webm").read_bytes())
            total += (PG / "guides" / f"{gid}.webm").stat().st_size
        self.assertLess(total, 10_000_000)

    def test_clip_duration_matches_manifest(self):
        if not shutil.which("ffprobe"):
            self.skipTest("ffprobe is not installed")
        table = guide_table()
        for gid in GUIDES:
            out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
                                           "-of", "json", str(PG / "guides" / f"{gid}.webm")])
            info = json.loads(out)
            duration = float(info["format"]["duration"])
            stream = info["streams"][0]
            self.assertIn(stream["codec_name"], ("vp9", "vp8", "av1"))
            self.assertLessEqual(stream["width"], 1280)
            self.assertLessEqual(stream["height"], 720)
            self.assertTrue(10 <= duration <= 45, f"{gid}: {duration:.1f}s")
            self.assertLessEqual(abs(duration - int(table[gid])), 2, f"{gid}: manifest says {table[gid]}s, clip is {duration:.1f}s")

    def test_captions_both_languages_aligned(self):
        for gid in GUIDES:
            for base in (PG, PX):
                ru_text, ru = read_vtt(base / "guides" / f"{gid}.ru.vtt")
                en_text, en = read_vtt(base / "guides" / f"{gid}.en.vtt")
                self.assertTrue(ru_text.startswith("WEBVTT") and en_text.startswith("WEBVTT"))
                self.assertGreaterEqual(len(ru), 4, gid)
                self.assertEqual([c[:2] for c in ru], [c[:2] for c in en], f"{gid}: RU and EN cues must share timings")
                for start, end, _ in ru:
                    self.assertLess(start, end)
                self.assertTrue(all(re.search("[а-яё]", c[2], re.I) for c in ru), f"{gid}: RU captions must be Russian")
                self.assertFalse(any(re.search("[а-яё]", c[2], re.I) for c in en), f"{gid}: EN captions must contain no Cyrillic")

    def test_no_secrets_in_captions(self):
        for path in list((PG / "guides").glob("*.vtt")):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"sk-or-v1-[0-9a-f]{20,}|ghp_[A-Za-z0-9]{30,}|\d{8,10}:[A-Za-z0-9_-]{30,}")

class WiringTest(unittest.TestCase):
    def test_script_and_style_tags(self):
        for index in (PG / "index.html", PX / "index.html"):
            html = index.read_text(encoding="utf-8")
            self.assertIn('/guide-video.js?v=3.5-r33', html, index)
            self.assertIn('/guide-video.css?v=3.5-r33', html, index)

    def test_buttons_next_to_instructions(self):
        expect = {
            PG / "telegram-settings.js": ['MemuGuideVideo.button("telegram-bot"'],
            PG / "integrations-ui.js": ['MemuGuideVideo.button("google-client"', 'MemuGuideVideo.button("github-token"', 'MemuGuideVideo.button("notion-connection"'],
            PG / "sync-settings.js": ['MemuGuideVideo.button("google-client"'],
            PX / "proxy-catalog-r33.js": ['MemuGuideVideo.button("proxy-api-key"'],
        }
        for path, needles in expect.items():
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{path.name}: {needle}")
                self.assertIn("window.MemuGuideVideo", text, f"{path.name}: the button must be guarded")

    def test_shipped_by_spec_and_build(self):
        """The guides dirs are inside app/pgstatic and app/static, which every spec and build.py ship whole."""
        mem = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        prx = (ROOT / "MemuAIProxy.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("('app/pgstatic', 'app/pgstatic')", mem)
        self.assertIn("('app/static', 'app/static')", prx)
        self.assertIn("('app/pgstatic', 'app/pgstatic')", prx)
        self.assertIn('"app/static;app/static"', build)
        self.assertIn('"app/pgstatic;app/pgstatic"', build)

class AccuracyTest(unittest.TestCase):
    def test_cloudflare_path_is_current(self):
        for path in (PG / "tgapp-settings.js", PX / "public.js", ROOT / "app/docs/ru/telegram-miniapp.md", ROOT / "app/docs/en/telegram-miniapp.md",
                     ROOT / "app/docs/ru/proxy-public.md", ROOT / "app/docs/en/proxy-public.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Zero Trust → Networks → Tunnels", text, path)
            self.assertIn("Networking → Tunnels", text, path)

    def test_google_sync_docs_use_google_auth_platform(self):
        for lang in ("ru", "en"):
            text = (ROOT / f"app/docs/{lang}/sync.md").read_text(encoding="utf-8")
            self.assertNotIn("OAuth consent screen", text)
            self.assertNotIn("Create credentials", text)
            for needle in ("Google Auth Platform", "Audience", "Test users", "Create client", "Desktop app"):
                self.assertIn(needle, text, f"{lang}: {needle}")

    def test_notion_developer_portal(self):
        ui = (PG / "integrations-ui.js").read_text(encoding="utf-8")
        self.assertIn("https://app.notion.com/developers", ui)
        self.assertIn("Internal connections", ui)
        self.assertIn("+ Add connection", ui)
        for lang in ("ru", "en"):
            text = (ROOT / f"app/docs/{lang}/integrations.md").read_text(encoding="utf-8")
            self.assertIn("app.notion.com/developers", text)
            self.assertNotIn("Internal Integration Secret", text)

    def test_catalog_links_fixed(self):
        text = (ROOT / "app/providers/catalog.py").read_text(encoding="utf-8")
        self.assertNotIn("poe.com/api_key", text)
        self.assertNotIn("platform.reka.ai/apikeys", text)

    def test_named_tunnel_accepts_pasted_install_command(self):
        from app.public_tunnel import _token_from_paste
        token = "eyJhIjoiMTIzNDU2Nzg5MGFiY2RlZiIsInQiOiJhYmMifQ=="
        self.assertEqual(_token_from_paste(token), token)
        self.assertEqual(_token_from_paste(f"cloudflared.exe service install {token}"), token)
        self.assertEqual(_token_from_paste(f"sudo cloudflared service install {token}"), token)
        self.assertEqual(_token_from_paste(f"cloudflared tunnel run --token {token}"), token)
        self.assertEqual(_token_from_paste(f'  "{token}"  '), token)

class PlayerBrowserTest(unittest.TestCase):
    """The player opens from a button, shows RU/EN captions as tracks, lists the steps and closes on Escape."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        if not Path("/usr/bin/chromium").exists():
            raise unittest.SkipTest("chromium is not installed")

    def _serve(self, directory):
        import threading, uvicorn, socket
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
        app = FastAPI()
        app.mount("/", StaticFiles(directory=str(directory), html=True))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        import time
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        self.addCleanup(lambda: setattr(server, "should_exit", True))
        return f"http://127.0.0.1:{port}"

    def _check(self, base, lang, gid):
        from playwright.sync_api import sync_playwright
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/chromium")
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/guide-video.css")
            page.set_content(f"""<html lang="{lang}"><head><link rel="stylesheet" href="{base}/guide-video.css"></head>
              <body><div id="host"></div><script src="{base}/guide-video.js"></script></body></html>""")
            page.wait_for_function("!!window.MemuGuideVideo")
            page.evaluate(f"document.getElementById('host').innerHTML = MemuGuideVideo.button('{gid}')")
            label = page.inner_text(".mgv-btn")
            self.assertIn("Video guide" if lang == "en" else "Видео-инструкция", label)
            page.click(".mgv-btn")
            page.wait_for_selector(".mgv-dialog")
            page.wait_for_function("document.querySelectorAll('.mgv-step').length >= 4", timeout=15000)
            page.wait_for_function("document.querySelector('.mgv-video').readyState >= 1", timeout=15000)
            info = page.evaluate("""() => { const v = document.querySelector('.mgv-video');
              return {dur: v.duration, tracks: [...v.textTracks].map(t => [t.language, t.mode]),
                      title: document.getElementById('mgv-title').textContent,
                      steps: [...document.querySelectorAll('.mgv-step')].map(s => s.textContent)}; }""")
            self.assertGreater(info["dur"], 5)
            self.assertIn([lang, "showing"], info["tracks"])
            self.assertEqual(len(info["tracks"]), 2)
            cyr = any(re.search("[а-яё]", s, re.I) for s in info["steps"] + [info["title"]])
            self.assertEqual(cyr, lang == "ru")
            page.click(".mgv-step >> nth=2")
            t = page.evaluate("document.querySelector('.mgv-video').currentTime")
            self.assertGreater(t, 1)
            page.keyboard.press("Escape")
            page.wait_for_selector(".mgv-backdrop", state="detached")
            self.assertEqual(errors, [])
            browser.close()

    def test_player_memuai_ru(self):
        self._check(self._serve(PG), "ru", "telegram-bot")

    def test_player_proxy_en(self):
        self._check(self._serve(PX), "en", "proxy-api-key")

if __name__ == "__main__":
    unittest.main()
