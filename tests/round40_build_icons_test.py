"""Round 40 (r40-build-icons): a faster installer build (п.9) and module app icons that really change (п.10).

Build: MemuNote / MemuFiles no longer drag half of ``app.*`` and anthropic / pygments / rich into their
PyInstaller analysis (static import graph — the same edges PyInstaller follows, lazy imports included);
every exclude is reached only behind a guard; spec files match build.py; per-program input stamps; stage and
PyInstaller phase timings.

Icons: built-ins ship a per-size ``icon.ico``; an app ``.ico`` gets a new path when its picture changes
(Explorer caches icons by path); installed apps are migrated on start (repair); shortcuts made by Windows when
pinning are re-pointed; the open window and its relaunch icon follow. No network, no Windows needed.
"""
from __future__ import annotations

import ast
import base64
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build  # noqa: E402
from app import module_apps, module_apps_win, module_icons, modules_host  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import import_graph  # noqa: E402

HEAVY = ("anthropic", "pygments", "rich", "prompt_toolkit", "yt_dlp", "fastapi", "uvicorn", "curl_cffi",
         "websockets", "numpy", "onnxruntime")


def _graph(key):
    target = build.TARGETS[key]
    return import_graph.analyse(target["entry"], list(target["hidden"]) + list(target.get("collect", [])),
                                target.get("exclude", []))


class LightTargetsTest(unittest.TestCase):
    def test_memunote_and_memufiles_stay_light(self):
        for key, limit in (("memunote", 70), ("memufiles", 110)):
            result = _graph(key)
            heavy = sorted(set(HEAVY) & set(result["external"]))
            self.assertEqual(heavy, [], f"{key} reaches {heavy}: " + "; ".join(
                " <- ".join(import_graph.chain(result, name)) for name in heavy))
            self.assertLess(len(result["app"]), limit, f"{key}: {len(result['app'])} app modules")
            for name in ("app.cli.main", "app.playground_api", "app.admin_api", "app.music.api", "app.inbox_hooks"):
                self.assertNotIn(name, result["app"], key)

    def test_the_heavy_apps_keep_their_stacks(self):
        self.assertIn("app.playground_api", _graph("memuai")["app"])
        self.assertIn("anthropic", _graph("memucode")["external"])

    def test_runtime_imports_of_memunote_are_bundled(self):
        result = _graph("memunote")
        for name in ("app.memory_v2", "app.memory_assoc", "app.token_estimate", "app.bigdocs", "app.memunote.server",
                     "app.memunote.desktop", "app.model_names", "app.storage", "app.themes", "app.version"):
            self.assertIn(name, result["app"])
        for name in ("webview", "PIL", "pypdf", "spylls"):
            self.assertIn(name, result["external"])

    def test_inbox_hooks_is_only_reached_guarded(self):
        """app.inbox_hooks is excluded from MemuNote/MemuFiles: every import of it in what they bundle is guarded."""
        import round30_memunote_test as r30
        for key in ("memunote", "memufiles"):
            for name in _graph(key)["app"]:
                path = r30.module_file(name)
                for target, guarded, _top in r30.imports_of(name, path):
                    if target == "app.inbox_hooks" or target.startswith("app.inbox_hooks."):
                        self.assertTrue(guarded, f"{name} imports app.inbox_hooks unguarded ({key})")

    def test_httpx_cli_exclude_is_safe(self):
        try:
            import httpx
        except ImportError:
            self.skipTest("httpx not installed")
        tree = ast.parse(Path(httpx.__file__).read_text(encoding="utf-8"))
        guarded = [node for node in ast.walk(tree) if isinstance(node, ast.Try)
                   and any(isinstance(child, ast.ImportFrom) and child.module == "_main" for child in node.body)]
        self.assertTrue(guarded, "httpx/__init__ imports ._main in try/except ImportError")
        self.assertIn("httpx._main", build.TARGETS["memunote"]["exclude"])

    def test_specs_match_build_py(self):
        for key, spec_name in (("memunote", "MemuNote.spec"), ("memufiles", "MemuFiles.spec")):
            tree = ast.parse((ROOT / spec_name).read_text(encoding="utf-8"))
            excludes = hidden = None
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "excludes":
                    excludes = ast.literal_eval(node.value)
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "hiddenimports":
                    hidden = ast.literal_eval(node.value)
            self.assertEqual(excludes, build.TARGETS[key]["exclude"], spec_name)
            self.assertEqual(sorted(hidden), sorted(build.TARGETS[key]["hidden"]), spec_name)
            self.assertIn("upx=False", (ROOT / spec_name).read_text(encoding="utf-8"))


class BuildScriptTest(unittest.TestCase):
    def test_per_program_inputs(self):
        inputs = {p.relative_to(ROOT).as_posix() for p in build.target_inputs(build.TARGETS["memunote"])}
        self.assertIn("app/memunote/server.py", inputs)
        self.assertIn("app/memory_v2.py", inputs)
        self.assertIn("memunote.py", inputs)
        self.assertTrue(any(p.startswith("app/memunote/static/") for p in inputs))
        self.assertFalse(any(p.startswith("app/pgstatic/") for p in inputs), "the chat UI does not rebuild MemuNote")
        self.assertNotIn("app/playground_api.py", inputs)
        proxy_only = {p.relative_to(ROOT).as_posix() for p in build.target_inputs(build.TARGETS["proxy_only"])}
        self.assertTrue(any(p.startswith("app/static/") for p in proxy_only), "generated assets map to their sources")

    def test_stamp_follows_own_files_only(self):
        target = build.TARGETS["memunote"]
        with mock.patch.object(build, "runtime_fingerprint", lambda: "py"):
            real = build.file_hash
            first = build.inputs_hash("memunote3.6", target)
            with mock.patch.object(build, "file_hash", lambda paths: real(
                    [p for p in paths if "pgstatic" not in p.parts])):
                self.assertEqual(build.inputs_hash("memunote3.6", target), first)
            self.assertNotEqual(build.inputs_hash("memunote3.7", target), first)

    def test_phase_timings(self):
        log = ("5212 INFO: Analyzing modules for base_library.zip ...\n7957 INFO: Analyzing memunote.py\n"
               "40000 INFO: Processing module hooks (post-graph stage)...\n52000 INFO: Building PYZ (ZlibArchive) x\n"
               "56000 INFO: Building EXE from EXE-00.toc\n57000 INFO: Building COLLECT COLLECT-00.toc\n"
               "61000 INFO: Build complete!\n")
        phases = dict(build.pyinstaller_phases(log))
        self.assertAlmostEqual(phases["анализ импортов"], 40.0 - 5.212, places=2)
        self.assertAlmostEqual(phases["PYZ"], 4.0, places=2)
        self.assertAlmostEqual(phases["COLLECT"], 4.0, places=2)
        self.assertEqual(build.pyinstaller_phases(""), [])

    def test_stages_jobs_and_order(self):
        build.STAGES.clear()
        with build.stage("x"):
            pass
        self.assertEqual(build.STAGES[0][0], "x")
        build.STAGES.clear()
        source = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('"--jobs"', source)
        self.assertIn('"--log-level", "INFO"', source)
        self.assertIn("--noupx", source)
        self.assertIn('["memuai", "proxy", "memucode", "memufiles", "memunote", "memu"]', source, "longest builds first")


# ------------------------------------------------------------------------------------------------- icons

class FakeShell(module_apps.Shell):
    def __init__(self, base: Path) -> None:
        self.base, self.calls, self.notified, self.retargeted = base, [], [], []

    def available(self) -> bool:
        return True

    def folder(self, name: str):
        path = self.base / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def shortcut(self, link, **kwargs) -> bool:
        self.calls.append((Path(link), kwargs))
        Path(link).write_text(json.dumps({k: str(v) for k, v in kwargs.items()}), encoding="utf-8")
        return True

    def retarget_icons(self, module_id, icon, icon_dir):
        self.retargeted.append((module_id, icon))
        return []

    def notify(self, paths):
        self.notified.append(list(paths))


def _png_url(color=(20, 160, 90, 255), size=256) -> str:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class BuiltinIcoTest(unittest.TestCase):
    def test_every_builtin_ships_a_per_size_ico(self):
        from PIL import Image
        for svg in sorted(modules_host.builtin_dir().glob("*/icon.svg")):
            ico = svg.with_suffix(".ico")
            self.assertTrue(ico.is_file(), f"{ico} (tools/build_module_icons.py)")
            raw = ico.read_bytes()
            count = int.from_bytes(raw[4:6], "little")
            sizes = []
            for i in range(count):
                entry = raw[6 + 16 * i: 22 + 16 * i]
                size = entry[0] or 256
                offset = int.from_bytes(entry[12:16], "little")
                sizes.append(size)
                is_png = raw[offset:offset + 4] == b"\x89PNG"
                self.assertEqual(is_png, size == 256, f"{ico.parent.name} {size}px: BMP below 256 px")
            self.assertEqual(tuple(sorted(sizes)), module_icons.ICO_SIZES, ico.parent.name)
            with Image.open(ico) as image:
                image.size = (16, 16)
                image.load()
                self.assertGreater(image.convert("RGBA").getpixel((8, 8))[3], 200, "a 16 px frame is not blank")

    def test_app_icon_source_prefers_the_shipped_ico(self):
        tmp = Path(tempfile.mkdtemp(prefix="r40ico-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(module_icons.app_icon_source(tmp, "music").name, "icon.ico")
        self.assertTrue(module_icons.source_signature(module_icons.app_icon_source(tmp, "music")).startswith("file:"))
        self.assertIsNone(module_icons.source_signature(None) or None)

    def test_frames_are_sharpened_small_and_exact(self):
        from PIL import Image
        frames = module_icons.ico_frames(Image.new("RGBA", (256, 256), (255, 0, 0, 255)))
        self.assertEqual(sorted(frames), list(module_icons.ICO_SIZES))
        self.assertEqual(frames[16].size, (16, 16))
        with Image.open(io.BytesIO(module_icons.ico_bytes(frames))) as ico:
            self.assertIn((256, 256), ico.info["sizes"])
            self.assertIn((24, 24), ico.info["sizes"])


class AppIconsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r40apps-"))
        self.root = self.tmp / "modules"
        self.root.mkdir()
        self.shell = FakeShell(self.tmp / "shell")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _custom(self, module_id="glyphy", icon="star"):
        return modules_host.create_module(self.root, {"id": module_id, "title": "Glyphy", "html": "<p>x</p>",
                                                      "js": "", "css": "", "icon": icon})

    def test_new_picture_new_path_and_old_copies_pruned(self):
        self._custom()
        first = module_apps.write_icon("glyphy", {"title": "Glyphy"}, _png_url((10, 10, 200, 255)), self.root)
        second = module_apps.write_icon("glyphy", {"title": "Glyphy"}, _png_url((200, 10, 10, 255)), self.root)
        self.assertNotEqual(first["shell"], second["shell"], "Explorer caches by path: a new picture, a new path")
        self.assertTrue(second["shell"].endswith("glyphy.ico"))
        self.assertFalse(Path(first["shell"]).exists(), "older copies are removed")
        self.assertEqual(Path(second["shell"]).read_bytes(), Path(second["ico"]).read_bytes())
        again = module_apps.write_icon("glyphy", {"title": "Glyphy"}, _png_url((200, 10, 10, 255)), self.root)
        self.assertEqual(again["shell"], second["shell"], "the same picture keeps its path")
        self.assertTrue(second["sig"].startswith("page:"))

    def test_builtin_uses_shipped_ico_bytes(self):
        out = module_apps.write_icon("music", {"title": "MemuMusic"}, _png_url(), self.root)
        shipped = (modules_host.builtin_dir() / "music" / "icon.ico").read_bytes()
        self.assertEqual(Path(out["ico"]).read_bytes(), shipped, "the per-size frames, not the page's 256 px tile")
        self.assertTrue(out["sig"].startswith("file:"))

    def test_install_records_signature_and_links_point_at_the_hashed_copy(self):
        row = module_apps.install("translator", root=self.root, shell=self.shell)
        self.assertTrue(row["icon_sig"].startswith("file:"))
        self.assertIn(f"{Path('shell')}", row["icon_file"])
        for _link, kwargs in self.shell.calls:
            self.assertEqual(kwargs["icon"], row["icon_file"])
        self.assertTrue(self.shell.notified, "Explorer is told about the new shortcuts")
        module_apps.uninstall("translator", root=self.root)
        self.assertEqual(list((module_apps.app_dir(self.root) / "shell").glob("*/translator.ico")), [])

    def test_repair_migrates_apps_installed_before_round_40(self):
        """The user's case: an app installed earlier keeps its old .ico — the start-up repair rebuilds it."""
        module_apps.install("studio", root=self.root, shell=self.shell)
        old = module_apps.app_dir(self.root) / "studio.ico"
        old.write_bytes(b"old icon")

        def pre_r40(data):
            entry = data["apps"]["studio"]
            entry.pop("icon_sig", None)
            entry.pop("icon_file", None)
        module_apps._mutate(pre_r40, self.root)
        self.shell.calls.clear()
        self.assertEqual(module_apps.repair(self.root, self.shell), 1)
        entry = module_apps.load_store(self.root)["apps"]["studio"]
        self.assertTrue(entry["icon_sig"].startswith("file:"))
        self.assertNotEqual(old.read_bytes(), b"old icon")
        self.assertTrue(self.shell.calls and all(kw["icon"] == entry["icon_file"] for _l, kw in self.shell.calls))
        self.assertEqual(self.shell.retargeted[-1], ("studio", entry["icon_file"]), "pinned buttons follow")
        self.assertEqual(module_apps.repair(self.root, self.shell), 0, "nothing to do the next time")

    def test_builtin_icon_update_is_detected(self):
        module_apps.install("design", root=self.root, shell=self.shell)
        entry = module_apps.load_store(self.root)["apps"]["design"]
        self.assertFalse(module_apps.icon_outdated("design", entry, self.root))
        entry["icon_sig"] = "file:0000000000000000:r39"   # MemuAI updated: the built-in's icon file changed
        self.assertTrue(module_apps.icon_outdated("design", entry, self.root))

    def test_user_icon_change_refreshes_the_app(self):
        module_apps.install("translator", root=self.root, shell=self.shell)
        before = module_apps.load_store(self.root)["apps"]["translator"]["icon_file"]
        module_icons.set_user_icon(self.root, "translator", _png_url((250, 200, 0, 255)),
                                   raster=_png_url((250, 200, 0, 255)), kind="file")
        out = module_apps.refresh_app_icon("translator", _png_url((250, 200, 0, 255)), self.root, self.shell)
        entry = module_apps.load_store(self.root)["apps"]["translator"]
        self.assertNotEqual(entry["icon_file"], before)
        self.assertEqual(entry["icon_file"], out["shell"])
        self.assertFalse(Path(before).exists())
        self.assertTrue(all(json.loads(Path(link).read_text())["icon"] == out["shell"] for link in entry["links"]))

    def test_open_window_follows(self):
        class Win:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):
                return lambda *args: self.calls.append((name, args)) or True
        win = Win()
        module_apps.install("translator", root=self.root, shell=self.shell)
        mgr = module_apps.ModuleWindows("http://127.0.0.1:1/", webview_module=object(), win=win, root=self.root)
        mgr.hwnds["translator"] = 42
        mgr.refresh_icon("translator")
        names = [name for name, _ in win.calls]
        self.assertEqual(names, ["set_window_app_id", "set_window_icon"])
        icon = module_apps.load_store(self.root)["apps"]["translator"]["icon_file"]
        self.assertEqual(win.calls[0][1][-1], icon, "a pinned button relaunches with the new icon")
        self.assertEqual(win.calls[1][1], (42, icon))

    def test_icon_sync_route_for_page_drawn_icons(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self._custom()
        with mock.patch.object(module_apps, "SHELL", self.shell), \
                mock.patch.object(modules_host, "default_root", lambda: self.root), \
                mock.patch.object(module_apps, "_root", lambda root=None: Path(root) if root else self.root), \
                mock.patch.object(module_apps, "app_dir", lambda root=None: self.root / "apps"):
            module_apps.install("glyphy", icon_png=_png_url((1, 2, 3, 255)), shell=self.shell)
            app = FastAPI()
            module_apps.register(app)
            client = TestClient(app)
            same = client.post("/api/pg/module-apps/icon-sync", json={"id": "glyphy", "icon_png": _png_url((1, 2, 3, 255))})
            self.assertEqual(same.json(), {"ok": True, "changed": False})
            new = client.post("/api/pg/module-apps/icon-sync", json={"id": "glyphy", "icon_png": _png_url((9, 9, 9, 255))})
            self.assertEqual(new.json(), {"ok": True, "changed": True})
            self.assertEqual(client.post("/api/pg/module-apps/icon-sync", json={"id": "translator"}).status_code, 404)

    def test_page_checks_installed_app_icons_once(self):
        js = (ROOT / "app" / "pgstatic" / "module-apps.js").read_text(encoding="utf-8")
        self.assertIn("/api/pg/module-apps/icon-sync", js)
        self.assertIn('startsWith("file:")', js)


class WindowsHelpersTest(unittest.TestCase):
    def test_retarget_only_our_old_icons(self):
        tmp = Path(tempfile.mkdtemp(prefix="r40lnk-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        icons = tmp / "module-apps"
        new = icons / "shell" / "abc" / "music.ico"
        links = {"pinned.lnk": (r"C:\MemuAI.exe", str(icons / "music.ico"), 0),             # pinned before r40
                 "older.lnk": (r"C:\MemuAI.exe", str(icons / "shell" / "old" / "music.ico"), 0),
                 "other.lnk": (r"C:\MemuAI.exe", str(icons / "studio.ico"), 0),              # another module
                 "user.lnk": (r"C:\MemuAI.exe", str(tmp / "mine" / "music.ico"), 0),          # the person's icon
                 "done.lnk": (r"C:\MemuAI.exe", str(new), 0)}
        for name in links:
            (tmp / name).write_text("x", encoding="utf-8")

        class Api:
            written = []

            def read_shortcut(self, link):
                return links[Path(link).name]

            def write_shortcut_icon(self, link, icon, index):
                self.written.append((Path(link).name, icon))
                return True
        fake = Api()
        with mock.patch.object(module_apps_win, "available", lambda: True), \
                mock.patch("app.win_app_identity.api", lambda: fake):
            changed = module_apps_win.retarget_shortcut_icons("music", str(new), icons, folders=[tmp])
        self.assertEqual(sorted(Path(p).name for p in changed), ["older.lnk", "pinned.lnk"])
        self.assertTrue(all(icon == str(new) for _n, icon in fake.written))

    def test_off_windows_noops(self):
        self.assertEqual(module_apps_win.retarget_shortcut_icons("music", "x.ico", Path(".")), [])
        self.assertEqual(module_apps_win.refresh_shell_icons(["x.ico"]), 0)
        self.assertFalse(module_apps_win.set_window_icon(1, "x.ico"))


if __name__ == "__main__":
    unittest.main()
