"""Server-mode smoke tests: XDG data dir (the server runs on Linux),
no unconditional Windows imports, headless CLI, and ``memuai_server.py``
headless startup (no window, no desktop entries, no installer platform).

There is no Linux desktop version of MemuAI: ``packaging/debian``,
``packaging/linux``, ``installer/linuxplat.py`` and ``README_LINUX.md``
were removed, and ``python build.py portable`` no longer exists. The
server is the only supported way to run MemuAI on Linux
(see ``docs/SERVER.md``).

Runs with the standard library only: heavy third-party deps (FastAPI,
uvicorn, pywebview) are not installed in this VM, so ``create_app()``
and the ``/api/server`` router mount are skipped there instead of
faked -- a stub would prove nothing about the real factory. The
MemuNote/MemuFiles headless servers are stdlib and are covered by
their own ``--no-window`` runs, not here.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WIN_ONLY = {"winreg", "win32", "win32api", "win32con", "win32gui",
            "clr", "pythonnet", "_winreg", "msvcrt"}


def _run_python(code: str, env: dict) -> str:
    merged = dict(os.environ)
    merged.pop("MEMUAI_DATA_DIR", None)
    merged.pop("OMNIHUB_DATA_DIR", None)
    merged.pop("APPDATA", None)
    merged.update(env)
    merged["PYTHONUTF8"] = "1"
    result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                            env=merged, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr.strip() or f"exit {result.returncode}")
    return result.stdout.strip()


class DataDirTest(unittest.TestCase):
    def test_xdg_data_home_is_used_on_linux(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            xdg = os.path.join(tmp, "xdg")
            out = _run_python("from app.config import DATA_DIR; print(DATA_DIR)",
                              {"HOME": tmp, "XDG_DATA_HOME": xdg})
            self.assertEqual(out, os.path.join(xdg, "MemuAI"))

    def test_default_without_xdg_is_local_share(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": tmp, "XDG_DATA_HOME": ""}
            out = _run_python("from app.config import DATA_DIR; print(DATA_DIR)", env)
            self.assertEqual(out, os.path.join(tmp, ".local", "share", "MemuAI"))

    def test_relative_xdg_falls_back_to_local_share(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = _run_python("from app.config import DATA_DIR; print(DATA_DIR)",
                              {"HOME": tmp, "XDG_DATA_HOME": "relative/path"})
            self.assertEqual(out, os.path.join(tmp, ".local", "share", "MemuAI"))

    def test_explicit_data_dir_still_wins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = _run_python(
                "from app.config import DATA_DIR; print(DATA_DIR)",
                {"HOME": tmp, "XDG_DATA_HOME": os.path.join(tmp, "xdg"),
                 "MEMUAI_DATA_DIR": os.path.join(tmp, "custom")})
            self.assertEqual(out, os.path.join(tmp, "custom"))

    def test_proxy_helper_uses_sibling_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            xdg = os.path.join(tmp, "xdg")
            code = ("from app.config import default_data_dir; "
                    "print(default_data_dir('MemuAIProxy')); print(default_data_dir())")
            out = _run_python(code, {"HOME": tmp, "XDG_DATA_HOME": xdg})
            proxy_dir, suite_dir = out.splitlines()
            self.assertEqual(proxy_dir, os.path.join(xdg, "MemuAIProxy"))
            self.assertEqual(suite_dir, os.path.join(xdg, "MemuAI"))

    def test_entry_points_agree_with_xdg_data_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            xdg = os.path.join(tmp, "xdg")
            code = ("import os\n"
                    "from main import _runtime_file\n"
                    "from app.deeplinks import _memu_dir\n"
                    "from proxy_only import configure_edition\n"
                    "print(_runtime_file('proxy.json'))\n"
                    "print(str(_memu_dir()))\n"
                    "configure_edition()\n"
                    "print(os.environ['MEMUAI_DATA_DIR'])")
            out = _run_python(code, {"HOME": tmp, "XDG_DATA_HOME": xdg})
            runtime, memu, proxy_env = out.splitlines()
            self.assertEqual(runtime, os.path.join(xdg, "MemuAI", "memuai", "proxy.json"))
            self.assertEqual(memu, os.path.join(xdg, "MemuAI", "memuai"))
            self.assertEqual(proxy_env, os.path.join(xdg, "MemuAIProxy"))


class NoWindowsImportsTest(unittest.TestCase):
    def test_no_unconditional_windows_imports(self):
        offenders = []
        for path in sorted(ROOT.rglob("*.py")):
            parts = path.parts
            if "__pycache__" in parts or ".venv" in parts or "private-dev" in parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in tree.body:
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in WIN_ONLY:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
        self.assertEqual(offenders, [])


class ServerModeTest(unittest.TestCase):
    def test_server_data_dir_defaults_to_xdg_server_subdir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            xdg = os.path.join(tmp, "xdg")
            out = _run_python("from memuai_server import default_data_dir; print(default_data_dir())",
                              {"HOME": tmp, "XDG_DATA_HOME": xdg,
                               "MEMUAI_SERVER_DATA_DIR": ""})
            self.assertEqual(out, os.path.join(xdg, "MemuAI", "server"))

    def test_server_data_dir_env_override_wins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "srv")
            out = _run_python("from memuai_server import default_data_dir; print(default_data_dir())",
                              {"HOME": tmp, "MEMUAI_SERVER_DATA_DIR": target})
            self.assertEqual(out, target)

    def test_server_help_runs_headless(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, MEMUAI_SERVER_DATA_DIR=tmp, PYTHONUTF8="1")
            result = subprocess.run([sys.executable, str(ROOT / "memuai_server.py"), "--help"],
                                    cwd=str(ROOT), env=env, capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr[-500:])
            self.assertIn("MemuAI Server", result.stdout)

    def test_server_refuses_non_loopback_without_allow_remote(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, MEMUAI_SERVER_DATA_DIR=tmp, PYTHONUTF8="1")
            result = subprocess.run(
                [sys.executable, str(ROOT / "memuai_server.py"),
                 "--host", "0.0.0.0", "--port", "8327"],
                cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--allow-remote", result.stderr)

    def test_server_token_file_is_created_mode_0600(self):
        import importlib.util
        import stat
        import tempfile
        # Load auth.py directly: importing the app.server_mode package
        # pulls in the FastAPI router, which is not installed here.
        # auth.py itself is stdlib-only.
        spec = importlib.util.spec_from_file_location(
            "server_mode_auth", ROOT / "app" / "server_mode" / "auth.py")
        auth_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(auth_mod)
        TokenStore = auth_mod.TokenStore
        with tempfile.TemporaryDirectory() as tmp:
            store = TokenStore(tmp)
            token = store.get_or_create()
            self.assertTrue(token)
            self.assertEqual(store.get_or_create(), token)
            self.assertTrue(store.path.is_file())
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)

    @unittest.skipUnless(importlib.util.find_spec("fastapi") is not None,
                         "FastAPI is not installed in this VM")
    def test_build_server_app_mounts_router_with_fake_factory(self):
        import tempfile
        from memuai_server import build_server_app
        with tempfile.TemporaryDirectory() as tmp:
            fake = SimpleNamespace(routers=[])
            fake.include_router = lambda router: fake.routers.append(router)
            app = build_server_app(tmp, base_app_factory=lambda: fake)
            self.assertIs(app, fake)
            self.assertEqual(len(fake.routers), 1)
            self.assertTrue((Path(tmp) / "server_token").is_file())

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "Linux-only assertion")
    def test_make_platform_rejects_linux_desktop(self):
        from installer.main import make_platform
        with self.assertRaises(RuntimeError):
            make_platform()


class DesktopArtifactsRemovedTest(unittest.TestCase):
    def test_linux_desktop_packaging_and_platform_are_gone(self):
        self.assertFalse((ROOT / "packaging" / "debian").exists())
        self.assertFalse((ROOT / "packaging" / "linux").exists())
        self.assertFalse((ROOT / "installer" / "linuxplat.py").exists())
        self.assertFalse((ROOT / "README_LINUX.md").exists())
        self.assertFalse((ROOT / "dist" / "MemuAI-4.5-linux.tar.gz").exists())
        self.assertFalse((ROOT / "dist" / "memuai_4.5_amd64.deb").exists())
        self.assertEqual(list((ROOT / "packaging").rglob("*.desktop")), [])
        self.assertTrue((ROOT / "packaging" / "server").is_dir())

    def test_no_references_to_removed_desktop_build_remain(self):
        build_text = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertNotIn("build_portable", build_text)
        self.assertNotIn("portable", build_text)
        self.assertNotIn("linuxplat", build_text)
        main_text = (ROOT / "installer" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("linuxplat", main_text)
        self.assertNotIn("LinuxPlatform", main_text)


class CliTest(unittest.TestCase):
    def test_cli_help_runs_on_linux(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, MEMUAI_DATA_DIR=tmp, PYTHONUTF8="1")
            result = subprocess.run([sys.executable, "-m", "app.cli", "--help"],
                                    cwd=str(ROOT), env=env, capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr[-500:])
            self.assertIn("MemuCode", result.stdout)


@unittest.skipUnless(importlib.util.find_spec("fastapi") is not None
                     and importlib.util.find_spec("uvicorn") is not None,
                     "FastAPI/uvicorn are not installed in this VM")
class ProxyAppTest(unittest.TestCase):
    def test_create_app(self):
        from app.main import create_app
        app = create_app()
        self.assertTrue(hasattr(app, "routes"))


if __name__ == "__main__":
    unittest.main()
