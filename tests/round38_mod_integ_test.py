"""Round 38 (r38-mod-integ): modules integrate with MemuAI — panels, commands for the AI, widgets.

Unit part (no network): the ``contributes`` schema, the MemuMusic manifest, phrase matching, the skill prompt, the
panel document, command routing / confirmation, the panel kind «module». Live part (real playground in a subprocess +
Chromium, ffmpeg tones): the gallery shows module panels, a «Сейчас играет» panel mounts and survives re-renders, a
command started from the AI plays in the main window, the chat mini player, Quick Access card + equalizer ring.
Screenshots: R38_SHOTS=<dir>.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    sync_playwright = None

CHROMIUM = next((c for c in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome", shutil.which("chromium") or "")
                 if c and Path(c).exists()), "")
SHOTS = os.environ.get("R38_SHOTS", "")

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

class ContributesSchemaTest(unittest.TestCase):
    def setUp(self):
        from app import module_integration as mi
        self.mi = mi

    def test_music_manifest_is_valid(self):
        from app import modules_host as mh
        manifest = json.loads((mh.builtin_dir() / "music" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(self.mi.validate_contributes(manifest["contributes"], builtin=True, folder=mh.builtin_dir() / "music"), [])
        clean = self.mi.normalize_contributes(manifest["contributes"], builtin=True)
        self.assertEqual([p["id"] for p in clean["panels"]], ["now-playing", "playlist", "stats"])
        self.assertTrue({"play", "pause", "next", "playlist", "now", "download"} <= {c["id"] for c in clean["commands"]})
        self.assertEqual({w["slot"] for w in clean["widgets"]}, {"quick.ring", "quick.card", "chat.composer", "main.dock", "chat.cards"})

        self.assertIn("contributes", mh.normalize_manifest(manifest, module_id="music"))

    def test_custom_module_cannot_use_server_handlers(self):
        errors = []
        clean = self.mi.normalize_contributes({"commands": [
            {"id": "evil", "run": {"handler": "music.play"}},
            {"id": "open-me", "run": {"open": True}, "args": {"type": "object", "properties": {"q": {"type": "string"}}}},
        ], "panels": [{"id": "x", "entry": "../../etc/passwd.html"}], "widgets": [{"id": "w", "slot": "nowhere", "kind": "equalizer"}]},
            builtin=False, errors=errors)
        self.assertEqual([c["id"] for c in clean["commands"]], ["open-me"])
        self.assertNotIn("panels", clean)
        self.assertNotIn("widgets", clean)
        self.assertGreaterEqual(len(errors), 3)

    def test_validator_reports_contributes(self):
        from app import modules_validate as mv
        report = mv.validate_package({"manifest": {"id": "my-mod", "title": "X", "contributes": {
            "panels": [{"id": "p", "entry": "integration/p.html"}]}}, "files": {"index.html": "<div></div>", "main.js": ""}},
            check_syntax=False)
        text = json.dumps(report, ensure_ascii=False)
        self.assertIn("integration/p.html", text)

    def test_phrase_regex(self):
        compiled, names = self.mi._phrase_regex("включи трек {query}")
        self.assertEqual(names, ["query"])
        self.assertEqual(compiled.match("Включи  трек Believer!").group(1), "Believer")
        self.assertIsNone(compiled.match("включи свет"))

class EnabledMusicTest(unittest.TestCase):
    """Module catalogue in a temporary profile with MemuMusic switched on."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "modules"
        from app import modules_host as mh
        mh.configure_module(cls.root, "music", enabled=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_contributions_and_matching(self):
        from app import module_integration as mi
        rows = mi.contributions(self.root)
        self.assertEqual([r["module"] for r in rows], ["music"])
        self.assertEqual(mi.match_phrase("Включи трек Imagine Dragons — Believer", self.root),
                         {"module": "music", "command": "play", "args": {"query": "Imagine Dragons — Believer"}})
        self.assertEqual(mi.match_phrase("следующий трек", self.root)["command"], "next")
        self.assertEqual(mi.match_phrase("включи музыку", self.root)["command"], "resume")
        self.assertIsNone(mi.match_phrase("включи свет в комнате", self.root))
        prompt = mi.skill_prompt(self.root, lang="en")
        self.assertIn("module_command", prompt)
        self.assertIn("music.play", prompt)
        self.assertIn("local music library", prompt)

    def test_disabled_module_contributes_nothing(self):
        from app import module_integration as mi, modules_host as mh
        root = Path(self.tmp.name) / "other"
        mh.configure_module(root, "music", enabled=False)
        self.assertEqual(mi.contributions(root), [])
        self.assertEqual(mi.skill_prompt(root), "")
        result = asyncio.run(mi.run_command("music", "play", {"query": "x"}, root=root))
        self.assertFalse(result["ok"])

    def test_panel_document_is_sandboxed(self):
        from app import module_integration as mi
        page = mi.panel_document("music", "now-playing", root=self.root)
        self.assertIn("connect-src 'none'", page)
        self.assertIn("M.panel = {", page)
        self.assertIn("mx-skel", page)
        self.assertIn('"now-playing"', page)
        with self.assertRaises(KeyError):
            mi.panel_document("music", "nope", root=self.root)

    def test_widget_settings(self):
        from app import module_integration as mi
        self.assertEqual(mi.item_settings("music", "widget", "eq-ring", root=self.root), {"enabled": True, "style": "bars", "color": "cover"})
        saved = mi.save_item_settings("music", "widget", "eq-ring", {"style": "wave", "color": "nope", "junk": 1}, root=self.root)
        self.assertEqual(saved, {"enabled": True, "style": "wave", "color": "cover"})

    def test_command_validation_and_confirmation(self):
        from app import module_integration as mi
        bad = asyncio.run(mi.run_command("music", "volume", {}, root=self.root))
        self.assertFalse(bad["ok"])
        self.assertIn("level", bad["error"])
        unknown = asyncio.run(mi.run_command("music", "explode", {}, root=self.root))
        self.assertIn("play", unknown["error"])

        row = mi.find("music", root=self.root)
        cmd = dict(row["commands"][0], danger=True)
        orig = mi.find
        try:
            mi.find = lambda module_id, root=None: dict(row, commands=[cmd])
            out = asyncio.run(mi.run_command("music", cmd["id"], {"query": "x"}, root=self.root))
            self.assertTrue(out["needs_confirm"])
        finally:
            mi.find = orig

class MusicHandlersTest(unittest.TestCase):
    """music.play / playlist / now against a real temporary library (no audio decoding needed)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = {k: os.environ.get(k) for k in ("MEMUAI_DATA_DIR",)}
        os.environ["MEMUAI_DATA_DIR"] = cls.tmp.name
        from app.music import library
        library._LIBS.clear() if hasattr(library, "_LIBS") else None
        cls.lib = library.get_library()
        folder = Path(cls.tmp.name) / "f"
        folder.mkdir()
        cls.ids = []
        for title, artist in (("Believer", "Imagine Dragons"), ("Thunder", "Imagine Dragons"), ("Numb", "Linkin Park")):
            path = folder / f"{title}.opus"
            path.write_bytes(b"0")
            cls.ids.append(cls.lib.add({"file": str(path), "title": title, "artist": artist, "duration": 180})["id"])
        cls.pl = cls.lib.create_playlist("Утро", cls.ids[:2], "")
        cls.root = Path(cls.tmp.name) / "modules"
        from app import modules_host as mh
        mh.configure_module(cls.root, "music", enabled=True)

    @classmethod
    def tearDownClass(cls):
        cls.lib.close()
        from app.music import library
        with library._LIBS_LOCK:
            library._LIBS = {key: value for key, value in library._LIBS.items() if value is not cls.lib}
        for key, value in cls.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.tmp.cleanup()

    def run_cmd(self, command, args=None):
        from app import module_integration as mi
        return asyncio.run(mi.run_command("music", command, args or {}, root=self.root))

    def test_play_finds_track_and_queues_artist(self):
        from app.music.player import hub
        out = self.run_cmd("play", {"query": "imagine dragons believer"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["card"]["title"], "Believer")
        self.assertTrue(out["client"]["music_boot"])
        command = hub(self.lib.root).commands[-1]
        self.assertEqual(command["cmd"], "play_track")
        self.assertEqual(command["args"]["track_id"], self.ids[0])
        self.assertIn(self.ids[1], command["args"]["queue"])
        typo = self.run_cmd("play", {"query": "beliver"})
        self.assertTrue(typo["ok"], typo)
        self.assertEqual(typo["card"]["title"], "Believer")
        missing = self.run_cmd("play", {"query": "zzzz qqqq"})
        self.assertFalse(missing["ok"])
        self.assertTrue(missing["not_found"])

    def test_playlist_and_controls(self):
        listing = self.run_cmd("playlist", {})
        self.assertEqual(listing["card"]["kind"], "list")
        out = self.run_cmd("playlist", {"name": "утро"})
        self.assertTrue(out["ok"], out)
        self.assertIn("Утро", out["text"])
        self.assertTrue(self.run_cmd("next")["ok"])
        self.assertTrue(self.run_cmd("volume", {"level": 40})["ok"])
        self.assertIn("ничего", self.run_cmd("now")["text"].lower())

    def test_download_rejects_non_links(self):
        out = self.run_cmd("download", {"url": "not a link"})
        self.assertFalse(out["ok"])

class PanelKindTest(unittest.TestCase):
    def test_module_block_and_kind(self):
        from app import panels
        clean, errors = panels.validate_spec({"blocks": [{"type": "module", "module": "music", "panel": "now-playing"},
                                                         {"type": "module", "module": "Bad Id", "panel": "x"}]})
        self.assertEqual(clean["blocks"], [{"type": "module", "module": "music", "panel": "now-playing"}])
        self.assertTrue(errors)
        self.assertIn("module", panels.KINDS)
        self.assertIn("module", panels.LOCAL_KINDS)

class StaticFrontTest(unittest.TestCase):
    def test_js_syntax_and_wiring(self):
        node = shutil.which("node")
        files = ["app/pgstatic/module-integration.js", "app/pgstatic/quick/plugins/modules.js",
                 "app/module_builtins/music/integration/panels.js", "app/pgstatic/panels.js", "app/pgstatic/music-dock.js"]
        if node:
            for name in files:
                subprocess.run([node, "--check", str(ROOT / name)], check=True)
        index = (ROOT / "app/pgstatic/index.html").read_text(encoding="utf-8")
        self.assertLess(index.index("/music-dock.js"), index.index("/module-integration.js"))
        plugins = json.loads((ROOT / "app/pgstatic/quick/plugins/index.json").read_text(encoding="utf-8"))["plugins"]
        order = [p.get("id") for p in plugins]
        self.assertLess(order.index("modules"), order.index("commands"))
        for spec in ("MemuAI.spec", "MemuAIProxy.spec"):
            self.assertIn("app.module_integration_quick", (ROOT / spec).read_text(encoding="utf-8"))
        perf = (ROOT / "app/pgstatic/quick/plugins/modules.js").read_text(encoding="utf-8")
        self.assertNotIn("setInterval", perf)
        self.assertIn("document.hidden", perf)
        main = (ROOT / "app/pgstatic/module-integration.js").read_text(encoding="utf-8")
        self.assertNotIn("setInterval", main)
        self.assertNotIn("MutationObserver", main)

@unittest.skipUnless(sync_playwright and CHROMIUM and shutil.which("ffmpeg"), "needs playwright, Chromium and ffmpeg")
class LiveIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        tones = root / "tones"
        tones.mkdir()
        for i, freq in enumerate((330, 440, 550)):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=60",
                            "-c:a", "libopus", "-b:a", "48k", str(tones / f"tone{i}.opus")], check=True)
        cls.port = _free_port()
        env = dict(os.environ, MEMUAI_DATA_DIR=str(root / "data"), MEMUAI_PROXY_URL="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")

        subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from app import pgdata; "
                        "pgdata.save_preferences({'profile_onboarded': True, 'profile_name': 'Test'})" % str(ROOT)], env=env, check=True)
        code = ("import sys, uvicorn; sys.path.insert(0, %r); from app.playground_api import create_app; "
                "uvicorn.run(create_app(), host='127.0.0.1', port=%d, log_level='error')") % (str(ROOT), cls.port)
        cls.proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.http = httpx.Client(trust_env=False, timeout=30)
        for _ in range(240):
            try:
                if cls.http.get(cls.base + "/api/pg/modules").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        cls.http.post(cls.base + "/api/pg/modules/configure", json={"id": "music", "enabled": True})
        cls.call("import.folder", {"path": str(tones)})
        for _ in range(60):
            if (cls.call("library.list", {}) or {}).get("total") == 3:
                break
            time.sleep(0.5)
        names = [("Northern Lights", "Aurora Fields", "#3aa7a3"), ("Golden Hour", "Kai Lumen", "#d9a441"), ("Night Drive", "Neon Avenue", "#8b6cf0")]
        tracks = sorted(cls.call("library.list", {"sort": "title"})["tracks"], key=lambda t: t["title"])
        for track, (title, artist, accent) in zip(tracks, names):
            cls.call("library.update", {"id": track["id"], "title": title, "artist": artist, "accent": accent})
        cls.call("playlist.create", {"name": "Вечер", "track_ids": [t["id"] for t in tracks]})
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        cls.page = cls.browser.new_page(viewport={"width": 1360, "height": 860})
        cls.errors = []
        cls.page.on("pageerror", lambda exc: cls.errors.append(str(exc)))
        cls.page.goto(cls.base + "/")
        cls.poll(cls.page, "() => !!(window.MemuModInt && MemuModInt.state().modules >= 1 && window.MemuPanels && window.MemuMusicDock)", 60000)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(5)
        cls.tmp.cleanup()

    @classmethod
    def call(cls, op, args):
        return cls.http.post(cls.base + "/api/pg/music/call", json={"op": op, "args": args}).json().get("result")

    @staticmethod
    def poll(target, script, timeout=20000):
        """wait_for_function evaluates a string — the MemuAI page CSP has no 'unsafe-eval'; evaluate() works."""
        for _ in range(timeout // 200):
            try:
                if target.evaluate(script):
                    return True
            except Exception:  # noqa: BLE001 - page still loading
                pass
            time.sleep(0.2)
        raise TimeoutError(script)

    def shot(self, page, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / name))

    def test_1_gallery_panel_and_command(self):
        page = self.page
        page.evaluate("() => MemuPanels.openGallery()")
        page.wait_for_selector('[data-modpanel-add="music:now-playing"]', timeout=10000)
        self.shot(page, "01-gallery.png")
        page.click('[data-modpanel-add="music:now-playing"]')
        page.wait_for_selector('.mmi-slot[data-mmi="music:now-playing"] iframe.mmi-frame.ready', timeout=20000)
        frame_el = page.query_selector('.mmi-slot[data-mmi="music:now-playing"] iframe')
        frame = frame_el.content_frame()
        frame.wait_for_selector(".mx-empty, .mx-np", timeout=10000)

        out = page.evaluate("""async () => {
            const r = await MemuModInt.runTool({ tool: "module_command", args: { module: "music", command: "play", args: { query: "golden hour" } } });
            return r; }""")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["card"]["title"], "Golden Hour")
        self.poll(page, "() => MemuMusicDock.state().booted && !MemuMusicDock.state().paused", 20000)
        frame.wait_for_selector(".mx-np .mx-title", timeout=20000)
        self.poll(frame, "() => document.querySelector('.mx-title').textContent === 'Golden Hour'", 20000)

        self.http.post(self.base + "/api/pg/modint/settings", json={"module": "music", "kind": "widget", "id": "chat-mini", "values": {"enabled": True}})
        page.evaluate("() => MemuModInt.client({ modint_reload: true })")
        page.wait_for_selector(".mmi-chat-np:not([hidden])", timeout=20000)

        page.evaluate("() => { window.__mark = document.querySelector('.mmi-frame'); MemuPanels.render(); MemuPanels.rerender(() => true); }")
        self.assertTrue(page.evaluate("() => document.querySelector('.mmi-slot .mmi-frame') === window.__mark"))
        time.sleep(1.5)
        self.shot(page, "02-home-panel-and-miniplayer.png")

        page.evaluate("() => MemuModInt.addPanel('music:playlist', 'home')")
        page.wait_for_selector('.mmi-slot[data-mmi="music:playlist"] iframe.mmi-frame.ready', timeout=20000)
        pl = page.query_selector('.mmi-slot[data-mmi="music:playlist"] iframe').content_frame()
        pl.wait_for_selector(".mx-chip", timeout=25000)
        page.evaluate("() => MemuModInt.addPanel('music:stats', 'home')")
        page.wait_for_selector('.mmi-slot[data-mmi="music:stats"] iframe.mmi-frame.ready', timeout=20000)
        time.sleep(1.5)
        page.evaluate("() => document.querySelector('#panels-home')?.scrollIntoView()")
        self.shot(page, "03-home-three-panels.png")
        self.assertEqual([e for e in self.errors if "ResizeObserver" not in e], [])

    def test_2_quick_access_card_and_ring(self):
        page = self.browser.new_page(viewport={"width": 760, "height": 560})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(self.base + "/quick/")
        self.poll(page, "() => window.MemuQuickModules && MemuQuickModules.state().phrases > 5", 30000)
        self.assertEqual(page.evaluate("() => MemuQuickModules.match('Включи трек Night Drive')"),
                         {"module": "music", "command": "play", "danger": False, "args": {"query": "Night Drive"}})
        page.fill("#qa-input", "включи трек night drive")
        page.press("#qa-input", "Enter")
        page.wait_for_selector(".qmod-card-player [data-np-root]", timeout=15000)
        self.poll(page, "() => document.querySelector('.qmod-card-player .qmod-np-title').textContent === 'Night Drive'", 10000)

        page.evaluate("""() => MemuQuickModules.setPlayer({ track_id: 'x', playing: true, stale: false,
             track: { id: 'x', title: 'Night Drive', artist: 'Neon Avenue', accent: '#8b6cf0' } })""")
        page.wait_for_selector("canvas.qmod-ring", timeout=5000)
        time.sleep(0.6)
        self.shot(page, "04-quick-card-ring.png")
        self.assertTrue(page.evaluate("() => MemuQuickModules.state().raf"))
        page.evaluate("() => MemuQuickModules.setPlayer({ track_id: 'x', playing: false })")
        self.poll(page, "() => !MemuQuickModules.state().ring", 2000)
        self.assertEqual(errors, [])
        page.close()

if __name__ == "__main__":
    unittest.main()
