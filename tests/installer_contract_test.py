"""Contract of MemuAI Setup (the custom installer in ``installer/``) with the rest of the project.

The Inno Setup scripts (``installer.iss``, ``installer-proxy.iss``) are gone; the
promises they made are kept by ``installer/products.py`` (what is installed
where), ``installer/cli.py`` (silent switches), ``installer/engine.py``
(registry, shortcuts, PATH, uninstall) and ``build.py`` (build targets). This
file pins those promises — behaviour is covered in ``installer_engine_test.py``.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import build
import proxy_only
from app import admin_api, desktop, windows_startup
from installer import cli, engine, i18n, products
from installer.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer"
#: The AppIds of the old Inno Setup scripts: Windows keys "Installed apps" by them.
SUITE_APPID = "{7C2A93E1-5B44-4E0D-9F61-8A2B43E9A122}"
PROXY_APPID = "{D05309A7-8FBA-40D6-BBE9-3FD5614D3B88}"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class InnoScriptsAreRetired(unittest.TestCase):
    def test_no_inno_sources_or_art_generator_are_left(self) -> None:
        for name in ("installer.iss", "installer-proxy.iss", "build_installer_art.py"):
            self.assertFalse((ROOT / name).exists(), name)
        source = read("build.py")
        self.assertNotIn("ISCC", source)
        self.assertNotIn(".iss", source)
        self.assertIn("from installer import build_setup", source)

    def test_gitignore_keeps_the_installer_sources(self) -> None:
        lines = [line.strip() for line in read(".gitignore").splitlines()]
        self.assertNotIn("installer/", lines, "would ignore the installer source package")
        self.assertIn("/installer/*.exe", lines)
        self.assertIn("/installer/*.partial", lines)


class ProductsContract(unittest.TestCase):
    def test_registry_keys_are_the_inno_appids(self) -> None:
        # Same key = an old Inno installation is upgraded in place, one entry in Installed apps.
        self.assertEqual(products.SUITE.reg_key, SUITE_APPID + "_is1")
        self.assertEqual(products.PROXY.reg_key, PROXY_APPID + "_is1")
        self.assertEqual(products.SUITE.legacy_machine_key, SUITE_APPID + "_is1")
        self.assertEqual(products.PROXY.legacy_machine_key, PROXY_APPID + "_is1")
        self.assertEqual(products.UNINSTALL_ROOT, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
        self.assertEqual(engine.HKCU, "HKCU", "per-user registration, never HKLM")
        body = inspect.getsource(Engine.detect)
        self.assertIn('"HKLM64"', body)
        self.assertIn('"HKLM32"', body)
        self.assertIn("Inno Setup: App Path", inspect.getsource(Engine))

    def test_suite_payloads_stay_isolated_and_proxy_is_always_present(self) -> None:
        suite = products.SUITE
        self.assertEqual(suite.component_ids(), ["proxy", "memuai", "memucode", "memunote", "memufiles"])
        self.assertTrue(suite.component("proxy").required)
        dests = [c.dest for c in suite.components]
        self.assertEqual(dests, ["MemuAIProxy", "MemuAI", "MemuCode", "MemuNote", "MemuFiles"], "one folder per program")
        self.assertEqual(len(set(dests)), 5)
        for comp in suite.components:
            self.assertTrue(comp.exe.startswith(comp.dest + "\\"), comp.id)
        self.assertEqual(suite.normalize([], [])[0], ["proxy"])
        self.assertEqual(suite.normalize(["memucode"], [])[0], ["proxy"], "MemuCode ships with the full edition")
        self.assertEqual(suite.normalize(["memunote"], [])[0], ["proxy"], "MemuNote ships with the full edition")
        self.assertEqual(suite.normalize(["memufiles"], [])[0], ["proxy"], "Round 35: MemuFiles ships with the full edition")
        self.assertFalse(suite.component("memufiles").default, "Round 35: MemuFiles is optional (packaged otherwise)")
        self.assertFalse(suite.component("memunote").default, "Round 29: MemuNote is optional (packaged otherwise)")
        self.assertEqual(suite.default_components(), ["proxy", "memuai", "memucode"])

    def test_standalone_proxy_has_only_the_proxy_payload(self) -> None:
        proxy = products.PROXY
        self.assertNotIn(SUITE_APPID, proxy.reg_key)
        self.assertEqual(proxy.setup_basename, "MemuAIProxy-Setup")
        self.assertEqual(proxy.component_ids(), ["proxy"])
        self.assertEqual(proxy.component("proxy").source, build.TARGETS["proxy_only"]["dist"])
        self.assertNotIn("MemuAI.exe", repr(proxy))
        self.assertNotIn("app/pgstatic", str(build.TARGETS["proxy_only"]["data"]))
        self.assertEqual(proxy.data_dirs, (("APPDATA", "MemuAIProxy"),))
        self.assertNotEqual(proxy.group, products.SUITE.group)
        self.assertNotEqual(proxy.uninstaller, products.SUITE.uninstaller)

    def test_sources_are_the_build_outputs(self) -> None:
        for key, comp in (("proxy", "proxy"), ("memuai", "memuai"), ("memucode", "memucode"), ("memunote", "memunote")):
            target = build.TARGETS[key]
            self.assertEqual(products.SUITE.component(comp).source, target.get("dist", "dist/" + target["name"]))
            self.assertTrue(products.SUITE.component(comp).exe.endswith(target["name"] + ".exe"))

    def test_install_is_per_user_in_local_app_data(self) -> None:
        source = inspect.getsource(Engine.default_dir)
        self.assertIn("LOCALAPPDATA", source)
        self.assertIn("Programs", source)
        self.assertEqual(products.SUITE.default_dir_name, "MemuAI")
        self.assertEqual(products.PROXY.default_dir_name, "MemuAI Proxy")
        spec = read("installer/setup.spec")
        self.assertIn("uac_admin=False", spec)
        self.assertIn("upx=False", spec)
        self.assertIn('path.name != "mock.js"', spec, "the development mock never ships")

    def test_startup_values_match_the_apps(self) -> None:
        suite_runs = {r.name: r for r in products.SUITE.run_values}
        self.assertEqual(suite_runs["MemuAIProxy"].args, "--background")
        self.assertEqual(suite_runs["MemuAIProxy"].task, "autostart")
        # MemuAI toggles its own autostart (Settings) under the same value name.
        self.assertEqual(suite_runs[windows_startup.VALUE_NAME].task, "memuaiautostart")
        self.assertEqual(products.SUITE.task("autostart").default, True)
        self.assertEqual(products.SUITE.task("memuaiautostart").default, False)
        self.assertEqual([r.name for r in products.PROXY.run_values], ["MemuAIProxyStandalone"])

    def test_shortcuts(self) -> None:
        names = {(s.where, s.name): s for s in products.SUITE.shortcuts}
        self.assertEqual(names[("programs", "MemuAI")].app_id, "MemuAI.Desktop")
        self.assertIn('"MemuAI.Desktop"', read("memuai.py"), "taskbar grouping with the running window")
        self.assertEqual(names[("programs", "MemuCode CLI")].workdir, "%USERPROFILE%")
        self.assertEqual(names[("desktop", "MemuAI")].task, "desktopicon")
        self.assertEqual(names[("desktop", "MemuAI Proxy")].task, "proxyicon")
        self.assertEqual(names[("programs", "@uninstall_shortcut")].args, "--uninstall")
        for key in ("uninstall_shortcut", "uninstall_shortcut_proxy"):
            self.assertEqual(set(i18n.STRINGS[key]), {"en", "ru"})
        retired = {name for _where, name in products.SUITE.legacy_shortcuts}
        self.assertIn("MemuAI Proxy без окна", retired)

    def test_memucode_goes_on_the_user_path(self) -> None:
        self.assertEqual(products.SUITE.path_entries, (("memucode", "MemuCode"), ("memuai", r"MemuAI\MemuCLI")))   # Round 35: memu
        self.assertEqual(products.PROXY.path_entries, ())
        apply_path = inspect.getsource(Engine._apply_path)
        self.assertIn('"Environment"', apply_path)
        self.assertIn('"expand_sz"', apply_path, "PATH stays REG_EXPAND_SZ")
        self.assertIn("broadcast_environment", inspect.getsource(Engine.install))
        self.assertIn("removing=True", inspect.getsource(Engine.uninstall))

    def test_apps_are_closed_gracefully_never_with_taskkill(self) -> None:
        self.assertEqual(products.SUITE.shutdown, (r"MemuAI\MemuAI.exe", r"MemuAIProxy\MemuAIProxy.exe",
                                                   r"MemuNote\MemuNote.exe", r"MemuFiles\MemuFiles.exe"))
        self.assertIn('"--shutdown"', inspect.getsource(Engine._shutdown_apps))
        for path in INSTALLER.glob("*.py"):
            self.assertNotIn("taskkill", path.read_text(encoding="utf-8").casefold(), path.name)


class UninstallContract(unittest.TestCase):
    def test_personal_data_is_kept_unless_asked(self) -> None:
        self.assertIs(inspect.signature(Engine.uninstall).parameters["remove_data"].default, False)
        self.assertFalse(cli.Args().remove_data)
        self.assertFalse(cli.parse(["--uninstall", "--silent"]).remove_data)
        self.assertTrue(cli.parse(["--uninstall", "--silent", "--remove-data"]).remove_data)
        html = read("installer/ui/index.html")
        self.assertRegex(html, r'<input type="checkbox" class="switch" id="opt-keep" checked>')
        self.assertEqual(products.SUITE.data_dirs, (("APPDATA", "MemuAI"), ("LOCALAPPDATA", "MemuAI")))
        self.assertIn("_safe_data_dir", inspect.getsource(Engine.uninstall))

    def test_registration_has_uninstall_strings(self) -> None:
        source = inspect.getsource(Engine._register)
        for name in ("DisplayName", "DisplayVersion", "Publisher", "DisplayIcon", "UninstallString",
                     "QuietUninstallString", "InstallLocation", "EstimatedSize", "NoModify", "NoRepair"):
            self.assertIn(f'"{name}"', source)
        self.assertIn("--uninstall --silent", source)


class SilentSwitchContract(unittest.TestCase):
    def test_inno_compatible_switches(self) -> None:
        args = cli.parse(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", r'/DIR="D:\Apps\MemuAI"',
                          "/COMPONENTS=memuai,memucode", "/TASKS=autostart,desktopicon", "/LANG=ru", r"/LOG=C:\s.log"])
        self.assertTrue(args.silent and args.very_silent)
        self.assertEqual(args.directory, r"D:\Apps\MemuAI")
        self.assertEqual(args.components, ["memuai", "memucode"])
        self.assertEqual(args.tasks, ["autostart", "desktopicon"])
        self.assertEqual(args.language, "ru")
        self.assertEqual(args.log, r"C:\s.log")
        self.assertEqual(args.unknown, [])
        self.assertTrue(cli.parse(["/SILENT"]).silent)
        self.assertEqual(cli.parse(["/LANG=english"]).language, "en")
        self.assertEqual(cli.parse(["/LOG"]).log, "", "/LOG without a path = default log in %TEMP%")
        self.assertEqual(cli.parse(["/TYPE=proxyonly"]).edition, "proxy")

    def test_nsis_switches(self) -> None:
        args = cli.parse(["/S", r"/D=C:\My Apps\MemuAI"])
        self.assertTrue(args.silent)
        self.assertEqual(args.directory, r"C:\My Apps\MemuAI", "/D= is last and may hold spaces")

    def test_selection_follows_inno_semantics(self) -> None:
        suite = products.SUITE
        base = (suite.default_components(), suite.default_tasks())
        self.assertEqual(cli.resolve_selection(cli.parse(["/COMPONENTS=memuai"]), suite, *base)[0],
                         ["proxy", "memuai"], "required Proxy is always added")
        self.assertEqual(cli.resolve_selection(cli.parse(["/TASKS=desktopicon"]), suite, *base)[1],
                         ["desktopicon"], "/TASKS replaces the defaults")
        self.assertEqual(cli.resolve_selection(cli.parse(["/MERGETASKS=!autostart,proxyicon"]), suite, *base)[1],
                         ["proxyicon"])

    def test_exit_codes_and_silent_mode_close_apps(self) -> None:
        from installer import main as setup_main
        self.assertEqual((setup_main.EXIT_OK, setup_main.EXIT_ERROR, setup_main.EXIT_CANCELLED, setup_main.EXIT_APPS),
                         (0, 1, 2, 5))
        self.assertIn("force_close=True", inspect.getsource(setup_main.silent_install))


class BuildContract(unittest.TestCase):
    def test_build_targets(self) -> None:
        source = read("build.py")
        self.assertIn('choices=["exe", "installer", "proxy-installer", "version"]', source)
        self.assertIn('build_setup(sys.modules[__name__], "memuai", version', source)
        self.assertIn('build_setup(sys.modules[__name__], "proxy", version', source)
        self.assertIn("build.py installer", read("build_installer.bat"))
        self.assertIn("build.py proxy-installer", read("build_proxy_installer.bat"))
        from installer import build_setup
        self.assertEqual(set(build_setup.STUB_TARGETS), set(products.PRODUCTS))
        self.assertIn('f"{product.setup_basename}-{version}.exe"', inspect.getsource(build_setup.build_setup))

    def test_setup_version_is_the_bundle_version(self) -> None:
        from installer import build_setup
        body = inspect.getsource(build_setup.build_setup)
        self.assertIn('"version": version', body, "the payload meta carries build.app_version()")
        for path in INSTALLER.glob("*.py"):
            self.assertIsNone(re.search(r'VERSION\s*=\s*"\d', path.read_text(encoding="utf-8")), path.name)


class ProxyOnlyInstallerContractTest(unittest.TestCase):
    def test_proxy_only_assets_omit_chat_app_references(self) -> None:
        build.prepare_proxy_only_assets()
        ui = ROOT / "build" / "proxy-only-static"
        kb = ROOT / "build" / "proxy-only-kb"
        self.assertNotIn("open-memuai", (ui / "index.html").read_text(encoding="utf-8"))
        app_js = (ui / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("open_memuai", app_js)
        self.assertIn("memuai-proxy-accounts.json", app_js)
        self.assertNotIn('"memuai-accounts.json"', app_js)
        self.assertNotIn("Чат MemuAI", (kb / "overview.md").read_text(encoding="utf-8"))
        self.assertIn("MemuAI Proxy", (ui / "index.html").read_text(encoding="utf-8"))

    def test_standalone_data_directory_is_separate(self) -> None:
        with patch.dict(os.environ, {"APPDATA": str(ROOT / "test-appdata")}, clear=True):
            proxy_only.configure_edition()
            self.assertEqual(os.environ["MEMUAI_PROXY_ONLY"], "1")
            self.assertEqual(Path(os.environ["MEMUAI_DATA_DIR"]),
                             ROOT / "test-appdata" / "MemuAIProxy")
        # The uninstaller removes (on request) exactly the folder the program uses.
        self.assertEqual(products.PROXY.data_dirs[0][1], "MemuAIProxy")

    def test_standalone_cannot_launch_the_chat_application(self) -> None:
        with patch.dict(os.environ, {"MEMUAI_PROXY_ONLY": "1"}), \
                patch.object(admin_api, "_auth", return_value=None):
            response = asyncio.run(admin_api.open_memuai(object()))
        self.assertEqual(response.status_code, 404)

    def test_existing_other_proxy_edition_is_reported(self) -> None:
        from main import main as run_proxy

        with patch.dict(os.environ, {"MEMUAI_PROGRAM_DIR": str(ROOT), "MEMUAI_PROXY_ONLY": "1"}), \
                patch.object(sys, "argv", ["proxy_only.py"]), \
                patch("main.activate_running_proxy", return_value=False), \
                patch.object(desktop, "acquire_instance_lock", return_value=None), \
                patch.object(desktop, "message_box") as message:
            run_proxy()
        self.assertIn("Другой экземпляр", message.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
