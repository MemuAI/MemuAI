"""Round 46 release proof: archive/PE inspection and noninteractive CLI only.

Never starts desktop windows, providers, installers or browser automation.
Run only after the complete Round 46 build; all child CLI data is isolated.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


def signature(code):
    return (code.co_code, tuple(signature(value) if isinstance(value, types.CodeType) else value for value in code.co_consts),
            code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars, code.co_argcount,
            code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags, code.co_exceptiontable, code.co_linetable)


def main():
    assert build.app_version() == "3.8", "Unexpected desktop release version"
    result = {"round": 46, "app_version": "3.8", "extension_version": "3.8.46", "executables": [], "code": {}, "assets": {}}
    for key, target in build.TARGETS.items():
        if key == "proxy_only":
            continue
        name = target["name"]
        path = ROOT / "dist" / name / (name + ".exe")
        with pefile.PE(str(path), fast_load=True) as pe:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
            info = pe.VS_FIXEDFILEINFO[0]
            file_version = (info.FileVersionMS >> 16, info.FileVersionMS & 65535, info.FileVersionLS >> 16, info.FileVersionLS & 65535)
            product_version = (info.ProductVersionMS >> 16, info.ProductVersionMS & 65535, info.ProductVersionLS >> 16, info.ProductVersionLS & 65535)
        assert file_version == product_version == build.version_tuple(build.app_version()), (name, file_version, product_version)
        result["executables"].append({"app": name, "version": list(file_version), "bytes": path.stat().st_size})
    groups = {
        "MemuCode": ["app.version", "app.pricing", "app.api_budget", "app.chat", "app.cli.runtime", "app.cli.llm",
                     "app.cli.agent", "app.cli.file_cache", "app.providers.custom"],
        "MemuAI": ["app.version", "app.voice_downloads", "app.stt_providers", "app.panels_proxy_usage", "app.win_window_theme",
                   "app.cli.file_cache",
                   "app.telegram_use_api", "app.telegram_format", "app.dfy", "app.dfy_api", "app.schedule_rules_api", "app.flow_engine"],
        "MemuAIProxy": ["app.version", "app.main", "app.admin_api", "app.config", "app.api_budget", "app.model_refresh",
                        "app.pricing", "app.quota_planner", "app.chat", "app.providers.custom", "app.state", "app.model_seeds",
                        "app.providers.antigravity", "app.providers.antigravity_models",
                        "app.voice_downloads", "app.stt_providers", "app.panels_proxy_usage", "app.win_window_theme", "app.cli.file_cache"],
    }
    for name, modules in groups.items():
        folder = ROOT / "dist" / name
        package = folder / (name + ".pkg")
        reader = CArchiveReader(str(package if package.is_file() else folder / (name + ".exe")))
        pyz = reader.open_embedded_archive("PYZ.pyz")
        for module in modules:
            assert module in pyz.toc, module
            path = ROOT / (module.replace(".", "/") + ".py")
            assert signature(pyz.extract(module)) == signature(compile(path.read_bytes(), str(path), "exec", dont_inherit=True)), (name, module)
        result["code"][name] = modules
    for name, relative in (("MemuAIProxy", "app/static"), ("MemuAI", "app/module_builtins/music")):
        source = ROOT / relative
        packaged = ROOT / "dist" / name / "_internal" / relative
        count = 0
        for path in source.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            target = packaged / path.relative_to(source)
            assert target.is_file(), target
            assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest(), target
            count += 1
        result["assets"][name + "/" + relative] = count
    for relative in ("app/pgstatic/browser-extension/manifest.json",):
        source = ROOT / relative
        packaged = ROOT / "dist/MemuAI/_internal" / relative
        assert json.loads(source.read_text("utf-8"))["version"] == "3.8.46"
        assert packaged.read_bytes() == source.read_bytes(), "Desktop embeds an older Chrome extension"
    # The small protocol CLI lists/builds links locally and forwards execution
    # to the desktop. A new route must not pull the entire server/STT into it.
    cli = ROOT / "dist/memu/memu.exe"
    assert cli.stat().st_size < 5 * 1024 * 1024, "Lightweight memu CLI unexpectedly contains server code"
    pyz = CArchiveReader(str(cli)).open_embedded_archive("PYZ.pyz")
    assert "app.browser_extension_connect" not in pyz.toc
    assert not any(name.startswith(("av.", "faster_whisper.", "fastapi.", "pydantic_core.")) for name in pyz.toc)
    with tempfile.TemporaryDirectory(prefix="round46-memu-proof-", dir=ROOT / "build") as temporary:
        env = {**os.environ, "MEMUAI_DATA_DIR": temporary, "PYTHONUTF8": "1"}
        env.pop("MEMUAI_TOKEN", None)
        arguments = (["--version"], ["--json", "link", "--list"],
                     ["--json", "link", "browser-extension/connect", "challenge=" + "a" * 32, "extension_id=" + "e" * 32])
        for args in arguments:
            response = subprocess.run([str(cli), *args], cwd=ROOT, env=env, capture_output=True,
                                      timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            assert response.returncode == 0, response.stderr.decode("utf-8", "replace")
            output = response.stdout.decode("utf-8")
            if "--list" in args:
                assert any(row["name"] == "browser-extension/connect" for row in json.loads(output)["routes"])
            elif "browser-extension/connect" in args:
                assert json.loads(output)["link"].startswith("memuai://browser-extension/connect?")
    shipped = ROOT / "dist/MemuAI/MemuCLI/memu.exe"
    assert shipped.read_bytes() == cli.read_bytes(), "Desktop embeds an older memu CLI"
    result["memu_cli"] = {"commands_passed": 3, "bytes": cli.stat().st_size, "server_dependencies_excluded": True}
    (ROOT / "build/round46-compiled-version.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
