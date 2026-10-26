"""Standalone check that the packaged app can read its built-in module packages.

This is the exact failure the round-7 report described: in the installed .exe
every built-in module opened an empty window, because ``app/module_builtins``
was not bundled next to the code. This script is frozen with the same
``--add-data`` entry the real build uses, then run to prove the files are there.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import modules_host as host  # noqa: E402

def main() -> int:
    report = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "meipass": getattr(sys, "_MEIPASS", ""),
        "builtin_dir": str(host.builtin_dir()),
        "builtin_dir_exists": host.builtin_dir().is_dir(),
    }
    if not report["frozen"]:
        report["error"] = "this check must run from the frozen executable"
        print(json.dumps(report, ensure_ascii=False))
        return 2

    root = Path(tempfile.mkdtemp(prefix="memuai-frozen-"))
    os.environ["MEMUAI_DATA_DIR"] = str(root)
    rows = host.list_modules(root)
    module_ids = [row["id"] for row in rows]
    report["modules"] = module_ids

    if not module_ids:
        report["problems"] = ["в бундле нет ни одного встроенного модуля"]
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1
    sizes = {}
    for module_id in module_ids:
        package = host.load_package(root, module_id)
        sizes[module_id] = {"html": len(package["html"]), "css": len(package["css"]),
                            "js": len(package["js"]), "missing": package["missing"]}
        host.configure_module(root, module_id, enabled=True)
        document = host.module_document(package, {}, env={"lang": "ru", "theme": "dark"})
        sizes[module_id]["document"] = len(document)
        sizes[module_id]["has_bridge"] = "window.Memu" in document
    report["packages"] = sizes

    problems = []
    if not report["builtin_dir_exists"]:
        problems.append("builtin_dir does not exist inside the bundle")
    expected = set(host.BUILTIN_ORDER)
    missing_modules = sorted(expected - set(module_ids))
    if missing_modules:
        problems.append(f"builtin packages missing from the bundle: {missing_modules}")
    for module_id, info in sizes.items():
        if info["missing"]:
            problems.append(f"{module_id}: missing {info['missing']}")
        if info["js"] < 1000:
            problems.append(f"{module_id}: script is empty")
        if not info["has_bridge"]:
            problems.append(f"{module_id}: document has no bridge")
    report["problems"] = problems
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if problems else 0

if __name__ == "__main__":
    raise SystemExit(main())