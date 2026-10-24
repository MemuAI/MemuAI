"""Round 29 phase 2 (r29d-chat4): regression tests for the main-window fixes of wave D.

* scheduled messages: a message that was already late when the reopened window delegated it to MemuAI Proxy
  still follows «schedsend_missed» (ask / skip were bypassed by the window's own delegate);
* Work bar chips and the access mode names are Russian in the Russian UI (were «Ask for approval / Plan / Goal»).
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r29d-chat4-")

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"
MIN = 60 * 1000


class MissedAfterRelaunch(unittest.TestCase):
    def setUp(self) -> None:
        from app import scheduled_messages as sm
        self.sm = sm
        path = sm.path_for(None)
        if path.exists():
            path.unlink()
        self.now = sm.now_ms()

    def due_item(self, minutes_late: int):
        sm = self.sm
        item = sm.create({"chat_id": "chat-r", "text": "hello", "trigger": "time", "due_at": self.now + 2 * MIN},
                         None, now=self.now)
        # time passes while MemuAI is closed
        return item, self.now + 2 * MIN + minutes_late * MIN

    def test_late_delegate_keeps_the_policy(self) -> None:
        sm = self.sm
        for policy, action, status in (("ask", "ask", "missed"), ("skip", "skip", "skipped"), ("send", "send", "sending")):
            item, reopened = self.due_item(10)
            sm.delegate(item["id"], None, now=reopened)           # the reopened window hands it to the Proxy
            res = sm.claim(item["id"], "proxy", None, policy=policy, now=reopened + 5000)
            self.assertEqual(res["action"], action, policy)
            self.assertEqual(res["item"]["status"], status, policy)
            sm.path_for(None).unlink()           # next policy starts from an empty queue

    def test_on_time_delegate_is_still_sent(self) -> None:
        sm = self.sm
        item, _ = self.due_item(0)
        due = item["due_at"]
        sm.delegate(item["id"], None, now=due + 1000)              # window open at the due moment
        # the Proxy picks it up a bit later than the grace time — still on-time delivery, no policy
        res = sm.claim(item["id"], "proxy", None, policy="skip", now=due + 1000 + sm.LATE_GRACE_MS - 1000)
        self.assertEqual(res["action"], "send")


class WorkBarRussian(unittest.TestCase):
    def test_chips_translated(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for chip in ("work-access", "work-plan", "work-goal"):
            tag = re.search(rf'<button[^>]*id="{chip}"[^>]*>([^<]*)</button>', html)
            self.assertTrue(tag, chip)
            self.assertRegex(tag.group(1), r"[А-Яа-яЁё]", chip)
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('function workAccessLabel(mode) { return mode === "full" ? memeText("Полный доступ", "Full access") : memeText("С подтверждением", "Ask for approval"); }', js)
        self.assertIn('plan.textContent = memeText("План", "Plan");', js)
        self.assertIn('goal.textContent = memeText("Цель", "Goal");', js)
        self.assertNotIn('access.textContent = workAccessMode() === "full" ? "Full access" : "Ask for approval";', js)
        self.assertNotIn('"Включить Full access?"', js)
        self.assertNotIn(">Ask for approval</option>", js)
        code = (STATIC / "memucode.js").read_text(encoding="utf-8")
        self.assertIn('t("Полный доступ", "Full access") : t("С подтверждением", "Ask")', code)

    def test_ru_docs_use_the_ui_names(self) -> None:
        for name in ("work.md", "memucode.md", "schedules.md", "troubleshooting.md"):
            text = (ROOT / "app" / "docs" / "ru" / name).read_text(encoding="utf-8")
            self.assertNotIn("Ask for approval", text, name)
            self.assertNotIn("Full access", text, name)

    def test_lang_scan_no_longer_allows_the_english_names(self) -> None:
        scan = (ROOT / "tests" / "ui_lang_scan.py").read_text(encoding="utf-8")
        allowed = scan[scan.index("RU_ALLOWED_PHRASES"):scan.index("RU_SKIP_WHERE")]
        self.assertNotIn('"Ask for approval"', allowed)
        self.assertNotIn('"Full access"', allowed)


class OtherUntranslated(unittest.TestCase):
    def test_default_quick_presets_follow_the_ui_language(self) -> None:
        from app import quick_apps, pgdata
        from unittest.mock import patch
        tmp = Path(tempfile.mkdtemp(prefix="r29d-qa-"))
        with patch.object(pgdata, "MEMU_DIR", tmp):
            ru = quick_apps.load_config("ru")["presets"]          # first start in Russian stores Russian presets
            en = quick_apps.load_config("en")["presets"]
            self.assertEqual([p["name"] for p in en], [p["name"] for p in quick_apps.default_presets("en")])
            self.assertEqual([p["id"] for p in en], [p["id"] for p in ru])
            edited = [dict(ru[0], name="Мой ответ")] + ru[1:]
            quick_apps.save_presets(edited, "ru")
            names = [p["name"] for p in quick_apps.load_config("en")["presets"]]
            self.assertEqual(names[0], "Мой ответ")                # what the user wrote stays as written
            self.assertEqual(names[1], "Formula help")

    def test_search_skills_and_prompts_localized(self) -> None:
        js = (STATIC / "search.js").read_text(encoding="utf-8")
        self.assertIn('const title = ui("name") || sk.name || sk.title || sk.id;', js)
        from app import search_api
        items = [{"type": "prompt", "title": "Original", "subtitle": "Original MemuAI prompts",
                  "meta": {"name_ru": "Оригинал", "set": "Original MemuAI prompts", "set_ru": "Оригинальные промпты MemuAI"}}]
        search_api._localize_prompts(items, "ru")
        self.assertEqual((items[0]["title"], items[0]["subtitle"]), ("Оригинал", "Оригинальные промпты MemuAI"))
        en = [{"type": "prompt", "title": "Original", "subtitle": "S", "meta": {"name_ru": "Оригинал", "set": "S", "set_ru": "Н"}}]
        search_api._localize_prompts(en, "en")
        self.assertEqual(en[0]["title"], "Original")


class ExportPreview(unittest.TestCase):
    def test_page_count_is_measured_in_the_preview(self) -> None:
        js = (STATIC / "export.js").read_text(encoding="utf-8")
        self.assertIn('parent.postMessage({mxPages:n,mxSeq:S},"*")', js)
        self.assertIn("e.source !== frame.contentWindow || !e.data || e.data.mxSeq !== previewSeq", js)
        self.assertIn(".replace(/<\\/body>(?![\\s\\S]*<\\/body>)/, `${measure}</body>`)", js)


class LanguageEvents(unittest.TestCase):
    def test_pg_apply_lang_announces_the_change(self) -> None:
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        start = app.index("function pgApplyLang() {")
        body = app[start:app.index("\n}\n", start)]
        self.assertIn('document.dispatchEvent(new CustomEvent("memu:lang"', body)
        self.assertIn('window.dispatchEvent(new CustomEvent("memu-language"', body)
        # every listener has a sender now
        self.assertIn('document.addEventListener("memu:lang", set);', (STATIC / "search.js").read_text(encoding="utf-8"))
        self.assertIn('window.addEventListener("memu-language"', (STATIC / "stuck.js").read_text(encoding="utf-8"))
        self.assertIn('document.addEventListener("memu:lang", () => { if (state.loaded) { render(); syncLang(); } });',
                      (STATIC / "panels.js").read_text(encoding="utf-8"))


class SelectsAt1366(unittest.TestCase):
    def test_menu_width_counts_borders_and_scrollbar(self) -> None:
        js = (STATIC / "memu-layers.js").read_text(encoding="utf-8")
        self.assertIn('menu.style.width = "";', js)
        self.assertIn("const natural_w = Math.ceil((menu.scrollWidth || 0) + borders);", js)
        self.assertIn("Math.ceil((menu.scrollHeight || maxH) + bordersV)", js)
        self.assertIn("const lost = menu.offsetWidth - menu.clientWidth - borders;", js)

    def test_fixed_width_selects_fit_their_longest_option(self) -> None:
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("function fitPrettySelectWidth(wrap, select, button) {", app)
        self.assertEqual(app.count("fitPrettySelectWidth(wrap, select, button);"), 2)   # pretty-select + searchable mpick
        self.assertIn("Math.min(need, 300)", app)
        # r29f: the title shows from 20 characters, and a shortened model name shows its full id.
        self.assertIn('button.title = (selected?.text || "").length > 20 ? selected.text', app)


if __name__ == "__main__":
    unittest.main()
