# -*- coding: utf-8 -*-
"""Round 40 (r40-learn): MemuLearn — FSRS-5 against reference values (py-fsrs 4.1.2, FSRS-5, default parameters),
the store and operations, Anki .apkg / .csv round trips, the AI prompts and answer parsing, the module package,
the bridge permission, the home panel / command and the Mini App ops. No network."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.learn import fsrs  # noqa: E402

DAY = fsrs.DAY
T0 = 1767225600.0

class FsrsReferenceTest(unittest.TestCase):
    """Values produced by the reference implementation (py-fsrs 4.1.2, fuzz off): the port must match them exactly."""

    def setUp(self):
        self.s = fsrs.Scheduler(enable_fuzzing=False)

    def test_formulas(self):
        s = self.s
        self.assertEqual([s.initial_stability(r) for r in fsrs.RATINGS], [0.40255, 1.18385, 3.173, 15.69105])
        self.assertAlmostEqual(s.initial_difficulty(fsrs.AGAIN), 7.1949, places=9)
        self.assertAlmostEqual(s.initial_difficulty(fsrs.GOOD), 5.282434422319005, places=9)
        self.assertAlmostEqual(fsrs.FACTOR, 19 / 81, places=12)
        self.assertAlmostEqual(s.retrievability(10.0, 10.0), 0.9, places=12)
        self.assertEqual(s.next_interval(3.173), 3)
        self.assertEqual(s.next_interval(0.2), 1)
        self.assertEqual(fsrs.Scheduler(desired_retention=0.8, enable_fuzzing=False).next_interval(10.0), 24)

    def test_reference_sequence(self):
        """Good ×5 on time, a lapse, Good ×2, Easy, Hard — stability, difficulty and interval of every step."""
        expected = [(3, 3.173, 5.2824, 600.0), (3, 4.4669, 5.273, 345600.0), (3, 14.2173, 5.2635, 1209600.0),
                    (3, 43.7251, 5.2542, 3801600.0), (3, 124.7966, 5.2448, 10800000.0), (1, 6.4599, 6.7716, 600.0),
                    (3, 9.0941, 6.7553, 777600.0), (3, 23.8908, 6.7391, 2073600.0), (4, 129.2769, 6.1962, 11145600.0),
                    (2, 169.7378, 6.7969, 14688000.0)]
        card, now = fsrs.CardState(), T0
        for rating, stability, difficulty, interval in expected:
            card, ivl = self.s.review(card, rating, now)
            self.assertAlmostEqual(card.stability, stability, places=4)
            self.assertAlmostEqual(card.difficulty, difficulty, places=4)
            self.assertAlmostEqual(ivl, interval, places=3)
            now = card.due
        self.assertEqual(card.lapses, 1)
        self.assertEqual(card.reps, 10)
        self.assertEqual(card.state, fsrs.REVIEW)

    def test_learning_steps_and_buttons(self):
        card = fsrs.CardState()
        prev = self.s.preview(card, T0)
        self.assertEqual(prev[fsrs.AGAIN], 60.0)
        self.assertEqual(prev[fsrs.HARD], 330.0)
        self.assertEqual(prev[fsrs.GOOD], 600.0)
        self.assertEqual(prev[fsrs.EASY], 16 * DAY)
        again, _ = self.s.review(card, fsrs.AGAIN, T0)
        self.assertEqual((again.state, again.step), (fsrs.LEARNING, 0))
        self.assertEqual(card.state, fsrs.NEW, "review() must not modify its input")
        self.assertEqual(fsrs.format_interval(600), "10 мин")
        self.assertEqual(fsrs.format_interval(16 * DAY, "en"), "16d")
        self.assertEqual(fsrs.format_interval(80 * DAY), "2,6 мес")

    def test_fuzz_is_deterministic_and_bounded(self):
        s = fsrs.Scheduler()
        card = fsrs.CardState(state=fsrs.REVIEW, step=None, stability=40.0, difficulty=5.0, last_review=T0 - 40 * DAY, due=T0, reps=5)
        a = s.preview(card, T0, "card-1")
        self.assertEqual(a, s.preview(card, T0, "card-1"))
        base = fsrs.Scheduler(enable_fuzzing=False).preview(card, T0, "card-1")
        for rating in (fsrs.HARD, fsrs.GOOD, fsrs.EASY):
            self.assertLessEqual(abs(a[rating] - base[rating]) / DAY, base[rating] / DAY * 0.1 + 2)

class LearnStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MEMUAI_DATA_DIR"] = self.tmp.name
        from app.learn import store
        store.reset_stores()
        self.store_mod = store

    def tearDown(self):
        self.store_mod.reset_stores()
        self.tmp.cleanup()

    def call(self, op, args=None, lang="ru"):
        from app.learn import api
        return api.call(op, args or {}, lang=lang)

    def _course(self):
        from app.learn import ai
        cid = self.call("course.create", {"title": "Основы Python"})["course"]["id"]
        outline = {"title": "Основы Python", "description": "С нуля",
                   "modules": [{"title": "Типы", "lessons": [{"title": "Числа", "brief": "int и float"}, "Строки"]},
                               {"title": "Функции", "lessons": [{"title": "def"}]}]}
        tree = self.call("ai.apply", {"task": "outline", "course_id": cid, "answer": "Вот план:\n```json\n" + json.dumps(outline, ensure_ascii=False) + "\n```"})["tree"]
        self.assertTrue(ai)
        return cid, tree

    def test_outline_lesson_cards_review_undo(self):
        cid, tree = self._course()
        self.assertEqual([m["title"] for m in tree], ["Типы", "Функции"])
        self.assertEqual([l["title"] for l in tree[0]["children"]], ["Числа", "Строки"])
        lid = tree[0]["children"][0]["id"]
        prompt = self.call("ai.prompt", {"task": "lesson", "lesson_id": lid, "cards": 6})
        self.assertIn("Числа", prompt["text"])
        self.assertIn("6", prompt["instructions"])
        answer = {"summary": "## Числа\nЦелые и дробные, $x^2$", "cards": [
            {"type": "basic", "front": "Тип `1.5`?", "back": "float"},
            {"type": "cloze", "front": "Целые — {{c1::int}}, дробные — {{c2::float}}"},
            {"type": "basic", "front": "пустой ответ", "back": ""},
            {"type": "cloze", "front": "без пропусков", "back": "x"}]}
        res = self.call("ai.apply", {"task": "lesson", "lesson_id": lid, "answer": json.dumps(answer, ensure_ascii=False) + ",,"})
        self.assertEqual(res["added"], 4)
        with self.assertRaises(self.store_mod.LearnError):
            self.call("ai.apply", {"task": "lesson", "lesson_id": lid, "answer": '{"error": "429"}'})
        q = self.call("review.queue", {"course_id": cid})
        self.assertEqual(q["counts"]["new"], 4)
        self.assertEqual([b["label"] for b in q["cards"][0]["buttons"]][:3], ["1 мин", "6 мин", "10 мин"])
        card = q["cards"][0]
        done = self.call("review.answer", {"id": card["id"], "rating": 3, "duration_ms": 4000})
        self.assertEqual(done["state"], fsrs.LEARNING)
        self.assertEqual(done["interval_label"], "10 мин")
        self.assertEqual(self.call("overview")["reviewed_today"], 1)
        back = self.call("review.undo")
        self.assertEqual((back["id"], back["state"], back["reps"]), (card["id"], 0, 0))
        stats = self.call("stats", {"course_id": cid})
        self.assertEqual(stats["states"]["new"], 4)
        panel = self.call("panel")
        self.assertEqual(panel["due"], 4)
        self.assertEqual(panel["courses"][0]["lessons"], 3)

    def test_cards_only_keeps_the_summary(self):

        cid, tree = self._course()
        lid = tree[0]["children"][1]["id"]
        self.call("node.update", {"id": lid, "summary": "## Строки\nf-строки"})
        p = self.call("ai.prompt", {"task": "lesson", "lesson_id": lid, "cards": 4, "cards_only": True})
        self.assertIn("f-строки", p["text"])
        self.assertNotIn('"summary"', p["instructions"])
        answer = {"summary": "чужой конспект", "cards": [{"type": "basic", "front": "Что такое f-строка?", "back": "f\"{x}\""}]}
        r = self.call("ai.apply", {"task": "lesson", "lesson_id": lid, "cards_only": True, "answer": json.dumps(answer, ensure_ascii=False)})
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["lesson"]["summary"], "## Строки\nf-строки")
        js = (ROOT / "app" / "module_builtins" / "learn" / "course.js").read_text(encoding="utf-8")
        self.assertIn("all.find((l) => !l.done)", js)
        self.assertIn('id="ls-make"', js)

    def test_exam_grade_makes_weak_cards(self):
        cid, tree = self._course()
        lid = tree[1]["children"][0]["id"]
        p = self.call("ai.prompt", {"task": "exam", "course_id": cid, "count": 2, "mode": "code"})
        self.assertIn(lid, p["text"])
        exam = self.call("ai.apply", {"task": "exam", "course_id": cid, "answer": json.dumps(
            {"questions": [{"kind": "code", "prompt": "Напишите функцию квадрата", "rubric": "def, return", "lesson_id": lid},
                           {"kind": "open", "prompt": "Что такое int?", "rubric": "целое", "lesson_id": "чужой"}]})})["exam"]
        self.assertEqual([q["id"] for q in exam["questions"]], ["q1", "q2"])
        self.assertEqual(exam["questions"][1]["lesson_id"], "")
        g = self.call("ai.prompt", {"task": "grade", "exam_id": exam["id"], "question_id": "q1", "student_answer": "def sq(x): print(x*x)"})
        self.assertIn("def sq", g["text"])
        verdict = {"score": 45, "verdict": "Печать вместо возврата", "mistakes": [{"what": "print", "fix": "return x*x"}],
                   "weak_topics": ["return"], "cards": [{"type": "basic", "front": "Чем return отличается от print?", "back": "Возвращает значение"}]}
        r = self.call("ai.apply", {"task": "grade", "exam_id": exam["id"], "question_id": "q1", "student_answer": "def sq(x): print(x*x)",
                                   "answer": "<think>…</think>" + json.dumps(verdict, ensure_ascii=False)})
        self.assertEqual(r["result"]["score"], 45.0)
        self.assertEqual(r["result"]["cards"], 1)
        self.assertEqual(r["exam"]["answers"]["q1"], "def sq(x): print(x*x)")
        self.assertIsNone(r["exam"]["finished"])
        made = self.call("card.list", {"course_id": cid, "lesson_id": lid})["cards"]
        self.assertEqual(made[0]["tags"], "weak:return")
        self.assertEqual(made[0]["origin"], "exam")

    def test_csv_round_trip(self):
        cid, _ = self._course()
        self.call("card.add", {"course_id": cid, "front": "2+2 = ?", "back": "4", "tags": "math"})
        self.call("card.add", {"course_id": cid, "kind": "cloze", "front": "Формула $E=mc^2$ — {{c1::Эйнштейн}}; {{c2::1905}}"})
        out = self.call("export.csv", {"course_id": cid})
        self.assertEqual(out["count"], 2)
        other = self.call("course.create", {"title": "CSV"})["course"]["id"]
        res = self.call("import.csv", {"course_id": other, "text": out["text"]})
        self.assertEqual(res, {"added": 2, "skipped": 0})
        rows = self.call("card.list", {"course_id": other})["cards"]
        self.assertEqual(sorted((r["kind"], r["ord"]) for r in rows), [("basic", 1), ("cloze", 1), ("cloze", 2)])
        semi = self.call("import.csv", {"course_id": other, "text": "question\tanswer\nCapital of France\tParis\n\t\n"})
        self.assertEqual(semi["added"], 1)

    def test_apkg_round_trip_and_anki_layout(self):
        cid, _ = self._course()
        png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        self.call("card.add", {"course_id": cid, "front": f'Картинка <img src="data:image/png;base64,{png}">', "back": "$$\\int x\\,dx$$", "tags": "img"})
        self.call("card.add", {"course_id": cid, "kind": "cloze", "front": "{{c1::Python}} создал {{c2::Гвидо}}", "extra": "1991"})
        out = self.call("export.apkg", {"course_id": cid})
        raw = base64.b64decode(out["data"])
        self.assertEqual(out["notes"], 2)
        zf = zipfile.ZipFile(io.BytesIO(raw))
        self.assertIn("collection.anki2", zf.namelist())
        media = json.loads(zf.read("media"))
        self.assertEqual(len(media), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.sqlite"
            path.write_bytes(zf.read("collection.anki2"))
            con = sqlite3.connect(str(path))
            models = json.loads(con.execute("SELECT models FROM col").fetchone()[0])
            self.assertEqual(sorted(m["type"] for m in models.values()), [0, 1])
            flds = [r[0] for r in con.execute("SELECT flds FROM notes")]
            self.assertTrue(any("\\[\\int x\\,dx\\]" in f for f in flds), flds)
            self.assertTrue(any(list(media.values())[0] in f for f in flds))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 3)
            con.close()
        imp = self.call("import.apkg", {"data": out["data"], "name": out["name"]})
        self.assertEqual((imp["notes"], imp["skipped"]), (2, 0))
        cards = self.call("card.list", {"course_id": imp["course_id"]})["cards"]
        self.assertEqual(len(cards), 3)
        basic = next(c for c in cards if c["kind"] == "basic")
        self.assertIn("data:image/png;base64,", basic["front"])
        self.assertEqual(basic["back"], "$$\\int x\\,dx$$")
        cloze = [c for c in cards if c["kind"] == "cloze"]
        self.assertEqual(sorted(c["ord"] for c in cloze), [1, 2])
        self.assertEqual(cloze[0]["extra"], "1991")
        with self.assertRaises(self.store_mod.LearnError):
            self.call("import.apkg", {"data": base64.b64encode(b"not a zip").decode(), "name": "x.apkg"})

    def test_reversed_basic_and_sounds_from_anki(self):
        """A «Basic (and reversed)» note gives two cards; [sound:] becomes a mark; HTML wrappers become lines."""
        from app.learn import anki
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collection.anki2"
            con = sqlite3.connect(str(path))
            con.executescript(anki._SCHEMA_11)
            models = {"11": {"type": 0, "name": "Basic (and reversed card)", "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
                             "tmpls": [{"ord": 0}, {"ord": 1}]}}
            con.execute("INSERT INTO col VALUES(1,0,0,0,11,0,0,0,'{}',?,?,'{}','{}')", (json.dumps(models), json.dumps({"5": {"name": "Испанский"}})))
            con.execute("INSERT INTO notes VALUES(1,'g',11,0,0,' es ',?,'',0,0,'')", ("<div>hola</div><div>[sound:hola.mp3]</div>\x1fпривет",))
            con.execute("INSERT INTO cards VALUES(1,1,5,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'')")
            con.commit(); con.close()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.write(path, "collection.anki2")
                zf.writestr("media", "{}")
        imp = self.call("import.apkg", {"data": base64.b64encode(buf.getvalue()).decode(), "name": "deck.apkg"})
        self.assertEqual(imp["title"], "Испанский")
        cards = self.call("card.list", {"course_id": imp["course_id"]})["cards"]
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["front"], "hola\n🔊 hola.mp3")
        self.assertEqual(cards[1]["front"], "привет")

    def test_sources_and_errors(self):
        doc = self.call("source.ingest", {"name": "guide.md", "data": base64.b64encode("# Списки\nСписок — изменяемая последовательность.\n\n# Кортежи\nКортеж нельзя менять.".encode()).decode()})
        self.assertEqual(doc["sections"][:2], ["Списки", "Кортежи"])
        view = self.call("course.create", {"source": {"kind": "file", "doc_id": doc["doc_id"]}})
        self.assertEqual((view["course"]["title"], view["course"]["source_kind"]), ("guide", "file"))
        prompt = self.call("ai.prompt", {"task": "outline", "course_id": view["course"]["id"]})
        self.assertIn("Кортеж нельзя менять", prompt["text"])
        self.assertEqual(self.call("source.notes"), {"files": []})
        with self.assertRaises(self.store_mod.LearnError) as err:
            self.call("source.note", {"path": "/etc/passwd"})
        self.assertEqual(err.exception.code, "forbidden")
        with self.assertRaises(self.store_mod.LearnError):
            self.call("course.create", {"source": {"kind": "file", "doc_id": "0" * 16}})
        with self.assertRaises(self.store_mod.LearnError):
            self.call("card.add", {"course_id": view["course"]["id"], "kind": "cloze", "front": "нет пропусков"})
        en = self.call("ai.prompt", {"task": "outline", "course_id": view["course"]["id"]}, lang="en")
        self.assertIn("Answer in English", en["instructions"])
        settings = self.call("settings.set", {"desired_retention": 2, "new_per_day": -5})
        self.assertEqual((settings["desired_retention"], settings["new_per_day"]), (0.97, 0))

    def test_katex_bundle_is_offline(self):
        bundle = self.call("katex")
        self.assertIn("katex", bundle["js"][:2000].lower())
        self.assertIn("data:font/woff2;base64,", bundle["css"])
        self.assertNotIn("url(fonts/", bundle["css"])

class LearnPackageTest(unittest.TestCase):
    def test_module_package_and_wiring(self):
        from app import modules_host as host, module_icons
        self.assertIn("learn", host.BUILTIN_ORDER)
        self.assertEqual(host.ACTION_PERMISSION["learn_call"], "learn")
        self.assertNotIn("learn", host.IMPLICIT_PERMISSIONS)
        self.assertEqual(module_icons.BUILTIN_GLYPH["learn"], "brain")
        with tempfile.TemporaryDirectory() as tmp:
            pkg = host.load_package(Path(tmp), "learn")
        self.assertFalse(pkg["missing"])
        self.assertFalse(pkg["enabled"])
        self.assertIn("learn", pkg["permissions"])
        self.assertNotIn("@memu-part", pkg["js"])
        for name in ("VIEWS.review", "function startReview", "const MD", "function startExamSetup", "VIEWS.stats"):
            self.assertTrue(name in pkg["js"] or name.replace("VIEWS.review", "startReview") in pkg["js"], name)
        bridge = (ROOT / "app" / "modules_host.py").read_text(encoding="utf-8")
        self.assertIn('learn: learnApi', bridge)
        modules_js = (ROOT / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        self.assertIn('case "learn_call"', modules_js)
        self.assertIn("/api/pg/learn/call", modules_js)
        folder = host.builtin_dir() / "learn"
        for name in ("icon.svg", "icon.png", "icon.ico", "integration/panel.html", "integration/panels.js", "integration/skill.md"):
            self.assertTrue((folder / name).is_file(), name)

    def test_every_string_is_translated(self):
        folder = ROOT / "app" / "module_builtins" / "learn"
        i18n = json.loads((folder / "i18n.json").read_text(encoding="utf-8"))
        self.assertEqual(set(i18n["ru"]), set(i18n["en"]))
        used = set()
        for js in folder.glob("*.js"):
            text = js.read_text(encoding="utf-8")
            used |= set(re.findall(r'\bt\("([a-z_]+(?:\.[a-z0-9_]+)+)"', text))
            used |= set(re.findall(r'plural\([^,]+,\s*"([a-z_.]+)"\)', text))
        used |= set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', (folder / "index.html").read_text(encoding="utf-8")))
        self.assertFalse(used - set(i18n["ru"]), sorted(used - set(i18n["ru"])))
        families = {"src.": ["topic", "book", "note", "anki"], "grade.": ["again", "hard", "good", "easy"],
                    "level.": ["zero", "basic", "mid", "pro"], "depth.": ["short", "normal", "deep"],
                    "exam.mode.": ["mixed", "theory", "code"], "exam.kind.": ["open", "code"], "kind.": ["basic", "cloze"],
                    "lesson.stage.": ["material", "think", "save", "retry"], "create.stage.": ["course", "outline", "tree", "retry"]}
        for prefix, names in families.items():
            for n in names:
                self.assertIn(prefix + n, i18n["ru"])
        for key in ("count.cards", "count.days", "count.lessons", "count.courses"):
            self.assertIn("many", i18n["ru"][key])
            self.assertIn("other", i18n["en"][key])

    def test_manifest_contributions_validate(self):
        from app import module_integration as mi
        manifest = json.loads((ROOT / "app" / "module_builtins" / "learn" / "manifest.json").read_text(encoding="utf-8"))
        import app.module_integration_data  # noqa: F401  (registers the data sources)
        import app.module_integration_handlers  # noqa: F401
        self.assertIn("learn.panel", mi.DATA)
        self.assertIn("learn.due", mi.HANDLERS)
        folder = ROOT / "app" / "module_builtins" / "learn"
        self.assertEqual(mi.validate_contributes(manifest["contributes"], builtin=True, folder=folder), [])
        clean = mi.normalize_contributes(manifest["contributes"], builtin=True)
        self.assertEqual([p["id"] for p in clean["panels"]], ["today"])
        self.assertEqual([c["id"] for c in clean["commands"]], ["due", "review"])

    def test_tgapp_wiring(self):
        index = (ROOT / "app" / "tgapp" / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index("learn.js?v="), index.index("app.js?v="))
        self.assertIn("learn.css?v=", index)
        api = (ROOT / "app" / "tgapp_api.py").read_text(encoding="utf-8")
        self.assertIn("learn_tgapp_ops.register_tgapp_ops(op, ApiError)", api)
        js = (ROOT / "app" / "tgapp" / "learn.js").read_text(encoding="utf-8")
        for op in ("learn.state", "learn.queue", "learn.answer", "learn.undo"):
            self.assertIn(op, js)
            self.assertIn(f'@op("{op}")', (ROOT / "app" / "learn" / "tgapp_ops.py").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
