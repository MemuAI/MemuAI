"""Text in the MemuAI and Proxy windows can be selected and copied.

pywebview disables selection by default (``text_select=False`` paints ``user-select: none`` over the whole
page), so answers, errors, paths and settings could not be copied. The windows now allow it, and the pages
keep only their controls unselectable. Static checks: no pywebview window is opened here.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def _calls(rel: str, name: str) -> list[ast.Call]:
    tree = ast.parse(_read(rel))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called == name:
                out.append(node)
    return out

def _keyword(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return ast.literal_eval(kw.value)
    return None

def _none_rule(css: str) -> str:
    """The `:where(...)` selector list that turns selection off for controls."""
    match = re.search(r":where\(([^{}]*?)\)\s*\{\s*-webkit-user-select:\s*none;\s*user-select:\s*none;\s*\}", css, re.S)
    if not match:
        raise AssertionError("no :where(...) { user-select: none } rule for controls")
    return match.group(1)

class DesktopWindowsTest(unittest.TestCase):
    def test_shell_allows_selection_by_default(self):
        tree = ast.parse(_read("app/desktop.py"))
        init = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "__init__"
                    and any(arg.arg == "text_select" for arg in node.args.args + node.args.kwonlyargs))
        names = [arg.arg for arg in init.args.args]
        defaults = dict(zip(names[len(names) - len(init.args.defaults):], init.args.defaults))
        self.assertIs(ast.literal_eval(defaults["text_select"]), True)
        self.assertIn('extra["text_select"] = bool(self.text_select)', _read("app/desktop.py"))

    def test_memuai_and_proxy_windows_do_not_turn_it_off(self):
        for rel in ("memuai.py", "app/desktop.py"):
            calls = _calls(rel, "DesktopShell")
            self.assertTrue(calls, rel)
            for call in calls:
                self.assertNotEqual(_keyword(call, "text_select"), False, rel)

    def test_module_and_memunote_windows_allow_selection(self):
        self.assertIn("text_select=True", _read("app/module_apps.py"))
        self.assertIn("text_select=True", _read("app/memunote/desktop.py"))

    def test_memufiles_keeps_rubber_band_selection(self):
        # MemuFiles selects files with a frame; a text selection would fight the rubber band.
        self.assertIn("text_select=False", _read("app/memufiles/desktop.py"))

class PageStylesTest(unittest.TestCase):
    def test_controls_stay_unselectable_and_fields_selectable(self):
        for rel in ("app/pgstatic/style.css", "app/static/style.css"):
            css = _read(rel)
            selectors = _none_rule(css)
            for needed in ("button", '[role="tab"]', '[role="switch"]', "svg"):
                self.assertIn(needed, selectors, f"{rel}: {needed}")
            self.assertRegex(css, r":where\(input, textarea, select, \[contenteditable\]:not\(\[contenteditable=\"false\"\]\), \.selectable\)\s*\{\s*-webkit-user-select:\s*text;\s*user-select:\s*text;")

    def test_shortcut_keys_are_copied_with_the_text(self):
        # `user-select: none` drops an element's text from a copied selection: "Press Ctrl+K" would become "Press ".
        for rel in ("app/pgstatic/style.css", "app/static/style.css"):
            self.assertNotRegex(_none_rule(_read(rel)), r"(^|[\s,(])kbd([\s,)]|$)", rel)

    def test_page_body_is_not_unselectable(self):
        for rel in ("app/pgstatic/style.css", "app/static/style.css"):
            for block in re.findall(r"(?:^|\n)\s*(?:html|body)\s*(?:,\s*(?:html|body)\s*)?\{([^}]*)\}", _read(rel)):
                self.assertNotRegex(block, r"user-select:\s*none", rel)

if __name__ == "__main__":
    unittest.main()
