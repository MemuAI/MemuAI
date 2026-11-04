"""Reflect «Токены»: per-answer token counts (real provider usage, the window's estimate or a text estimate),
the period totals, per day / model / place breakdowns and the section in the settings page."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PG = ROOT / "app" / "pgstatic"

from app import reflect  # noqa: E402

UTC = timezone.utc
START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)

def ms(day: int, hour: int = 12) -> int:
    return int((START + timedelta(days=day, hours=hour)).timestamp() * 1000)

class MessageTokensTest(unittest.TestCase):
    def test_recorded_counts_win(self) -> None:
        msg = {"role": "assistant", "content": "x" * 340, "tokens": {"in": 1200, "out": 90, "est": False, "model": "gpt-x"}}
        self.assertEqual(reflect.message_tokens(msg, msg["content"], 0), (1200, 90, False, "gpt-x"))

    def test_ctx_real_then_sent_then_text(self) -> None:
        real = {"role": "assistant", "content": "y" * 34, "ctx": {"sent": 900, "real": 1500, "model": "m1"}}
        self.assertEqual(reflect.message_tokens(real, real["content"], 0), (1500, 10, True, "m1"))
        sent = {"role": "assistant", "content": "y" * 34, "ctx": {"sent": 900}, "model": "m2"}
        self.assertEqual(reflect.message_tokens(sent, sent["content"], 0)[:2], (900, 10))
        bare = {"role": "assistant", "content": "z" * 68}
        tin, tout, est, model = reflect.message_tokens(bare, bare["content"], 3400)
        self.assertEqual((tin, tout, est, model), (1000 + reflect.SYSTEM_TOKENS_ESTIMATE, 20, True, ""))

    def test_garbage_is_clamped(self) -> None:
        msg = {"role": "assistant", "content": "a", "tokens": {"in": -5, "out": "1e12", "model": 7}}
        tin, tout, _est, model = reflect.message_tokens(msg, "a", 0)
        self.assertEqual(tin, 0)
        self.assertEqual(tout, reflect.TOKENS_CAP)
        self.assertEqual(model, "7")

class TokenStatsTest(unittest.TestCase):
    def conv(self, cid: str, source: str, rows):
        return {"id": cid, "title": "t", "source": source, "messages": rows}

    def test_totals_series_models_places(self) -> None:
        convs = [
            self.conv("a", "chat", [
                {"role": "user", "text": "q", "ts": ms(0)},
                {"role": "assistant", "text": "a", "ts": ms(0), "tin": 1000, "tout": 200, "est": False, "model": "big"},
                {"role": "user", "text": "q2", "ts": ms(2)},
                {"role": "assistant", "text": "a", "ts": ms(2), "tin": 3000, "tout": 400, "est": True, "model": "big"}]),
            self.conv("b", "work", [
                {"role": "user", "text": "q", "ts": ms(2)},
                {"role": "assistant", "text": "b" * 340, "ts": ms(2), "tin": 500, "tout": 100, "est": True, "model": "small"}]),
            self.conv("c", "quick", [  # rows without counts (old data) are estimated from the text
                {"role": "user", "text": "q", "ts": ms(5)},
                {"role": "assistant", "text": "c" * 34, "ts": ms(5)}]),
        ]
        stats = reflect.compute_stats(reflect.in_period(convs, START, END), START, END)
        tok = stats["tokens"]
        self.assertEqual(tok["input"], 4500)
        self.assertEqual(tok["output"], 710)
        self.assertEqual(tok["total"], 5210)
        self.assertEqual(tok["answers"], 4)
        self.assertEqual(len(tok["series"]), len(stats["series"]))
        self.assertEqual(tok["series"][0], 1200)
        self.assertEqual(tok["series"][2], 4000)
        self.assertEqual(tok["busiest_day"], {"date": "2026-09-03", "tokens": 4000})
        self.assertEqual([m["model"] for m in tok["models"]], ["big", "small", ""])
        self.assertEqual(sum(m["share"] for m in tok["models"]), 100)
        self.assertEqual(tok["places"]["chat"], 4600)
        self.assertEqual(list(tok["places"])[0], "chat")
        self.assertAlmostEqual(tok["estimated_share"], round((3400 + 600 + 10) / 5210, 2))
        self.assertEqual(tok["largest_conversation"], 4600)
        self.assertEqual(tok["average_answer"], round(710 / 4))
        # the existing day rows keep their exact shape
        self.assertEqual(set(stats["series"][0]), {"date", "conversations", "messages", "minutes"})

    def test_many_models_fold_into_other(self) -> None:
        rows = []
        for i in range(9):
            rows += [{"role": "user", "text": "q", "ts": ms(i)},
                     {"role": "assistant", "text": "a", "ts": ms(i), "tin": 100 * (i + 1), "tout": 0, "est": False, "model": f"m{i}"}]
        stats = reflect.compute_stats(reflect.in_period([self.conv("x", "chat", rows)], START, END), START, END)
        models = stats["tokens"]["models"]
        self.assertEqual(len(models), reflect.TOKEN_MODELS_SHOWN + 1)
        self.assertEqual(models[0]["model"], "m8")
        self.assertEqual(models[-1]["model"], "")
        self.assertEqual(models[-1]["other"], 3)
        self.assertEqual(models[-1]["tokens"], 100 + 200 + 300)

    def test_empty_period(self) -> None:
        tok = reflect.compute_stats([], START, END)["tokens"]
        self.assertEqual((tok["total"], tok["answers"], tok["models"], tok["busiest_day"]), (0, 0, [], None))

class CollectTest(unittest.TestCase):
    def test_collected_rows_carry_tokens(self) -> None:
        from unittest import mock
        chats = [{"id": "chat-1", "title": "t", "created": ms(0), "messages": [
            {"role": "user", "content": "привет", "createdAt": ms(0)},
            {"role": "assistant", "content": "ответ", "createdAt": ms(0), "tokens": {"in": 700, "out": 30, "est": False, "model": "mm"}},
            {"role": "user", "content": "ещё", "createdAt": ms(1)},
            {"role": "assistant", "content": "второй ответ", "createdAt": ms(1)}]}]
        with mock.patch("app.pgdata.load_chats", return_value=chats):
            convs = reflect.collect_conversations({"reflect_include_quick": False, "reflect_include_telegram": True})
        rows = [r for r in convs[0]["messages"] if r["role"] == "assistant"]
        self.assertEqual((rows[0]["tin"], rows[0]["tout"], rows[0]["est"], rows[0]["model"]), (700, 30, False, "mm"))
        self.assertTrue(rows[1]["est"])
        self.assertGreater(rows[1]["tin"], reflect.SYSTEM_TOKENS_ESTIMATE)

class StaticTest(unittest.TestCase):
    def test_window_records_tokens_for_every_round(self) -> None:
        app = (PG / "app.js").read_text(encoding="utf-8")
        self.assertIn("assistant.tokens = { in: (+prevTokens.in || 0) + tin, out: (+prevTokens.out || 0) + tout,", app)
        self.assertIn("realRound && +roundUsage.prompt_tokens > 0", app)

    def test_page_has_the_section(self) -> None:
        js = (PG / "reflect.js").read_text(encoding="utf-8")
        for needle in ("function tokensSection(data)", 'chart(series, "tokens")', 'data-metric="${tok ? "tokens" : "conversations"}"',
                       "root.querySelectorAll(\".rf-chart\").forEach(wireOneChart)", "rf-tok-models", "estimated_share"):
            self.assertIn(needle, js)
        css = (PG / "reflect.css").read_text(encoding="utf-8")
        for needle in (".rf-tok-split", ".rf-tok-model", ".rf-chart--tokens .rf-bar"):
            self.assertIn(needle, css)

if __name__ == "__main__":
    unittest.main()
