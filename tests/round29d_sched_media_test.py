"""Round 29 (r29d-sched-media): regressions for the wave-D fixes in Planning and media.

* the conflict warning names the other item as a task («…одновременно с задачей «X».»);
* a result that has just finished reads «только что / just now», not «сейчас / now» (desktop panel, Mini App);
* the Mini App preview sheet's confirm button is short enough not to wrap on a 390 px phone («Создать» / «Create»);
* the Telegram session keeps ``last_media`` (the sanitizer dropped it, «🔁 Ещё вариант» said «Нечего повторить»).
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29d-sm-")

ROOT = Path(__file__).resolve().parents[1]

class ConflictWarningTextTest(unittest.TestCase):
    def test_conflict_names_a_task(self) -> None:
        from app import schedule_rules as rules
        rule = {"when": [{"type": "daily", "times": ["09:00"]}]}
        other = {"id": "o", "name": "Сводка", "kind": "chat", "rule": rule, "enabled": True}
        ru = rules.warnings({"id": "n", "kind": "chat", "rule": rule}, [other], "ru")
        en = rules.warnings({"id": "n", "kind": "chat", "rule": rule}, [other], "en")
        self.assertIn("Запускается одновременно с задачей «Сводка».", ru)
        self.assertIn("Runs at the same time as the task “Сводка”.", en)

class RelativeTimeWordsTest(unittest.TestCase):
    def _rel(self, path: str) -> str:
        source = (ROOT / path).read_text(encoding="utf-8")
        match = re.search(r"function relIn\(ts\) \{.*?\n\s{2,6}\}\n", source, re.S)
        self.assertIsNotNone(match, path)
        return match.group(0)

    def test_just_finished_is_just_now(self) -> None:
        for path in ("app/pgstatic/plan.js", "app/tgapp/plan.js"):
            body = self._rel(path)
            self.assertIn("только что", body, path)
            self.assertIn("just now", body, path)

    def test_relin_behaviour(self) -> None:
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        body = self._rel("app/tgapp/plan.js")
        script = ("const S={lang:'ru'}; const tr=(r,e)=>S.lang==='en'?e:r; function plural(n,a,b,c){const x=Math.abs(n)%100,y=x%10;"
                  "if(x>10&&x<20)return c;if(y===1)return a;return y>=2&&y<=4?b:c;}\n" + body +
                  "\nconst now=Date.now()/1000; console.log([relIn(now-20), relIn(now+20), relIn(now-600)].join('|'));"
                  "S.lang='en'; console.log([relIn(now-20), relIn(now+3)].join('|'));")
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30).stdout.split("\n")
        self.assertEqual(out[0], "только что|сейчас|10 мин назад")
        self.assertEqual(out[1], "just now|now")

class MiniAppPreviewButtonTest(unittest.TestCase):
    def test_confirm_label_is_short(self) -> None:
        source = (ROOT / "app/tgapp/plan.js").read_text(encoding="utf-8")
        self.assertIn('label: tr("Создать", "Create"), run:', source)
        self.assertNotIn('label: tr("Создать задачу", "Create the task")', source)

class TelegramLastMediaTest(unittest.TestCase):
    def test_session_keeps_last_media(self) -> None:
        from app import pgdata
        state = pgdata._telegram_state({"sessions": {"42": {"profile_id": "default", "last_media": {
            "kind": "video", "request": "волны", "prompt": "waves at sunset", "model": "m", "junk": "x" * 10}}}})
        self.assertEqual(state["sessions"]["42"]["last_media"],
                         {"kind": "video", "request": "волны", "prompt": "waves at sunset", "model": "m"})
        empty = pgdata._telegram_state({"sessions": {"42": {"last_media": "bad"}}})
        self.assertEqual(empty["sessions"]["42"]["last_media"], {})
        long = pgdata._telegram_state({"sessions": {"1": {"last_media": {"kind": "evil", "request": "r" * 9000}}}})
        self.assertEqual(long["sessions"]["1"]["last_media"]["kind"], "image")
        self.assertEqual(len(long["sessions"]["1"]["last_media"]["request"]), 3000)

    def test_round_trip_through_the_state_file(self) -> None:
        from app import pgdata

        def put(data):
            data.setdefault("sessions", {})["77"] = {"last_media": {"kind": "image", "request": "кот", "prompt": "a cat"}}
            return data
        pgdata.update_telegram_state(put)
        self.assertEqual(pgdata.load_telegram_state()["sessions"]["77"]["last_media"]["request"], "кот")

if __name__ == "__main__":
    unittest.main()
