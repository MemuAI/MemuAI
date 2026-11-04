# -*- coding: utf-8 -*-
"""The shared model selector: component files, presets, host inlining,
and adoption by every built-in module.

The component lives in ``app/pgstatic/model-selector.js`` /
``model-selector.css`` and is inlined into every module document by
``app/modules_host.py``. Behaviour (provider names, search, favourites,
mountSelect sync) is exercised in ``tests/model_selector_dom_test.cjs``;
this file checks the static contracts: the files exist, the presets are
real (JS + CSS agree), the host inlines the component, and no built-in
module still renders a raw ``provider`` field as the provider label.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PGSTATIC = ROOT / "app" / "pgstatic"
BUILTINS = ROOT / "app" / "module_builtins"

PRESETS = ["dropdown", "inline-list", "grid", "cards", "compact", "full",
           "circle", "rail", "segmented", "chips", "table", "minimal"]

# Files that choose a model for the user, per module. Music renders its
# pickers from feat-ai.js (a @memu-part of main.js); the rest from
# main.js / create.js / extra.js.
MODULE_JS = {
    "translator": ["main.js"],
    "design": ["main.js"],
    "studio": ["main.js"],
    "memudb": ["main.js"],
    "learn": ["main.js", "create.js", "extra.js"],
    "characters": ["main.js"],
    "benchmarks": ["main.js"],
    "music": ["feat-ai.js"],
    "catalog": ["main.js"],
    "api-models": ["main.js"],
    "meet": ["main.js"],
    "utils": ["main.js"],
}


def _has_fastapi() -> bool:
    try:
        import fastapi  # noqa: F401
    except ImportError:
        return False
    return True


class ComponentFilesTest(unittest.TestCase):
    def test_component_files_exist_and_are_crlf(self) -> None:
        for name in ("model-selector.js", "model-selector.css"):
            path = PGSTATIC / name
            self.assertTrue(path.is_file(), name)
            self.assertIn(b"\r\n", path.read_bytes()[:4000], name)

    def test_presets_are_declared_in_js_and_styled_in_css(self) -> None:
        js = (PGSTATIC / "model-selector.js").read_text(encoding="utf-8")
        css = (PGSTATIC / "model-selector.css").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(PRESETS), 10)
        for preset in PRESETS:
            self.assertIn('"%s"' % preset, js, preset)
            self.assertIn('[data-preset="%s"]' % preset, css, preset)

    def test_component_never_returns_custom_as_a_provider_label(self) -> None:
        js = (PGSTATIC / "model-selector.js").read_text(encoding="utf-8")
        self.assertIn("External API", js)
        self.assertNotIn('return "custom"', js)
        self.assertNotIn("return 'custom'", js)
        # The placeholder filter is what keeps a "custom" provider_label
        # from ever reaching the UI.
        self.assertIn('s === "custom"', js)

    def test_component_css_has_no_scrollbar_rules(self) -> None:
        # Scrollbar policy (MEMORY.md): custom scrollbars or none; the
        # shared component must not introduce its own scrollbar rules.
        # The header comment mentions scrollbars on purpose, so strip
        # comments before looking for real rules.
        css = (PGSTATIC / "model-selector.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for rule in ("::-webkit-scrollbar", "scrollbar-width",
                     "scrollbar-color", "scrollbar-gutter"):
            self.assertNotIn(rule, css, rule)

    def test_component_documents_its_api(self) -> None:
        js = (PGSTATIC / "model-selector.js").read_text(encoding="utf-8")
        for token in ("mountSelect", "renderList", "providerName",
                      "groupByProvider", "filterModels", "getFavorites",
                      "noteRecent", "PRESETS"):
            self.assertIn(token, js, token)
        self.assertIn("MemuModelSelector.mountSelect", js)


class AdoptionTest(unittest.TestCase):
    def test_every_builtin_module_references_the_selector(self) -> None:
        manifests = sorted(p.parent.name for p in BUILTINS.glob("*/manifest.json"))
        self.assertEqual(sorted(MODULE_JS), manifests)
        for module_id, files in MODULE_JS.items():
            folder = BUILTINS / module_id
            hits = []
            for name in files:
                code = (folder / name).read_text(encoding="utf-8")
                if "MemuModelSelector" in code:
                    hits.append(name)
            self.assertTrue(hits, "%s does not reference MemuModelSelector" % module_id)

    def test_no_module_renders_a_raw_provider_field(self) -> None:
        # A bare `.provider` read is only acceptable where the value is
        # guarded (characters' providerTitle fallback, which refuses
        # "custom") or comes from an external catalogue (catalog's
        # models.dev card, whose provider is the catalogue's own name).
        allowed = ('!== "custom"', "Models.dev", "provider.id", "provider.name")
        offenders = []
        for module_id, files in MODULE_JS.items():
            for name in files:
                path = BUILTINS / module_id / name
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if re.search(r"\.provider(?![_a-zA-Z])", line):
                        if "providerName" in line or "provider_label" in line:
                            continue
                        if any(token in line for token in allowed):
                            continue
                        offenders.append("%s/%s:%d: %s" % (module_id, name, lineno, line.strip()))
        self.assertEqual(offenders, [])

    def test_no_module_uses_custom_as_a_provider_label_fallback(self) -> None:
        # The word "custom" legitimately names unrelated features
        # (custom sections, custom API sources, custom prompts), so only
        # the label-fallback shapes are forbidden: a fallback, a return
        # or a concatenation that would print "custom" as the provider.
        # Guards such as `provider !== "custom"` are fine, and the DOM
        # test proves providerName() never returns it.
        forbidden = re.compile(
            r"(\|\||\?\s*:|return|\+)\s*[\"']custom[\"']")
        for module_id, files in MODULE_JS.items():
            for name in files:
                code = (BUILTINS / module_id / name).read_text(encoding="utf-8")
                for match in forbidden.finditer(code):
                    context = code[max(0, match.start() - 60):match.end() + 20]
                    self.fail("%s/%s uses 'custom' as a provider label: %r"
                              % (module_id, name, context))


class HostInliningTest(unittest.TestCase):
    @unittest.skipUnless(_has_fastapi(), "fastapi is not installed in this VM")
    def test_host_inlines_the_selector_into_every_module_document(self) -> None:
        from app import modules_host as host
        with tempfile.TemporaryDirectory() as temp:
            for module_id in host.BUILTIN_ORDER:
                package = host.load_package(Path(temp), module_id)
                self.assertFalse(package["missing"], module_id)
                doc = host.module_document(package)
                self.assertIn("MemuModelSelector", doc, module_id)
                self.assertIn('data-preset="dropdown"', doc, module_id)


class SharedPinsTest(unittest.TestCase):
    """Pinned models are one shared list (preferences pinned_models):
    the bridge in modules_host.py, the host dispatch in modules.js,
    and the selector's sync API. No new server endpoint is needed:
    the host writes through the existing /api/pg/preferences."""

    def test_bridge_exposes_shared_pin_calls(self) -> None:
        bridge = (ROOT / "app" / "modules_host.py").read_text(encoding="utf-8")
        self.assertIn("pinnedModels: function", bridge)
        self.assertIn("setModelPinned: function", bridge)
        self.assertIn('"models_pinned": "models"', bridge)
        self.assertIn('"models_pin": "models"', bridge)
        self.assertIn("pinned_models", bridge)

    def test_host_dispatch_serialises_pin_writes(self) -> None:
        js = (PGSTATIC / "modules.js").read_text(encoding="utf-8")
        self.assertIn('case "models_pinned"', js)
        self.assertIn('case "models_pin"', js)
        self.assertIn("pinQueue", js)
        self.assertIn("/api/pg/preferences", js)
        self.assertIn("auto_pin_suppressed", js)
        self.assertIn('broadcastEvent("model-pins"', js)
        self.assertIn("renderModelDD", js)

    def test_preview_dispatch_answers_pin_calls(self) -> None:
        js = (PGSTATIC / "modules.js").read_text(encoding="utf-8")
        self.assertIn("previewPinned", js)

    def test_selector_exposes_pin_sync_api(self) -> None:
        js = (PGSTATIC / "model-selector.js").read_text(encoding="utf-8")
        for token in ("syncPins", "getPinned", "setPinned", "providerHue",
                      "Закреплённые", "memu-model-selector:shared-pinned",
                      "Memu.pinnedModels", "Memu.setModelPinned"):
            self.assertIn(token, js, token)
        # A "custom-..." id names no provider: never a "Custom" label.
        self.assertNotIn('if (prefix) return prettifyToken(prefix);', js)

    def test_no_new_presets_were_created(self) -> None:
        js = (PGSTATIC / "model-selector.js").read_text(encoding="utf-8")
        css = (PGSTATIC / "model-selector.css").read_text(encoding="utf-8")
        self.assertEqual(len(PRESETS), 12)
        for preset in PRESETS:
            self.assertIn('[data-preset="%s"]' % preset, css, preset)
        declared = re.search(r"var PRESETS = \[([^\]]+)\]", js)
        self.assertIsNotNone(declared)
        self.assertEqual(len(re.findall(r'"[a-z-]+"', declared.group(1))), 12)


class SettingsRedesignTest(unittest.TestCase):
    def test_settings_cards_carry_per_module_accents_and_hierarchy(self) -> None:
        js = (PGSTATIC / "modules.js").read_text(encoding="utf-8")
        css = (PGSTATIC / "modules-settings.css").read_text(encoding="utf-8")
        self.assertIn("--mod-accent", js)
        self.assertIn("ms-card-access", js)
        self.assertIn("ms-inspector-lead", js)
        self.assertIn("--mod-accent", css)
        self.assertIn(".ms-card-access", css)
        self.assertIn(".ms-inspector-lead", css)
        self.assertIn("ms-inspector-in", css)

    def test_settings_motion_respects_reduced_motion(self) -> None:
        css = (PGSTATIC / "modules-settings.css").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("transition", css)
        reduced = css[css.rindex("prefers-reduced-motion"):]
        self.assertIn("animation: none", reduced)

    def test_settings_keeps_all_existing_functions(self) -> None:
        js = (PGSTATIC / "modules.js").read_text(encoding="utf-8")
        for token in ("data-module-enabled", "data-ms-toggle", "data-module-open",
                      "data-module-more", "data-module-slot", "data-module-hidden",
                      "data-module-resizable", "ms-permissions", "modules-filter",
                      "data-ms-filter"):
            self.assertIn(token, js, token)


class SelectorColoursTest(unittest.TestCase):
    def test_kind_badges_are_coloured_per_kind_in_css(self) -> None:
        css = (PGSTATIC / "model-selector.css").read_text(encoding="utf-8")
        for kind in ("chat", "image", "video", "music", "audio", "stt",
                     "tts", "embedding", "rerank", "decisions"):
            self.assertIn('[data-kind="%s"]' % kind, css, kind)
        self.assertIn("--ms-prov-h", css)
        self.assertIn("data-prov", css)

    def test_characters_assist_model_uses_the_shared_selector(self) -> None:
        assist = (BUILTINS / "characters" / "assist.js").read_text(encoding="utf-8")
        main = (BUILTINS / "characters" / "main.js").read_text(encoding="utf-8")
        self.assertIn("data-assist-model", assist)
        self.assertIn("providerLabel", assist)
        self.assertIn("data-assist-model", main)
        self.assertIn('preset: "compact"', main)


if __name__ == "__main__":
    unittest.main()
