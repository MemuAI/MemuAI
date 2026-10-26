"""Round 26: static whole-project checks.

Guards whole classes of bugs instead of single lines:

* ``module.attr`` / ``from .module import name`` on a project module that has no such
  name (the class of «type object 'Image' has no attribute 'new'» and «websearch.web_fetch»);
* a coroutine of the project called as a bare statement without ``await`` (it never runs);
* a ``/api/...`` call of the UI (MemuAI, Quick Access, companion, Proxy panel) whose route
  is not registered, or is registered for another HTTP method, or a GET helper given a body;
* Telegram Mini App operations and pywebview bridge calls with no handler on the Python side;
* a ``typeof name === "function"`` guard in the MemuAI UI on a function that does not exist
  (such a guard silently disables the feature: ``MemuAI.exe --temporary-chat`` opened an
  ordinary chat because all three guarded names were never defined).
"""
import ast
import os
import re
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memuai-r26-static-"))

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()

def _py_modules():
    files = {}
    for pkg in ("app", "installer"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, pkg)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    mod = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
                    files[mod[:-9] if mod.endswith(".__init__") else mod] = path
    trees = {}
    for mod, path in files.items():
        with open(path, encoding="utf-8") as handle:
            trees[mod] = ast.parse(handle.read(), path)
    return files, trees

FILES, TREES = _py_modules()

def _is_pkg(mod):
    return FILES[mod].endswith("__init__.py")

def _resolve(cur, level, module):
    if not level:
        return module or ""
    parts = cur.split(".")
    if not _is_pkg(cur):
        parts = parts[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    base = ".".join(parts)
    return (base + "." + module if module else base) if base else (module or "")

def _top_names(tree):
    """Names a module defines at import time (incl. under if/try), plus star/__getattr__ flags."""
    names, dynamic = set(), False

    def target(node):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                target(item)
        elif isinstance(node, ast.Starred):
            target(node.value)

    def body(nodes):
        nonlocal dynamic
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for tg in node.targets:
                    target(tg)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                target(node.target)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        dynamic = True
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ("body", "orelse", "finalbody"):
                    body(getattr(node, field, None) or [])
                for handler in getattr(node, "handlers", None) or []:
                    body(handler.body)

    body(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            names.update(node.names)
    return names, dynamic or "__getattr__" in names

class PythonModuleAttributesTest(unittest.TestCase):
    def test_project_module_attributes_exist(self):
        defs, dynamic = {}, set()
        for mod, tree in TREES.items():
            defs[mod], is_dynamic = _top_names(tree)
            if is_dynamic:
                dynamic.add(mod)
        binds, problems = {}, []
        for mod, tree in TREES.items():
            bind = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    src = _resolve(mod, node.level, node.module)
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        full = src + "." + alias.name
                        if full in TREES:
                            bind[alias.asname or alias.name] = full
                        elif src in TREES and src not in dynamic and alias.name not in defs[src]:
                            problems.append(f"{FILES[mod]}:{node.lineno}: from {src} import {alias.name}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in TREES and alias.asname:
                            bind[alias.asname] = alias.name

            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for tg in node.targets:
                        if isinstance(tg, ast.Attribute) and isinstance(tg.value, ast.Name) and tg.value.id in bind:
                            defs[bind[tg.value.id]].add(tg.attr)
            binds[mod] = bind
        for mod, tree in TREES.items():
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
                    continue
                target = binds[mod].get(node.value.id)
                if not target or target in dynamic or node.attr.startswith("__") or node.attr in defs[target]:
                    continue
                if _is_pkg(target) and target + "." + node.attr in FILES:
                    continue
                problems.append(f"{FILES[mod]}:{node.lineno}: {node.value.id}.{node.attr} (not in {target})")
        self.assertEqual(sorted(set(problems)), [])

    def test_project_coroutines_are_awaited(self):
        problems = []
        async_top = {m: {n.name for n in t.body if isinstance(n, ast.AsyncFunctionDef)} for m, t in TREES.items()}
        for mod, tree in TREES.items():
            bind_mod, bind_fn = {}, set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    src = _resolve(mod, node.level, node.module)
                    for alias in node.names:
                        if src + "." + alias.name in TREES:
                            bind_mod[alias.asname or alias.name] = src + "." + alias.name
                        elif src in TREES and alias.name in async_top[src]:
                            bind_fn.add(alias.asname or alias.name)
            local_async = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
            local_sync = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
                    continue
                func = node.value.func
                bad = False
                if isinstance(func, ast.Name):
                    bad = (func.id in local_async and func.id not in local_sync) or func.id in bind_fn
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in bind_mod:
                    bad = func.attr in async_top[bind_mod[func.value.id]]
                if bad:
                    problems.append(f"{FILES[mod]}:{node.lineno}: {ast.unparse(node.value)[:80]} without await")
        self.assertEqual(problems, [])

def _scan_to(s, i, stop):
    """Index of the first top-level character of ``stop`` (or the closing bracket) from ``i``."""
    depth, quote, j = 0, None, i
    while j < len(s):
        c = s[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if quote == "`" and c == "$" and s[j + 1:j + 2] == "{":
                j = _scan_to(s, j + 2, "}") + 1
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif depth == 0 and c in stop:
            return j
        j += 1
    return j

def _url_of(expr):
    """'/api/a/' + id + '/b' → '/api/a/x/b'; a trailing non-literal after a full path is a query."""
    pieces, start, j, e = [], 0, 0, expr.strip()
    while True:
        k = _scan_to(e, j, "+")
        pieces.append(e[start:k])
        if k >= len(e) or e[k] != "+":
            break
        start = j = k + 1
    parts = []
    for piece in pieces:
        piece = piece.strip()
        m = re.fullmatch(r"""(['"`])(.*)\1""", piece, re.S)
        if m:
            parts.append(re.sub(r"\$\{[^}]*\}", "x", m.group(2)))
        else:
            if parts and not parts[-1].endswith("/"):
                break
            parts.append("x")
    return "".join(parts).split("?")[0].split("#")[0]

HELPERS = {
    ("app/pgstatic/quick/quick.js", "api"): "auto",
    ("app/pgstatic/deeplinks.js", "call"): "arg2post",
    ("app/pgstatic/memucode.js", "post"): "post",
    ("app/pgstatic/quick/quick-dfy.js", "post"): "post",
    ("app/pgstatic/quick/quick-dfy.js", "json"): "opts",
    ("app/pgstatic/quick/plugins/commands-settings.js", "call"): "arg2post",
    ("app/pgstatic/appearance.js", "api"): "arg2post",

    ("app/pgstatic/theme-editor.js", "api"): "arg2post",
    ("app/pgstatic/docs.js", "getJson"): "get",
    ("app/pgstatic/panels.js", "call"): "opts",
    ("app/pgstatic/dfy-client.js", "call"): "opts",
    ("app/pgstatic/modules.js", "apiJson"): "opts",

    ("app/pgstatic/hotkeys.js", "get"): "get",

    ("app/pgstatic/schedsend.js", "sendBeacon"): "post",

    ("app/pgstatic/quick/plugins/features.js", "api"): "arg2post",
    ("app/pgstatic/quick/sticker.js", "api"): "arg2post",
    ("app/pgstatic/quick/plugins/features-settings.js", "post"): "post",

    ("app/pgstatic/quick/plugins/wake.js", "call"): "arg2post",
    ("app/pgstatic/quick/plugins/wake-settings.js", "call"): "arg2post",

    ("app/pgstatic/quick/plugins/canvas-settings.js", "post"): "post",

    ("app/pgstatic/quick/layer.js", "api"): "arg2post",

    ("app/pgstatic/memory.js", "call"): "opts",
    ("app/pgstatic/memory.js", "act"): "opts",
    ("app/pgstatic/search.js", "http"): "opts",
    ("app/pgstatic/safe-mode.js", "post"): "post",
    ("app/pgstatic/sync-settings.js", "call"): "arg2post",

    ("app/pgstatic/stuck.js", "post"): "post",
    ("app/pgstatic/plan.js", "call"): "post",

    ("app/pgstatic/quick/plugins/explorer.js", "api"): "arg2post",
    ("app/pgstatic/quick/plugins/extras.js", "api"): "arg2post",
    ("app/pgstatic/quick/plugins/explorer-settings.js", "post"): "post",

    ("app/pgstatic/memunote-settings.js", "call"): "arg2post",

    ("app/pgstatic/integrations-ui.js", "call"): "arg2post",
    ("app/pgstatic/dfy-takeover.js", "call"): "post",

    ("app/pgstatic/module-apps.js", "api"): "arg2post",

    ("app/pgstatic/module-icons.js", "api"): "arg2post",
    ("app/pgstatic/panels-layout.js", "call"): "opts",

    ("app/static/reasoning_card.js", "post"): "post",

    ("app/pgstatic/askuser.js", "http"): "arg2post",
    ("app/pgstatic/canvas.js", "req"): "opts",
    ("app/pgstatic/flows.js", "call"): "post",
    ("app/pgstatic/inbox.js", "call"): "opts",
    ("app/pgstatic/memufiles-settings.js", "call"): "arg2post",
    ("app/pgstatic/quick/plugins/fsearch-settings.js", "post"): "post",
    ("app/pgstatic/plan-conds.js", "call"): "post",

    ("app/pgstatic/app-settings-form.js", "call"): "arg2post",
    ("app/pgstatic/module-integration.js", "json"): "arg2post",
    ("app/pgstatic/mcp-hub.js", "call"): "opts",
}
GLOBAL_HELPERS = {"api": "opts", "ctx.api": "opts", "fetch": "opts", "todoCall": "opts",
                  "EventSource": "get", "downloadAuthed": "get",

                  "MemuLive.stream": "get", "MemuLive.post": "post"}
STRING_METHODS = {"startsWith", "includes", "indexOf", "replace", "test", "match", "endsWith"}

_ROUTES = {}

def _routes():
    if _ROUTES:
        return _ROUTES
    from fastapi.routing import APIRoute, APIWebSocketRoute

    def flatten(routes, prefix=""):
        for route in routes:
            if isinstance(route, APIRoute):
                yield prefix + route.path, set(route.methods)
            elif isinstance(route, APIWebSocketRoute):
                yield prefix + route.path, {"WS"}
            elif hasattr(route, "original_router"):
                yield from flatten(route.original_router.routes,
                                   prefix + (getattr(route.include_context, "prefix", "") or ""))
            elif hasattr(route, "routes") and not hasattr(route, "app"):
                yield from flatten(route.routes, prefix + getattr(route, "path", ""))

    from app.main import create_app as proxy_app
    from app.playground_api import create_app as pg_app
    for name, factory in (("proxy", proxy_app), ("pg", pg_app)):
        rows = []
        for path, methods in flatten(factory().routes):
            rx = re.sub(r"\{[^}:]+:path\}", ".+", path)
            rx = re.sub(r"\{[^}]+\}", "[^/]+", rx)
            rows.append((re.compile("^" + rx + "$"), methods, path))
        _ROUTES[name] = rows
    return _ROUTES

def _segment_match(url, path):
    segs = url.split("/")
    if "x" not in segs:
        return False
    route = path.split("/")
    if len(segs) != len(route):
        return False
    return all(u == "x" or u == r or r.startswith("{") for u, r in zip(segs, route))

class FrontendBackendRoutesTest(unittest.TestCase):
    def test_every_ui_api_call_has_a_route_with_its_method(self):
        routes = _routes()
        self.assertGreater(len(routes["proxy"]), 50)
        self.assertGreater(len(routes["pg"]), 150)
        call = re.compile(r"""([A-Za-z_$][\w$.]*)\s*\(\s*(?=['"`]/api/)""")
        problems, checked = [], 0
        for root, which in (("app/pgstatic", "pg"), ("app/static", "proxy")):
            for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, root)):
                dirnames[:] = [d for d in dirnames if d != "vendor"]
                for name in filenames:
                    if not name.endswith(".js"):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
                    s = _read(rel)
                    for m in call.finditer(s):
                        func = m.group(1)
                        short = func.split(".")[-1]
                        if short in STRING_METHODS:
                            continue
                        a0 = m.end()
                        a1, end = _scan_to(s, a0, ","), _scan_to(s, a0, "")
                        url = _url_of(s[a0:a1])
                        rest = s[a1 + 1:end] if a1 < end else ""
                        where = f"{rel}:{s.count(chr(10), 0, m.start()) + 1}"
                        rule = (HELPERS.get((rel, short))
                                or ("auto" if func.endswith(("Q.api", "MemuQuick.api")) else None)
                                or GLOBAL_HELPERS.get(func) or GLOBAL_HELPERS.get(short))
                        if rule is None:
                            problems.append(f"{where}: unknown request helper {func}() — add it to HELPERS")
                            continue
                        explicit = re.search(r"\bmethod\s*:\s*['\"]([A-Za-z]+)", rest)
                        has_body = re.search(r"\bbody\b", rest) is not None
                        if rule == "post":
                            method = "POST"
                        elif rule == "get":
                            method = "GET"
                        elif rule == "arg2post":
                            method = "POST" if rest.strip() else "GET"
                        elif explicit:
                            method = explicit.group(1).upper()
                        elif has_body and rule == "auto":
                            method = "POST"
                        else:
                            method = "GET"
                            if has_body and short != "fetch":
                                problems.append(f"{where}: {func}({url}) sends a body without a method (GET)")
                        hits = [r for r in routes[which] if r[0].match(url) or _segment_match(url, r[2])]
                        checked += 1
                        if not hits:
                            problems.append(f"{where}: no route for {method} {url}")
                        elif method not in set().union(*(h[1] for h in hits)):
                            problems.append(f"{where}: {method} {url} — route allows "
                                            f"{sorted(set().union(*(h[1] for h in hits)))}")
        self.assertGreater(checked, 400)
        self.assertEqual(problems, [])

    def test_routes_are_unique_and_not_shadowed(self):
        problems = []
        for name, rows in _routes().items():
            seen = set()
            for i, (rx, methods, path) in enumerate(rows):
                for method in methods:
                    if (path, method) in seen:
                        problems.append(f"{name}: {method} {path} registered twice")
                    seen.add((path, method))
                if "{" not in path:
                    continue
                for _, later_methods, later in rows[i + 1:]:
                    if "{" not in later and rx.match(later) and methods & later_methods:
                        problems.append(f"{name}: {path} is registered before {later} and swallows it")
        self.assertEqual(problems, [])

    def test_tgapp_operations_have_handlers(self):
        js = "".join(_read(f"app/tgapp/{n}") for n in os.listdir(os.path.join(ROOT, "app/tgapp")) if n.endswith(".js"))
        used = set(re.findall(r"""\bapi\(\s*["']([a-z_.]+)["']""", js))
        py = "".join(_read(f"app/{n}") for n in os.listdir(os.path.join(ROOT, "app")) if n.endswith(".py"))
        py += _read("app/music/tgapp_ops.py")
        py += _read("app/learn/tgapp_ops.py")
        registered = set(re.findall(r"""@op\(\s*["']([a-z_.]+)["']""", py))
        self.assertTrue(used and registered)
        self.assertEqual(sorted(used - registered), [])

    def test_pywebview_bridge_calls_exist(self):
        installer_js = _read("installer/ui/app.js")
        bridge = {n.name for n in ast.walk(ast.parse(_read("installer/bridge.py"))) if isinstance(n, ast.FunctionDef)}
        calls = set(re.findall(r"""\bcall\(\s*["']([a-z_]+)["']""", installer_js))
        self.assertTrue(calls)
        self.assertEqual(sorted(calls - bridge), [])
        titlebar_js = _read("app/pgstatic/titlebar.js")
        win = {n.name for n in ast.walk(ast.parse(_read("app/win_titlebar.py"))) if isinstance(n, ast.FunctionDef)}
        calls = set(re.findall(r"""\bcall\(\s*["'](titlebar_[a-z_]+)["']""", titlebar_js))
        self.assertTrue(calls)
        self.assertEqual(sorted(calls - win), [])

class FrontendDeadGuardsTest(unittest.TestCase):
    BROWSER = {"EventSource", "requestAnimationFrame", "ResizeObserver", "Highlight", "IntersectionObserver",
               "MutationObserver", "queueMicrotask", "structuredClone", "fetch", "AbortController",
               "OffscreenCanvas", "createImageBitmap", "BroadcastChannel", "Notification", "MediaRecorder",
               "showSaveFilePicker", "showOpenFilePicker", "requestIdleCallback"}

    def test_typeof_function_guards_name_real_functions(self):
        problems = []
        for root in ("app/pgstatic", "app/static", "app/tgapp", "installer/ui"):
            texts = {}
            for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, root)):
                dirnames[:] = [d for d in dirnames if d != "vendor"]
                for name in filenames:
                    if name.endswith(".js"):
                        rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
                        texts[rel] = _read(rel)
            everything = "\n".join(texts.values())
            declared = set(re.findall(r"\b(?:function\s*\*?\s*|class\s+|const\s+|let\s+|var\s+)([A-Za-z_$][\w$]*)", everything))
            declared |= set(re.findall(r"\b(?:window|globalThis|self)\.([A-Za-z_$][\w$]*)\s*=(?!=)", everything))
            declared |= set(re.findall(r"[{,]\s*([A-Za-z_$][\w$]*)\s*(?=[,}])", everything))
            for rel, s in texts.items():
                for m in re.finditer(r"""typeof\s+(?:window\.)?([A-Za-z_$][\w$]*)\s*[!=]==?\s*["']function["']""", s):
                    if m.group(1) not in declared and m.group(1) not in self.BROWSER:
                        problems.append(f"{rel}:{s.count(chr(10), 0, m.start()) + 1}: typeof {m.group(1)}")
        self.assertEqual(problems, [])

    def test_temporary_chat_start_action_opens_a_temporary_chat(self):
        app_js = _read("app/pgstatic/app.js")
        block = app_js[app_js.index('get("start")'):]
        block = block[:block.index("})();")]
        self.assertRegex(block, r'requested === "temp"\)\s*return newChat\(true\)')
        self.assertRegex(app_js, r"\nfunction newChat\(temp = false\)")

        self.assertIn('"temp" if args.temporary_chat', _read("memuai.py"))

if __name__ == "__main__":
    unittest.main()
