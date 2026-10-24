"""Read-only release proof for PE versions, financial code and shipped interfaces."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
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
    result = {"executables": [], "code": {}, "assets": {}}
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
        "MemuCode": ["app.version", "app.pricing", "app.api_budget", "app.chat", "app.cli.runtime", "app.cli.llm", "app.providers.custom"],
        "MemuAIProxy": ["app.version", "app.main", "app.admin_api", "app.config", "app.api_budget", "app.model_refresh",
                        "app.pricing", "app.quota_planner", "app.chat", "app.providers.custom"],
    }
    for name, modules in groups.items():
        folder = ROOT / "dist" / name
        package = folder / (name + ".pkg")
        reader = CArchiveReader(str(package if package.is_file() else folder / (name + ".exe")))
        pyz = reader.open_embedded_archive("PYZ.pyz")
        for module in modules:
            assert module in pyz.toc, module
            path = ROOT / (module.replace(".", "/") + ".py")
            assert signature(pyz.extract(module)) == signature(compile(path.read_text("utf-8"), str(path), "exec")), (name, module)
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
    (ROOT / "build/round44-compiled-version.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
