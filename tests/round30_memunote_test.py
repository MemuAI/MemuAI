"""Round 30 (r30-memunote): MemuNote starts reliably, and its new icon.

* the import contract — MemuNote never imports, unguarded, a module that
  MemuNote.spec / build.py TARGETS["memunote"] exclude (static AST walk) and it
  really starts with those modules blocked (subprocess with an import hook);
* the window icon is never a PNG on Windows (pywebview ≥ 6.2 + System.Drawing.Icon
  crashed the GUI thread: the "blinks in Task Manager" bug);
* single instance: a ghost instance (no window) is replaced, not handed the files;
* crash log + message, browser fallback without WebView2;
* the icon assets (SVG source, PNG 512/256, multi-size ICO) and where they are used.
No network: everything talks to 127.0.0.1 only.
"""
from __future__ import annotations

import ast
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build  # noqa: E402

TARGET = build.TARGETS["memunote"]
EXCLUDES = tuple(TARGET["exclude"])
STDLIB = set(sys.stdlib_module_names)

# Excluded modules MemuNote may reach only behind a guard (try/except, or a caller that catches):
# (importing module, excluded target) → why it is safe.
ALLOWED_GUARDED = {
    ("app.memory_v2", "app.pgdata"): "memu_dir(): try/except, falls back to the data dir rule",
    ("app.memory_v2", "app.config"): "memu_dir(): try/except ImportError",
    ("app.themes", "app.pgdata"): "themes_dir() — user themes, never called by MemuNote",
    ("app.memunote.ai", "app.websearch"): "local web search fallback, try/except",
}


def excluded(name: str) -> str:
    for item in EXCLUDES:
        if name == item or name.startswith(item + "."):
            return item
    return ""


def module_file(name: str) -> Path | None:
    rel = Path(*name.split("."))
    for candidate in (ROOT / (str(rel) + ".py"), ROOT / rel / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _guarded(stack) -> bool:
    for node in stack:
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                kind = handler.type
                names = []
                if kind is None:
                    return True
                for item in (kind.elts if isinstance(kind, ast.Tuple) else [kind]):
                    names.append(getattr(item, "id", getattr(item, "attr", "")))
                if {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"} & set(names):
                    return True
    return False


def imports_of(name: str, path: Path):
    """[(target module, guarded, top_level)] for every import statement of a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found = []

    def visit(node, stack, in_function):
        for child in ast.iter_child_nodes(node):
            fn = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            new_stack = stack + [node] if isinstance(node, ast.Try) and child in node.body else stack
            if isinstance(child, ast.Import):
                for alias in child.names:
                    found.append((alias.name, _guarded(new_stack), not in_function))
            elif isinstance(child, ast.ImportFrom):
                if child.level:
                    base = package.split(".")
                    base = base[:len(base) - (child.level - 1)] if child.level > 1 else base
                    mod = ".".join(base + ([child.module] if child.module else []))
                else:
                    mod = child.module or ""
                targets = [mod]
                for alias in child.names:              # "from . import x" / "from .. import memory_v2"
                    if module_file(f"{mod}.{alias.name}"):
                        targets.append(f"{mod}.{alias.name}")
                for target in targets:
                    found.append((target, _guarded(new_stack), not in_function))
            visit(child, new_stack, fn)

    visit(tree, [], False)
    return found


def import_closure():
    """Walk MemuNote's imports from memunote.py. Returns (visited app modules, problems)."""
    problems, visited = [], set()
    queue = [("memunote", ROOT / "memunote.py")]
    while queue:
        name, path = queue.pop()
        if name in visited:
            continue
        visited.add(name)
        own = name == "memunote" or name.startswith("app.memunote")
        for target, guarded, top_level in imports_of(name, path):
            hit = excluded(target)
            if hit:
                if (name, hit) in ALLOWED_GUARDED:
                    continue
                if own or top_level:
                    problems.append(f"{name} imports excluded {target} ({'guarded' if guarded else 'UNGUARDED'})")
                continue
            if not own and not top_level:
                continue        # a shared module's lazy import: only reached through functions MemuNote calls
            root = target.split(".")[0]
            if root != "app":
                continue
            file = module_file(target)
            if file is not None:
                queue.append((target, file))
    return visited, problems


class ImportContractTest(unittest.TestCase):
    def test_no_excluded_import_reaches_memunote(self) -> None:
        visited, problems = import_closure()
        self.assertFalse(problems, "\n".join(problems))
        for name in ("app.memunote.desktop", "app.memunote.server", "app.memunote.crash", "app.memory_v2"):
            self.assertIn(name, visited)

    def test_allowlist_is_real(self) -> None:
        """Each allowed pair still exists and is still guarded — a stale entry would hide nothing."""
        for (module, target), why in ALLOWED_GUARDED.items():
            pairs = [(t, g) for t, g, _top in imports_of(module, module_file(module)) if excluded(t) == target]
            self.assertTrue(pairs, f"{module} no longer imports {target}: drop it from ALLOWED_GUARDED")
            if target != "app.pgdata" or module != "app.themes":
                self.assertTrue(all(g for _t, g in pairs), f"{module} → {target} is no longer guarded ({why})")

    def test_runtime_dependencies_not_excluded(self) -> None:
        for name in ("webview", "clr", "pythonnet", "clr_loader", "bottle", "proxy_tools", "typing_extensions",
                     "app.memunote", "app.memory_v2", "app.storage", "app.themes", "app.version"):
            self.assertFalse(excluded(name), name)
        third_party = set()
        for name in import_closure()[0]:
            for target, _g, _t in imports_of(name, module_file(name) or ROOT / "memunote.py"):
                root = target.split(".")[0]
                if root and root not in STDLIB and root != "app":
                    third_party.add(root)
        self.assertFalse({n for n in third_party if excluded(n)}, third_party)

    def test_spec_matches_build_target(self) -> None:
        spec = (ROOT / "MemuNote.spec").read_text(encoding="utf-8")
        tree = ast.parse(spec)
        hidden = excludes = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "hiddenimports" for t in node.targets):
                hidden = ast.literal_eval(node.value)
            if isinstance(node, ast.keyword) and node.arg == "excludes":
                excludes = ast.literal_eval(node.value)
        self.assertEqual(hidden, TARGET["hidden"])
        self.assertEqual(excludes, TARGET["exclude"])
        self.assertIn("app.memunote.crash", TARGET["hidden"])
        self.assertIn("app.pgdata", TARGET["exclude"])
        self.assertIn("clr", TARGET["hidden"])
        icon = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                    if isinstance(node, ast.keyword) and node.arg == "icon")
        self.assertEqual(Path(icon[0]).name, "memunote-icon.ico")
        self.assertIn("console=False", spec)


BLOCKER = r"""
import importlib.abc, json, runpy, sys
EXCL = tuple(json.loads(sys.argv[1]))
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if any(name == e or name.startswith(e + ".") for e in EXCL):
            raise ModuleNotFoundError(f"No module named {name!r} (excluded from MemuNote.exe)", name=name)
        return None
sys.meta_path.insert(0, Block())
ROOT, mode = sys.argv[2], sys.argv[3]
sys.path.insert(0, ROOT)
if mode == "run":
    sys.argv = ["memunote.py"] + sys.argv[4:]
    runpy.run_path(ROOT + "/memunote.py", run_name="__main__")
else:
    from app import memory_v2
    from app.memunote import ai, crash, desktop, prompts, server, settings, winsys
    from app.memunote.formats import docx, odt, rtf, richhtml, textio
    print(json.dumps({"memu_dir": str(memory_v2.memu_dir()),
                      "memory": bool(ai.memory_context("hello", True)["protocol"]),
                      "theme": server.NoteApp().theme()["mode"],
                      "loaded": [m for m in sys.modules if any(m == e or m.startswith(e + ".") for e in EXCL)]}))
"""


class FrozenLikeStartTest(unittest.TestCase):
    """MemuNote with every excluded module blocked, the way MemuNote.exe runs."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r30mn-")
        self.env = {**os.environ, "MEMUAI_DATA_DIR": self.tmp.name, "NO_PROXY": "127.0.0.1,localhost"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cmd(self, *args: str):
        return [sys.executable, "-c", BLOCKER, json.dumps(EXCLUDES), str(ROOT), *args]

    def test_modules_work_without_excluded(self) -> None:
        out = subprocess.run(self._cmd("check"), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(data["loaded"], [])
        self.assertTrue(data["memory"], "MemuAI memory must work inside MemuNote.exe")
        self.assertEqual(Path(data["memu_dir"]), Path(self.tmp.name) / "memuai")

    def test_headless_start_serves_and_shuts_down(self) -> None:
        proc = subprocess.Popen(self._cmd("run", "--new-instance", "--no-window", "--print-url"), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            url = proc.stdout.readline().strip()
            self.assertTrue(url.startswith("http://127.0.0.1:"), proc.stderr.read() if not url else url)
            base, token = url.split("/index.html#t=")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def call(path, body=None):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                             headers={"X-MN-Token": token, "Content-Type": "application/json"})
                with opener.open(req, timeout=10) as response:
                    return response.status, response.read()

            status, page = call("/index.html")
            self.assertEqual(status, 200)
            self.assertIn(b"memunote", page.lower())
            boot = json.loads(call("/api/boot")[1])
            self.assertTrue(boot["ok"])
            self.assertFalse(boot["desktop"])
            self.assertEqual(call("/memunote.svg")[0], 200)
            ping = json.loads(call("/api/remote", {"action": "ping"})[1])
            self.assertEqual(ping["ui"], "headless")
            call("/api/remote", {"action": "shutdown"})
            self.assertEqual(proc.wait(timeout=10), 0)
            self.assertFalse((Path(self.tmp.name) / "memunote" / "instance.json").exists())
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            proc.stderr.close()


class IconSafetyTest(unittest.TestCase):
    def test_never_png_on_windows(self) -> None:
        from app.memunote import desktop
        self.assertIsNone(desktop._icon_path("nt", True))           # frozen: the exe's own icon
        dev = desktop._icon_path("nt", False)
        self.assertTrue(dev and dev.endswith(".ico"), dev)
        self.assertTrue(desktop._icon_path("posix", False).endswith(".png"))
        source = (ROOT / "app" / "memunote" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn('"icon": _icon_path()', source)


class SingleInstanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r30mn-si-")
        self.patch = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp.name})
        self.patch.start()
        from app.memunote import desktop, server
        self.desktop, self.server = desktop, server

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()

    def test_instance_alive_rules(self) -> None:
        alive = self.desktop.instance_alive
        self.assertFalse(alive(None))
        self.assertTrue(alive({"ok": True, "pid": 1}))                      # a Round-29 instance
        for ui in ("window", "browser", "headless"):
            self.assertTrue(alive({"ui": ui, "age": 9999}))
        self.assertTrue(alive({"ui": "starting", "age": 3}))
        self.assertFalse(alive({"ui": "starting", "age": 600}))             # a ghost

    def _instance(self, ui: str, age: float):
        app = self.server.NoteApp([])
        app.ui = ui
        app.started = time.time() - age
        httpd, _ = self.server.start(app)
        self.desktop.publish_instance(app.port, app.token)
        return app, httpd

    def test_ghost_is_replaced(self) -> None:
        app, httpd = self._instance("starting", 300)
        try:
            self.assertFalse(self.desktop.hand_off(["/tmp/a.txt"]))
            self.assertTrue(app.stopping.wait(5), "the ghost was not asked to quit")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_live_window_takes_files(self) -> None:
        app, httpd = self._instance("window", 10)
        try:
            self.assertTrue(self.desktop.hand_off(["/tmp/a.txt"]))
            _, events = app.events.wait(0, 1)
            self.assertIn({"type": "open", "paths": ["/tmp/a.txt"]}, events)
            self.assertFalse(app.stopping.is_set())
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_stale_file_means_no_instance(self) -> None:
        self.desktop.publish_instance(1, "x")          # nothing listens on port 1
        self.assertFalse(self.desktop.hand_off([]))


class CrashAndFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r30mn-crash-")
        self.patch = mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": self.tmp.name})
        self.patch.start()
        from app.memunote import crash, desktop, server
        self.crash, self.desktop, self.server = crash, desktop, server
        self.crash._SHOWN.clear()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()

    def test_log_location(self) -> None:
        self.assertEqual(self.crash.log_path(), Path(self.tmp.name) / "logs" / "memunote-crash.log")
        with mock.patch.dict(os.environ, {"MEMUAI_DATA_DIR": "", "OMNIHUB_DATA_DIR": "", "LOCALAPPDATA": "/x/Local"}):
            self.assertEqual(self.crash.log_path(), Path("/x/Local/MemuAI/logs/memunote-crash.log"))

    def test_texts_bilingual(self) -> None:
        self.assertEqual(set(self.crash.TEXTS["ru"]), set(self.crash.TEXTS["en"]))
        for lang in ("ru", "en"):
            for key, value in self.crash.TEXTS[lang].items():
                self.assertTrue(value.strip(), key)
        self.assertIn("WebView2", self.crash.TEXTS["ru"]["webview2"])

    def test_report_logs_and_tells_once(self) -> None:
        shown = []
        with mock.patch.object(self.crash, "message_box", lambda t, b, error=True: shown.append((t, b))):
            for _ in range(2):
                try:
                    raise RuntimeError("boom")
                except RuntimeError:
                    self.crash.report(*sys.exc_info())
        log = self.crash.log_path().read_text(encoding="utf-8")
        self.assertEqual(log.count("RuntimeError: boom"), 2)
        self.assertEqual(len(shown), 1)
        self.assertIn("RuntimeError: boom", shown[0][1])
        self.assertIn(str(self.crash.log_path()), shown[0][1])

    def test_entry_point_reports_startup_errors(self) -> None:
        import runpy
        module = runpy.run_path(str(ROOT / "memunote.py"), run_name="memunote_test")
        with mock.patch("app.memunote.desktop.main", side_effect=ValueError("bad start")), \
                mock.patch.object(self.crash, "message_box") as box, \
                mock.patch.object(sys, "excepthook", sys.excepthook), \
                mock.patch.object(threading, "excepthook", threading.excepthook):
            self.assertEqual(module["_main"](), 1)
        self.assertIn("bad start", box.call_args[0][1])

    def test_browser_fallback_without_webview2(self) -> None:
        opened, notices = [], []
        with mock.patch.object(self.desktop, "_load_webview", return_value=(None, "webview2")), \
                mock.patch("webbrowser.open", lambda url: opened.append(url)), \
                mock.patch.object(self.crash, "message_box", lambda t, b, error=True: notices.append((t, b))), \
                mock.patch.object(self.desktop, "BROWSER_FIRST_VISIT", 0.1):
            started = time.monotonic()
            code = self.desktop.main(["--new-instance"])
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue(opened and opened[0].startswith("http://127.0.0.1:"))
        time.sleep(0.2)
        self.assertTrue(notices and "WebView2" in notices[0][1] and self.crash.WEBVIEW2_URL in notices[0][1])
        self.assertFalse((Path(self.tmp.name) / "memunote" / "instance.json").exists())

    def test_window_failure_falls_back(self) -> None:
        fake = mock.Mock()
        fake.start.side_effect = RuntimeError("no GUI")
        opened = []
        with mock.patch.object(self.desktop, "_load_webview", return_value=(fake, "")), \
                mock.patch("webbrowser.open", lambda url: opened.append(url)), \
                mock.patch.object(self.crash, "message_box"), \
                mock.patch.object(self.desktop, "BROWSER_FIRST_VISIT", 0.1):
            self.assertEqual(self.desktop.main(["--new-instance"]), 0)
        self.assertTrue(opened)
        self.assertIn("RuntimeError: no GUI", self.crash.log_path().read_text(encoding="utf-8"))

    def test_webview2_probe_is_windows_only(self) -> None:
        from app.memunote import winsys
        if os.name != "nt":
            self.assertEqual(winsys.webview2_version(), "")


def ico_sizes(path: Path):
    data = path.read_bytes()
    _reserved, kind, count = struct.unpack("<HHH", data[:6])
    sizes = []
    for i in range(count):
        w, h = struct.unpack("<BB", data[6 + 16 * i: 8 + 16 * i])
        sizes.append((w or 256, h or 256))
    return kind, sizes


class IconAssetsTest(unittest.TestCase):
    def test_ico_has_every_size(self) -> None:
        kind, sizes = ico_sizes(ROOT / "memunote-icon.ico")
        self.assertEqual(kind, 1)
        for size in (16, 20, 24, 32, 40, 48, 64, 256):
            self.assertIn((size, size), sizes)

    def test_png_and_svg(self) -> None:
        from PIL import Image
        with Image.open(ROOT / "memunote-icon.png") as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((2, 2))[3], 0, "the corners must be transparent")
        with Image.open(ROOT / "app" / "memunote" / "static" / "memunote-icon.png") as image:
            self.assertEqual(image.size, (256, 256))
        svg = (ROOT / "app" / "memunote" / "static" / "memunote.svg").read_text(encoding="utf-8")
        self.assertIn("viewBox=\"0 0 512 512\"", svg)
        self.assertTrue((ROOT / "build_memunote_icon.py").is_file())

    def test_used_everywhere(self) -> None:
        index = (ROOT / "app" / "memunote" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon"', index)
        self.assertIn("memunote.svg", index)
        products = (ROOT / "installer" / "products.py").read_text(encoding="utf-8")
        self.assertIn("memunote", products)


if __name__ == "__main__":
    unittest.main()
