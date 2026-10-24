"""Module translations: storage, validation, the bridge and the shipped modules."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import modules_host as host
from app.modules_host import (create_module, export_package, i18n_keys_used, import_package,
                              load_package, module_document, normalize_i18n, update_code,
                              validate_module_source)

ROOT = Path(__file__).resolve().parents[1]
BUILTINS = ROOT / "app" / "module_builtins"


def _literal_keys(js: str):
    """Keys passed as plain literals to t()/Memu.t() in a module script."""
    return set(re.findall(r'\b(?:Memu\.)?t\(\s*"([A-Za-z0-9_.]+)"\s*[,)]', js))


class NormalisationTests(unittest.TestCase):
    def test_shapes_are_kept_and_junk_is_dropped(self) -> None:
        clean = normalize_i18n({
            "ru": {"a": "б", "n": {"one": "{count} файл", "few": "{count} файла", "bogus": "x"}, "num": 3},
            "en": {"a": "b", "flag": True, "list": ["x"]},
            "bad locale": {"a": "c"}, "de": "not a table",
        })
        self.assertEqual(set(clean), {"ru", "en"})
        self.assertEqual(clean["ru"]["n"], {"one": "{count} файл", "few": "{count} файла"})
        self.assertEqual(clean["ru"]["num"], "3")
        self.assertNotIn("flag", clean["en"])
        self.assertNotIn("list", clean["en"])

    def test_json_text_is_parsed_and_broken_json_is_an_error(self) -> None:
        self.assertEqual(normalize_i18n('{"en": {"x": "y"}}'), {"en": {"x": "y"}})
        self.assertEqual(normalize_i18n(""), {})
        with self.assertRaises(ValueError):
            normalize_i18n('{"en": {"x": }')
        with self.assertRaises(ValueError):
            normalize_i18n(["not", "an", "object"])

    def test_used_keys_come_from_markup_and_script(self) -> None:
        keys = i18n_keys_used('<b data-i18n="a"></b><input data-i18n-placeholder="b">',
                              'Memu.t("c"); Memu.i18n.t(\'d\'); el.innerHTML = `<i data-i18n="e"></i>`;')
        self.assertEqual(keys, ["a", "b", "c", "d", "e"])


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="memu-i18n-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _create(self, **extra):
        spec = {"id": "hello", "title": "Привет", "html": '<b data-i18n="title"></b>', "js": "",
                "i18n": {"ru": {"title": "Привет"}, "en": {"title": "Hello"}}, **extra}
        return create_module(self.root, spec)

    def test_translations_are_written_read_and_survive_a_code_save(self) -> None:
        self._create(default_locale="en")
        package = load_package(self.root, "hello")
        self.assertEqual(package["i18n"]["en"]["title"], "Hello")
        self.assertEqual(package["locales"], ["en", "ru"])
        self.assertEqual(package["default_locale"], "en")
        self.assertTrue((self.root / "custom" / "hello" / "i18n.json").is_file())
        # A save that does not send translations keeps the stored ones.
        update_code(self.root, "hello", {"html": '<b data-i18n="title"></b>!'})
        self.assertEqual(load_package(self.root, "hello")["i18n"]["ru"]["title"], "Привет")
        # An explicit empty set removes the file.
        update_code(self.root, "hello", {"i18n": ""})
        self.assertEqual(load_package(self.root, "hello")["i18n"], {})
        self.assertFalse((self.root / "custom" / "hello" / "i18n.json").exists())

    def test_broken_json_is_refused_on_save_and_ignored_on_read(self) -> None:
        self._create()
        with self.assertRaises(ValueError):
            update_code(self.root, "hello", {"i18n": '{"ru": '})
        (self.root / "custom" / "hello" / "i18n.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(load_package(self.root, "hello")["i18n"], {})

    def test_export_and_import_carry_translations(self) -> None:
        self._create()
        package = json.loads(json.dumps(export_package(self.root, "hello"), ensure_ascii=False))
        self.assertIn("i18n.json", package["files"])
        import_package(self.root, package, new_id="hello-copy")
        self.assertEqual(load_package(self.root, "hello-copy")["i18n"]["en"]["title"], "Hello")

    def test_editing_a_builtin_keeps_its_shipped_translations(self) -> None:
        update_code(self.root, "translator", {"title": "Мой переводчик"})
        package = load_package(self.root, "translator")
        self.assertIn("en", package["i18n"])
        self.assertEqual(package["i18n"]["en"]["translate"], "Translate")


class ValidationTests(unittest.TestCase):
    def test_missing_keys_are_reported_per_locale(self) -> None:
        result = validate_module_source({
            "html": '<b data-i18n="title"></b><i data-i18n="sub"></i>', "js": 'Memu.t("go")',
            "i18n": {"ru": {"title": "Т", "sub": "П", "go": "Г"}, "en": {"title": "T"}}})
        rows = [row for row in result["problems"] if row["field"] == "i18n"]
        self.assertEqual(len(rows), 1)
        self.assertIn("«en»", rows[0]["ru"])
        self.assertIn("sub", rows[0]["ru"])
        self.assertIn("go", rows[0]["ru"])

    def test_keys_without_any_translation_are_a_warning(self) -> None:
        result = validate_module_source({"html": '<b data-i18n="title"></b>', "js": ""})
        self.assertFalse(result["ok"])
        self.assertTrue(any(row["field"] == "i18n" and row["level"] == "warn" for row in result["problems"]))

    def test_broken_translation_json_is_a_warning_not_a_crash(self) -> None:
        result = validate_module_source({"html": "", "js": "", "i18n": "{nope"})
        self.assertTrue(any(row["field"] == "i18n" and row["level"] == "warn" for row in result["problems"]))


class ShippedModulesTests(unittest.TestCase):
    """Every shipped module and template must be complete in both languages."""

    def test_builtins_translate_every_key_they_use(self) -> None:
        for module_id in host.BUILTIN_ORDER:
            folder = BUILTINS / module_id
            html = (folder / "index.html").read_text(encoding="utf-8")
            js = (folder / "main.js").read_text(encoding="utf-8")
            tables = json.loads((folder / "i18n.json").read_text(encoding="utf-8"))
            self.assertEqual(set(tables), {"ru", "en"}, module_id)
            self.assertEqual(set(tables["ru"]), set(tables["en"]), f"{module_id}: ru/en key sets differ")
            used = set(i18n_keys_used(html, js)) | _literal_keys(js)
            for locale, table in tables.items():
                missing = sorted(key for key in used if key not in table)
                self.assertEqual(missing, [], f"{module_id}/{locale}")
                empty = sorted(key for key, value in table.items() if not value)
                self.assertEqual(empty, [], f"{module_id}/{locale} has empty strings")

    def test_builtin_markup_has_no_hardcoded_russian_text(self) -> None:
        for module_id in host.BUILTIN_ORDER:
            html = (BUILTINS / module_id / "index.html").read_text(encoding="utf-8")
            visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
            leftovers = re.findall(r">[^<]*[А-Яа-яЁё][^<]*<|(?:title|placeholder|aria-label|alt)=\"[^\"]*[А-Яа-яЁё]", visible)
            self.assertEqual(leftovers, [], module_id)

    def test_templates_are_bilingual(self) -> None:
        for template in host.TEMPLATES:
            used = set(i18n_keys_used(template["html"], template["js"])) | _literal_keys(template["js"])
            self.assertEqual(set(template["i18n"]), {"ru", "en"}, template["id"])
            for locale, table in template["i18n"].items():
                self.assertEqual(sorted(key for key in used if key not in table), [], f"{template['id']}/{locale}")


@unittest.skipUnless(shutil.which("node"), "node is required for the bridge runner")
class BridgeTests(unittest.TestCase):
    def test_the_real_bridge_translates_and_switches_language(self) -> None:
        page = module_document({
            "id": "demo", "default_locale": "ru", "html": "<b></b>", "js": "",
            "i18n": {
                "ru": {"title": "Привет", "hint": "Подсказка", "rich": "<b>жирно</b>", "only_ru": "Только русский",
                       "hello": "Здравствуйте, {name}",
                       "files": {"one": "{count} файл", "few": "{count} файла", "many": "{count} файлов"}},
                "en": {"title": "Hello", "hint": "Hint", "rich": "<b>bold</b>", "hello": "Hi, {name}",
                       "files": {"one": "{count} file", "other": "{count} files"}},
            },
        }, env={"lang": "ru"})
        # The page also inlines the shared keyboard helper (memu-keys.js, Round 33) before the bridge; pick the bridge
        # by its content instead of its position.
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        bridge = next(s for s in scripts if "var MODULE_ID" in s)
        self.assertIn("MemuKeys", scripts[0], "the keyboard helper must run before the bridge and the module")
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "bridge.js"
            report_path = Path(temp) / "report.json"
            script.write_text(bridge, encoding="utf-8")
            completed = subprocess.run(["node", str(ROOT / "tests" / "modules_i18n_runner.cjs"), str(script), str(report_path)],
                                       capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["initial"]["title"], "Привет")
        self.assertEqual(report["initial"]["placeholder"], "Подсказка")
        self.assertEqual(report["initial"]["tooltip"], "Подсказка")
        self.assertEqual(report["initial"]["rich"], "<b>жирно</b>")
        self.assertEqual(report["initial"]["htmlLang"], "ru")
        self.assertEqual(report["plural"], ["1 файл", "3 файла", "5 файлов", "21 файл"])
        self.assertEqual(report["interpolated"], "Здравствуйте, Алекс")
        self.assertEqual(report["missing"], "nope")
        self.assertEqual(report["missingFallback"], "Запасной 1")
        self.assertIn(report["numberRu"], ("1 234,5", "1 234,5", "1 234,5"))
        # Switching the host language re-translates the page at once.
        self.assertEqual(report["english"]["title"], "Hello")
        self.assertEqual(report["english"]["placeholder"], "Hint")
        self.assertEqual(report["english"]["lang"], "en")
        self.assertEqual(report["english"]["htmlLang"], "en")
        # A key missing in English falls back to the default locale, not the key.
        self.assertEqual(report["english"]["onlyRu"], "Только русский")
        self.assertEqual(report["pluralEn"], ["1 file", "3 files"])
        self.assertEqual(report["numberEn"], "1,234.5")
        self.assertEqual(report["events"], [{"lang": "en", "previous": "ru"}])
        self.assertEqual(report["eventsAfterSame"], 1, "the same language must not re-fire")
        self.assertEqual(report["viaTheme"]["title"], "Привет")
        self.assertEqual(report["hostile"]["lang"], "ru")
        self.assertEqual(report["added"]["title"], "Новый заголовок")
        self.assertEqual(report["getEn"], "Hello")
        self.assertEqual(sorted(report["locales"]), ["en", "ru"])
        self.assertEqual(report["has"], [True, False])
        self.assertEqual(report["version"], 4)


if __name__ == "__main__":
    unittest.main()
