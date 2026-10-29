"""Round 34 (r34-quick-design, r34-quick-design-2): Quick Ask design fixes — static DOM/CSS/JS checks, no network.

1. Accent-filled buttons of the plugins use the theme token --on-accent (the --accent-ink / --accent-contrast /
   --accent-fg names exist in no theme, so the text fell back to white on a light accent — 2:1).
2. quick/quick-design.css is linked last: the bottom slot shrinks and scrolls, the idle lists step aside for a waiting
   DFY / Computer Use card and while a phrase is checked for a command, DFY head wraps, tip wraps < 520 px, one focus ring.
3. The empty-field placeholder drops «/ — команды» in a narrow window.
4. commands.js shows «Выполняю…» only after 280 ms and marks the check on <html>.
5. quick-md.js holds back a bare list marker while streaming; in a narrow window sentence-long table cells keep a
   readable width and short headers one line (rows were 100 px tall).
6. quick-copy.js places its menus by the layout size (not the qc-pop-scaled rect) — «⋯ Ещё» kept 8 px from the edge.
7. Settings: «Команды, голос и компаньон» → «Возможности», plugin «Новые возможности» → «Буфер, сниппеты и стикеры»
   (settings, Ctrl+K section, toasts, docs ru/en and their AI summaries).
8. «в Настройки →» → «в Настройках →» in quick.js.
9. Cache-busters of every changed file were bumped.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
QUICK = STATIC / "quick"

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

class Tokens(unittest.TestCase):
    def test_on_accent_token(self) -> None:
        files = ["quick/quick-dfy.css", "quick/plugins/canvas.css", "quick/layer.css", "quick/panel-text-editor.css",
                 "stuck.css", "memu-ctxmenu.js", "memory.css"]
        for rel in files:
            text = read(STATIC / rel)
            self.assertIsNone(re.search(r"--accent-(ink|contrast|fg)\b", text), rel)
            self.assertIn("--on-accent", text, rel)

class DesignSheet(unittest.TestCase):
    def setUp(self) -> None:
        self.html = read(QUICK / "index.html")
        self.css = read(QUICK / "quick-design.css")

    def test_linked_last(self) -> None:
        links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', self.html)
        self.assertTrue(links[-1].startswith("/quick/quick-design.css?v="), links[-1])

    def test_rules(self) -> None:
        css = self.css
        self.assertRegex(css, r"#qa-slot-bottom:not\(:empty\) \{ min-height: 0; flex-shrink: 1;[^}]*overflow-y: auto")
        self.assertIn('#qa-slot-bottom > .qa-plugin:is([data-plugin="dfy"], [data-plugin="computer"])', css)
        self.assertIn(":root[data-qa-cmd-check] #qa-suggest { display: none !important; }", css)
        self.assertIn(".qdfy-head { flex-wrap: wrap;", css)
        self.assertIn("@media (max-width: 520px)", css)
        self.assertRegex(css, r":where\(\.qa-card, \.qc-menu, \.qc-selbar\)[^{]*:focus-visible \{\s*outline: 2px solid")
        self.assertNotIn("!important", css.replace(":root[data-qa-cmd-check] #qa-suggest { display: none !important; }", ""))

class Behaviour(unittest.TestCase):
    def test_narrow_placeholder(self) -> None:
        js = read(QUICK / "quick.js")
        self.assertIn('innerWidth < 520 ? (name ? tr(`Спросите ${name}…`, `Ask ${name}…`) : tr("Спросите что угодно…", "Ask anything…"))', js)

    def test_command_check_without_flash(self) -> None:
        js = read(QUICK / "plugins" / "commands.js")
        self.assertIn("const showPending = setTimeout(() => pushCard(pending, true), 280);", js)
        self.assertIn('document.documentElement.dataset.qaCmdCheck = "1";', js)
        self.assertIn("clearTimeout(slow); clearTimeout(showPending);", js)
        self.assertIn("delete document.documentElement.dataset.qaCmdCheck;", js)
        self.assertNotIn("    pushCard(pending, true);\n", js)

    def test_stream_list_marker_waits(self) -> None:
        js = read(QUICK / "quick-md.js")
        line = next(l for l in js.splitlines() if "if (streaming) raw = raw.replace(/\\n[ \\t]*(" in l)
        rx = re.compile(r"\n[ \t]*(\d{1,3}[.)]?|[-*+])[ \t]*$")
        self.assertIn(r"(\d{1,3}[.)]?|[-*+])[ \t]*$/", line)
        for tail in ("1. Один\n2", "1. Один\n2.", "- a\n-", "- a\n  * "):
            self.assertIsNotNone(rx.search(tail), tail)
        for tail in ("1. Один\n2. Два", "Цена 2", "x\n2024 год"):
            self.assertIsNone(rx.search(tail), tail)

    def test_narrow_tables(self) -> None:
        js, css = read(QUICK / "quick-md.js"), read(QUICK / "quick-design.css")
        self.assertIn("`<td${c.length > 32 ? ' class=\"long\"' : \"\"}>${inline(c)}</td>`", js)
        self.assertIn("`<th${h.length <= 20 ? ' class=\"short\"' : \"\"}>${inline(h)}</th>`", js)
        self.assertIn("@media (max-width: 520px) { .qa-md td.long { min-width: 16em; } .qa-md th.short { white-space: nowrap; } }", css)

    def test_menus_use_layout_size(self) -> None:
        js = read(QUICK / "quick-copy.js")
        self.assertIn("m = { width: menu.offsetWidth, height: menu.offsetHeight };\n    const left =", js)
        self.assertIn("const m = { width: menu.offsetWidth, height: menu.offsetHeight };", js)
        self.assertIn("b = { width: bar.offsetWidth, height: bar.offsetHeight }", js)
        self.assertNotIn("m = menu.getBoundingClientRect()", js)
        self.assertNotIn("b = bar.getBoundingClientRect()", js)

class Names(unittest.TestCase):
    OLD = ("Команды, голос и компаньон", "Commands, voice and companion", "Новые возможности", "New features")

    def test_settings_section(self) -> None:
        js = read(STATIC / "quick-settings.js")
        self.assertIn('title: tr("Возможности", "Features"), icon: ICONS.cmd,', js)
        manifest = json.loads(read(QUICK / "plugins" / "index.json"))
        features = next(p for p in manifest["plugins"] if p["id"] == "features")
        self.assertEqual(features["title"], {"ru": "Буфер, сниппеты и стикеры", "en": "Clipboard, snippets and stickers"})
        self.assertGreaterEqual(int(features["v"].rsplit("-r", 1)[1]), 34, features["v"])
        fjs = read(QUICK / "plugins" / "features.js")
        self.assertIn('const SEC = () => t("Буфер, сниппеты и стикеры", "Clipboard, snippets and stickers");', fjs)
        self.assertEqual(fjs.count("Быстрый доступ → Возможности → Буфер, сниппеты и стикеры"), 2)
        self.assertIn('title: { ru: "Буфер, сниппеты и стикеры", en: "Clipboard, snippets and stickers" }',
                      read(QUICK / "plugins" / "features-settings.js"))

    def test_old_names_gone_from_ui_and_docs(self) -> None:
        paths = [STATIC / "quick-settings.js", QUICK / "plugins" / "features.js", QUICK / "plugins" / "features-settings.js",
                 QUICK / "plugins" / "index.json", QUICK / "plugins" / "wake-settings.js",
                 ROOT / "app" / "docs" / "summaries" / "ru.json", ROOT / "app" / "docs" / "summaries" / "en.json"]
        paths += sorted((ROOT / "app" / "docs" / "ru").glob("*.md")) + sorted((ROOT / "app" / "docs" / "en").glob("*.md"))
        for path in paths:
            text = read(path)

            text = re.sub(r"\(Round 34: was «[^»]+»\)|; «New features» before round 34|// Round 34: was «[^»]+»", "", text)
            for old in self.OLD:
                self.assertNotIn(old, text, f"{path.name}: {old}")
        self.assertIn("Быстрый доступ → Возможности → Буфер, сниппеты и стикеры", read(ROOT / "app" / "docs" / "ru" / "quick-ask.md"))
        self.assertIn("Quick Ask → Features → Clipboard, snippets and stickers", read(ROOT / "app" / "docs" / "en" / "quick-ask.md"))

    def test_settings_grammar(self) -> None:
        self.assertNotIn("в Настройки →", read(QUICK / "quick.js"))

class CacheBusters(unittest.TestCase):
    def test_bumped(self) -> None:
        index, quick, layer = read(STATIC / "index.html"), read(QUICK / "index.html"), read(QUICK / "layer.html")
        for ref in ("/memory.css?v=1.2-r40", "/stuck.css?v=1.1-r34", "/memu-ctxmenu.js?v=4.1", "/quick-settings.js?v=1.2-r34"):
            self.assertIn(ref, index)
        for ref in ("/quick/quick-dfy.css?v=1.3-r34", "/stuck.css?v=1.1-r34", "/quick/quick-md.js?v=1.3-r34",
                    "/quick/quick-copy.js?v=1.1-r34", "/quick/quick-design.css?v="):
            self.assertIn(ref, quick)
        for ref in ("/quick/layer.css?v=1.3-r34", "/memu-ctxmenu.js?v=4.1"):
            self.assertIn(ref, layer)
        self.assertIn("/quick/quick-md.js?v=1.3-r34", read(QUICK / "sticker.html"))
        self.assertIn('const TEXT_V = "?v=1.1-r34";', read(QUICK / "plugins" / "canvas-embed.js"))
        self.assertIn("/quick/panel-text-editor.css?v=1.1-r34", read(QUICK / "plugins" / "canvas-look-settings.js"))
        self.assertIn('loadScript("/memu-ctxmenu.js?v=4.1")', read(QUICK / "plugins" / "canvas.js"))
        manifest = {p["id"]: p for p in json.loads(read(QUICK / "plugins" / "index.json"))["plugins"]}
        for plugin in ("canvas", "commands", "features"):
            self.assertGreaterEqual(int(manifest[plugin]["v"].rsplit("-r", 1)[1]), 34, (plugin, manifest[plugin]["v"]))

if __name__ == "__main__":
    unittest.main()

class SoftTerminalContrast(unittest.TestCase):
    def test_soft_and_terminal_small_labels_are_lifted(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parents[1] / "app/pgstatic/quick/quick-design.css").read_text(encoding="utf-8")
        self.assertIn('[data-design="soft"], [data-design="terminal"]', css)
        self.assertIn(".qcu-label", css.split('[data-design="terminal"]')[-2])
