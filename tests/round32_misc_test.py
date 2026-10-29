"""Round 32, wave 2 (misc): countdown target in the user's timezone, ISO values localized in panels, CU prompt.

Запуск: python3 -m pytest -q tests/round32_misc_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r32-misc-test-"))

PANELS_JS = (ROOT / "app" / "pgstatic" / "panels.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

def _py_in_tz(tz: str, code: str) -> dict:
    """Run ``code`` (prints JSON) with the process timezone set to ``tz`` — the server runs on the user's PC."""
    env = {**os.environ, "TZ": tz, "PYTHONPATH": str(ROOT)}
    setup = ("from app import panels\nfrom tests.timezone_fixture import local_datetime\n"
             + f"panels.datetime = local_datetime({tz!r})\n")
    out = subprocess.run([sys.executable, "-c", setup + textwrap.dedent(code)], capture_output=True, text=True, env=env,
                         timeout=60, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])

def _js_func(name: str) -> str:
    a = PANELS_JS.index(f"  function {name}(")
    depth, i = 0, PANELS_JS.index("{", a)
    while True:
        ch = PANELS_JS[i]
        depth += ch == "{"
        depth -= ch == "}"
        i += 1
        if depth == 0:
            return PANELS_JS[a:i]

class CountdownTimezoneTest(unittest.TestCase):
    CODE = """
        import json
        from app import panels
        s = {"lang": "ru"}
        out = {
          "naive": panels._parse_target("2027-01-01T00:00"),
          "with_offset": panels._parse_target("2027-01-01T00:00+03:00"),
          "old_window_z": panels._parse_target("2026-12-31T21:00:00.000Z"),
          "utc_plus0": panels._parse_target("2026-12-31T21:00+00:00"),
          "other_offset": panels._parse_target("2027-01-01T00:00+05:00"),
          "lenient": panels._lenient_target("Fri 2027-01-01 12:00 AM MSK (UTC+03:00)"),
          "label_old_stored": panels.countdown_values("2026-12-31T21:00+00:00", s)["target_label"],
          "label_new": panels.countdown_values("2027-01-01T00:00+03:00", s)["target_label"],
          "label_z": panels.countdown_values("2026-12-31T21:00Z", s)["target_label"],
        }
        print(json.dumps(out))
    """

    def test_local_time_is_stored_with_the_users_offset(self) -> None:
        r = _py_in_tz("Europe/Moscow", self.CODE)
        self.assertEqual(r["naive"], "2027-01-01T00:00+03:00")
        self.assertEqual(r["with_offset"], "2027-01-01T00:00+03:00")

        self.assertEqual(r["old_window_z"], "2027-01-01T00:00+03:00")
        self.assertEqual(r["utc_plus0"], "2027-01-01T00:00+03:00")
        self.assertEqual(r["other_offset"], "2027-01-01T00:00+05:00", "an explicit non-UTC offset is kept")
        self.assertEqual(r["lenient"], "2027-01-01T00:00+03:00")

    def test_label_is_in_the_users_zone_even_for_targets_stored_in_utc(self) -> None:
        r = _py_in_tz("Europe/Moscow", self.CODE)
        self.assertEqual(r["label_old_stored"], "01.01.2027")
        self.assertEqual(r["label_new"], "01.01.2027")
        self.assertEqual(r["label_z"], "01.01.2027")

    def test_create_through_the_api_contract(self) -> None:
        r = _py_in_tz("Europe/Moscow", """
            import json, os, tempfile
            os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r32-misc-cd-")
            from app import panels
            p = panels.create_panel(template="countdown", params={"target": "2027-01-01T00:00+03:00"}, title="НГ", start=False)
            q = panels.create_panel(template="countdown", params={"target": "2026-12-31T21:00:00.000Z"}, title="НГ2", start=False)
            print(json.dumps([p["source"]["params"]["target"], q["source"]["params"]["target"],
                              p["data"].get("target_label"), q["data"].get("target_label")]))
        """)
        self.assertEqual(r[:2], ["2027-01-01T00:00+03:00", "2027-01-01T00:00+03:00"])
        self.assertEqual(r[2], r[3])

@unittest.skipUnless(NODE, "node is required")
class PanelsJsTest(unittest.TestCase):
    def _node(self, tz: str, script: str):
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30,
                             env={**os.environ, "TZ": tz})
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_form_sends_local_wall_time_with_offset(self) -> None:
        script = _js_func("localIso") + """
            console.log(JSON.stringify([localIso("2027-01-01T00:00"), localIso("2026-07-01T09:30"), localIso("bad")]));"""
        self.assertEqual(self._node("Europe/Moscow", script), ["2027-01-01T00:00+03:00", "2026-07-01T09:30+03:00", "bad"])
        ny = self._node("America/New_York", script)
        self.assertEqual(ny[:2], ["2027-01-01T00:00-05:00", "2026-07-01T09:30-04:00"])
        self.assertIn("v = localIso(v)", PANELS_JS)
        self.assertNotIn("v = new Date(v).toISOString()", PANELS_JS)

    def _iso_shown(self, lang: str, values):
        a = PANELS_JS.index("  const ISO_VALUE =")
        b = PANELS_JS.index("  const plain =", a)
        script = (f"const en = () => {json.dumps(lang == 'en')};\n" + PANELS_JS[a:b] +
                  f"\nconsole.log(JSON.stringify({json.dumps(values)}.map(isoShown)));")
        return self._node("Europe/Moscow", script)

    def test_iso_values_follow_the_window_language(self) -> None:
        vals = ["2027-01-15", "2027-01-15T18:30+03:00", "1 января 2027", "2027-02-30", "v2027-01-15", "12:30"]
        en = self._iso_shown("en", vals)
        ru = self._iso_shown("ru", vals)
        self.assertIn("Jan", en[0])
        self.assertIn("2027", en[0])
        self.assertIn("18:30", en[1])
        self.assertIn("янв", ru[0])
        self.assertEqual(en[2:], vals[2:], "words and non-ISO values are left as written")
        for s in en:
            self.assertNotRegex(s, r"[А-Яа-яЁё]" if s != "1 января 2027" else r"^$")

    def test_countdown_target_is_never_localized(self) -> None:
        body = PANELS_JS[PANELS_JS.index('case "countdown": {'):]
        body = body[:body.index("case \"row\"")]
        self.assertIn("plain(b.target, data)", body)
        self.assertIn("plain(b.since, data)", body)
        self.assertNotIn("text(b.target", body)

class ComputerPromptTest(unittest.TestCase):
    def test_type_hint_reads_as_one_sentence(self) -> None:
        from app import automation_runtime
        prompt = automation_runtime.COMPUTER_SYSTEM_PROMPT
        self.assertIn('a "\\n" in the text presses Enter', prompt)
        line = next(x for x in prompt.splitlines() if x.startswith('4. "type"'))
        self.assertIn("presses Enter). The app you opened", line)

if __name__ == "__main__":
    unittest.main()
