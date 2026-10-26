"""Round 29 integration check (r29-integrate-tests): the frozen programs carry every app module they import.

* every ``app.*`` hidden import of MemuAI.spec / MemuAIProxy.spec / MemuCode.spec / MemuNote.spec and build.py
  TARGETS resolves to a module file and imports (non-Windows modules are only located, not executed);
* each *.spec names the same app.* hidden imports as its build.py target (they are generated from one another);
* modules loaded by name at run time (``importlib.import_module("app.x")``, Quick Ask plugin ``backend``) are listed
  for the program that loads them.
Run: python3 tests/round29_spec_imports_check.py   (exit code 1 on problems)
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-speccheck-"))

SPECS = {"MemuAI.spec": "memuai", "MemuAIProxy.spec": "proxy", "MemuCode.spec": "memucode", "MemuNote.spec": "memunote",
         "memu.spec": "memu",
         "MemuFiles.spec": "memufiles"}
WIN_ONLY = re.compile(r"(_win|win_|winsys|wake_audio|clr)$|^app\.win_")

def spec_hidden(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "hiddenimports" for t in node.targets):
            return list(ast.literal_eval(node.value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Analysis":
            for keyword in node.keywords:
                if keyword.arg == "hiddenimports":
                    return list(ast.literal_eval(keyword.value))
    return []

def main() -> int:
    import build
    problems = []

    generated = {"app." + Path(p).stem for p in (getattr(build, "OAUTH_BUILD", None),) if p}
    for spec, target in SPECS.items():
        path = ROOT / spec
        if not path.is_file():
            problems.append(f"{spec}: missing")
            continue
        names = spec_hidden(path)
        built = build.TARGETS[target]["hidden"]
        a = {n for n in names if n.startswith("app.")}
        b = {n for n in built if n.startswith("app.")}
        if a - b:
            problems.append(f"{spec}: app modules not in build.py TARGETS[{target!r}]: {sorted(a - b)}")
        if b - a:
            problems.append(f"build.py TARGETS[{target!r}]: app modules not in {spec}: {sorted(b - a)}")
        for name in sorted(a | b):
            if name in generated:
                continue
            if importlib.util.find_spec(name) is None:
                problems.append(f"{spec}: {name} does not exist")
                continue
            if WIN_ONLY.search(name):
                continue
            try:
                importlib.import_module(name)
            except Exception as exc:   # noqa: BLE001 — report every broken module
                problems.append(f"{spec}: import {name} failed: {type(exc).__name__}: {exc}")

    for name in build.TARGETS["proxy_only"]["hidden"]:
        if name.startswith("app.") and name not in generated and importlib.util.find_spec(name) is None:
            problems.append(f"build.py proxy_only: {name} does not exist")

    memuai = set(build.TARGETS["memuai"]["hidden"])
    plugins = json.loads((ROOT / "app/pgstatic/quick/plugins/index.json").read_text(encoding="utf-8"))["plugins"]
    for plugin in plugins:
        backend = plugin.get("backend")
        if backend and backend not in memuai:
            problems.append(f"Quick Ask plugin {plugin['id']}: backend {backend} is not a MemuAI hidden import")
    for py in (ROOT / "app").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"import_module\(\s*[\"'](app\.[\w.]+)[\"']", text):
            owners = [t for t, v in build.TARGETS.items() if name in v["hidden"]]
            if not owners:
                problems.append(f"{py.relative_to(ROOT)}: import_module({name!r}) is in no build target")
    for p in problems:
        print("PROBLEM:", p)
    print(f"{len(problems)} problems")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
