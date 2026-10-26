"""Round 40: what a PyInstaller target really pulls in — a static import graph of the project's own code.

PyInstaller follows EVERY import statement of a module it analyses, including the lazy ones inside functions
and ``try`` blocks. One lazy ``from . import playground_api`` in a helper that MemuNote reaches drags half of
``app.*`` and their third-party packages (anthropic, pygments, rich, …) into MemuNote's analysis — minutes of
build time and megabytes of payload. This tool walks the same edges with ``ast`` (no PyInstaller needed):

    python tools/import_graph.py memunote            # counts + why each heavy package is reached
    python tools/import_graph.py memufiles --why anthropic
    python tools/import_graph.py --all --json build/import-graph.json

Targets and their hidden imports / excludes come from ``build.py`` (TARGETS). Third-party packages are reported
by top-level name only (their own internal imports are PyInstaller's business). tests/round40_build_icons_test.py
uses :func:`analyse` to keep the light targets light.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}

def _module_file(name: str) -> Optional[Path]:
    """``app.x.y`` → its .py (module or package ``__init__``); ``app`` is a namespace package."""
    parts = name.split(".")
    if parts[0] != "app":
        return None
    base = ROOT.joinpath(*parts)
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None

def _is_package(name: str) -> bool:
    return name == "app" or (ROOT.joinpath(*name.split(".")) / "__init__.py").is_file()

def _imports(path: Path, name: str) -> Iterable[str]:
    """Every module an ``import`` statement anywhere in ``path`` names (absolute names)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, SyntaxError, ValueError):
        return []
    package = name if _is_package(name) and path.name == "__init__.py" else name.rsplit(".", 1)[0]
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1]) if node.level > 1 else package
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
            else:
                base = node.module or ""
            if not base:
                continue
            out.append(base)
            for alias in node.names:
                if alias.name != "*":
                    out.append(f"{base}.{alias.name}")
    return out

def _excluded(name: str, excludes: Set[str]) -> bool:
    parts = name.split(".")
    return any(".".join(parts[:i]) in excludes for i in range(1, len(parts) + 1))

def analyse(entry: str, hidden: Iterable[str] = (), excludes: Iterable[str] = ()) -> Dict[str, object]:
    """BFS over the project's modules from ``entry`` (a script in the repo root) + ``hidden``.

    Returns ``{"app": {module: parent}, "external": {top package: (parent module, …)}}`` — parents are the
    first importer found (the shortest chain), enough to answer «why is X in this build»."""
    excludes = set(excludes)
    parent: Dict[str, str] = {}
    external: Dict[str, str] = {}
    queue = deque()
    script = ROOT / entry
    for name in _imports(script, "__main__"):
        queue.append((name, entry))
    for name in hidden:
        queue.append((name, "<hiddenimports>"))
    while queue:
        name, via = queue.popleft()
        if _excluded(name, excludes):
            continue
        top = name.split(".")[0]
        if top != "app":
            if top not in STDLIB and top not in external and not _excluded(top, excludes):
                external[top] = via
            continue

        parts = name.split(".")
        for i in range(2, len(parts)):
            pkg = ".".join(parts[:i])
            if _module_file(pkg) and pkg not in parent and not _excluded(pkg, excludes):
                parent[pkg] = via
                for sub in _imports(_module_file(pkg), pkg):
                    queue.append((sub, pkg))
        path = _module_file(name)
        if path is None or name in parent:
            continue
        parent[name] = via
        for sub in _imports(path, name):
            queue.append((sub, name))
    return {"app": parent, "external": external}

def chain(result: Dict[str, object], name: str) -> List[str]:
    parents: Dict[str, str] = dict(result["app"])  # type: ignore[arg-type]
    externals: Dict[str, str] = result["external"]  # type: ignore[assignment]
    out = [name]
    current = externals.get(name) if name in externals else parents.get(name)
    while current and current not in out:
        out.append(current)
        current = parents.get(current)
    return out

def target(key: str) -> Tuple[str, List[str], List[str]]:
    sys.path.insert(0, str(ROOT))
    import build  # noqa: WPS433 — the one place targets are defined
    spec = build.TARGETS[key]
    hidden = list(spec["hidden"]) + [f"{name}" for name in spec.get("collect", [])]
    return spec["entry"], hidden, list(spec.get("exclude", []))

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("targets", nargs="*", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--why", action="append", default=[])
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    import build
    keys = list(build.TARGETS) if args.all or not args.targets else args.targets
    dump = {}
    for key in keys:
        entry, hidden, excludes = target(key)
        result = analyse(entry, hidden, excludes)
        apps, ext = result["app"], result["external"]
        print(f"{key}: {len(apps)} app.* modules, third-party: {', '.join(sorted(ext))}")
        for name in args.why:
            print(f"  why {name}: " + " <- ".join(chain(result, name)) if (name in apps or name in ext) else f"  {name}: not reached")
        dump[key] = {"app": sorted(apps), "external": sorted(ext)}
    if args.json:
        Path(args.json).write_text(json.dumps(dump, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
