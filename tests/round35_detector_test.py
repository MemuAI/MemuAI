"""Round 35 (detector): gateway recounts, silent probes, newer quiz, speed — and reference pages.

Offline: model calls are replaced by fakes. Live checks of the same behaviour are described in
changelog/round35/detector.md (tooken.club / reformboss / OpenRouter through the Proxy).
"""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import detector, model_call, tokenizer_probe, visual_test  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
O200K = [159, 192, 147]

def measured(base: int, counts, base2: int = 0) -> dict:
    usage = lambda tokens: {"usage": {"prompt_tokens": tokens, "estimated": False}, "kind": "tokenizer", "text": "OK"}  # noqa: E731
    out = {"tokenizer_base": usage(base)}
    for name, count in zip(tokenizer_probe.ORDER, counts):
        out[f"tokenizer_{name}"] = usage((base2 or base) + count)
    out["tokenizer_base2"] = usage(base2 or base)
    return out

def identity(text: str, version: str = "") -> dict:
    return {"identity_en": {"kind": "identity", "text": text},
            "identity_ru": {"kind": "identity", "text": text},
            "identity_base": {"kind": "identity", "text": text},
            "identity_version": {"kind": "identity", "text": version or text}}

class GatewayRecountTest(unittest.TestCase):
    """tooken.club reports o200k counts for its Claude models (live, 2026-10-12): 37 / 196 / 229 / 184 / 37,
    while its DeepSeek and GLM models report their own tokenizers. A substitute GPT would need a persona
    prompt to say "Claude Sonnet 5.5" — and the request carries no room for one."""

    def test_tiktoken_counts_without_room_for_a_persona_are_a_recount_not_a_swap(self) -> None:
        results = {**identity("Claude Sonnet 5.5, trained by Anthropic."), **measured(37, O200K)}
        report = detector.analyse("tooken-claude-sonnet-5-5", results)
        self.assertEqual(report["tokenizer"]["verdict"], "recount")
        self.assertIn("o200k", report["tokenizer"]["recount_of"])
        self.assertNotEqual(report["verdict"], "substituted")
        self.assertFalse(report["spoof_suspected"])
        self.assertNotEqual(report["confidence"], "high", "the one unfakeable signal is missing")
        warn = [row for row in report["reasons"] if row["level"] == "warn"]
        self.assertTrue(any("пересчитал" in row["ru"] and "tiktoken" in row["ru"] for row in warn), report["reasons"])
        self.assertTrue(any("recounted" in row["en"] for row in warn))
        self.assertTrue(any("tiktoken" in row["ru"] for row in report["confidence_factors"]))
        self.assertEqual(report["version"]["claimed"]["id"], "claude-sonnet-5-5", "Sonnet 5.5 is in the catalogue now")

    def test_a_persona_sized_hidden_prompt_keeps_the_substitution_verdict(self) -> None:
        results = {**identity("Claude Sonnet 5.5, trained by Anthropic."), **measured(37 + 40, O200K)}
        report = detector.analyse("tooken-claude-sonnet-5-5", results)
        self.assertEqual((report["tokenizer"]["verdict"], report["verdict"]), ("mismatch", "substituted"))

    def test_no_recount_when_the_self_report_does_not_back_the_claim(self) -> None:
        for text in ("I am ChatGPT, made by OpenAI.", "unknown"):
            results = {**identity(text), **measured(37, O200K)}
            report = detector.analyse("reseller-claude-opus-5-5", results, "claude")
            self.assertEqual(report["tokenizer"]["verdict"], "mismatch", text)
        mixed = {**identity("Claude by Anthropic."), **measured(37, O200K)}
        mixed["identity_ru"]["text"] = "Я ChatGPT от OpenAI."
        mixed["identity_base"]["text"] = "GPT by OpenAI"
        self.assertEqual(detector.analyse("x-claude-opus-5-5", mixed)["tokenizer"]["verdict"], "mismatch")

    def test_only_tiktoken_references_qualify(self) -> None:
        tokenizer = {"verdict": "mismatch", "matches": [{"id": "qwen", "families": ["qwen"]}], "hidden_tokens": 0, "shift": 0}
        self.assertFalse(detector.gateway_recount(tokenizer, "claude", {"claude": 9.0}, 1.0, 3))
        tokenizer["matches"] = [{"id": "o200k", "families": ["gpt"]}]
        self.assertTrue(detector.gateway_recount(tokenizer, "claude", {"claude": 9.0}, 1.0, 3))
        self.assertFalse(detector.gateway_recount(tokenizer, "gpt", {"gpt": 9.0}, 1.0, 3))
        self.assertFalse(detector.gateway_recount({**tokenizer, "shift": 12}, "claude", {"claude": 9.0}, 1.0, 3))
        self.assertFalse(detector.gateway_recount(tokenizer, "claude", {"claude": 9.0}, 1.0, 1), "one phrasing is not enough")

    def test_the_panel_knows_the_new_verdict(self) -> None:
        js = (ROOT / "app" / "static" / "detector.js").read_text(encoding="utf-8")
        self.assertIn('recount: ["warn"', js)

class SilentProbesAndRunTest(unittest.TestCase):
    def _run(self, answer_for, depth="quick"):
        calls = []

        async def fake_complete(model, messages, params=None):
            prompt = messages[-1]["content"]
            calls.append(prompt)
            text = answer_for(prompt, len(calls))
            if isinstance(text, Exception):
                raise text
            return {"text": text, "reasoning": "", "ttft_ms": 10, "ms": 20, "chars": len(text)}

        async def go():
            with patch("app.model_call.complete", fake_complete), patch.object(detector, "TRANSIENT_PAUSES", (0.0, 0.0)):
                started = await detector.start("tooken-claude-sonnet-5-5", depth)
                for _ in range(400):
                    run = detector.get_run(started["id"])
                    if run["state"] != "running":
                        return run, calls
                    await asyncio.sleep(0.01)
            raise AssertionError("the run did not finish")

        with tempfile.TemporaryDirectory() as temp, patch("app.config.DATA_DIR", temp):
            return asyncio.run(go())

    def test_a_gateway_refusing_special_tokens_is_a_silent_probe_not_a_failure(self) -> None:
        def answer(prompt, _n):
            if "Repeat each numbered line" in prompt:
                return RuntimeError("Ошибка апстрима (HTTP 400): Запрос содержит запрещённый служебный токен.")
            return "Claude Sonnet 5.5 by Anthropic." if "model" in prompt or "модел" in prompt else "unknown"

        run, _calls = self._run(answer)
        self.assertEqual(run["state"], "done")
        probe = next(row for row in run["probes"] if row["id"] == "tokens")
        self.assertTrue(probe.get("blocked"))
        self.assertNotIn("error", probe)
        quality = run["report"]["quality"]
        self.assertNotIn("tokens", quality["failed"] + quality["empty"])
        self.assertTrue(any("отклоняет запросы со служебными токенами" in row["ru"] for row in run["report"]["reasons"]))
        self.assertFalse(detector._SPECIAL_BLOCKED_RE.search("Rate limit exceeded"))

    def test_bursty_rate_limits_get_two_quiet_retries(self) -> None:
        failures = {"count": 0}

        def answer(prompt, _n):
            if "training data cutoff" in prompt and failures["count"] < 2:
                failures["count"] += 1
                return RuntimeError("Все аккаунты «custom» недоступны (or): Ошибка апстрима: The operation was aborted")
            return "unknown"

        run, _calls = self._run(answer)
        probe = next(row for row in run["probes"] if row["id"] == "cutoff_self")
        self.assertEqual((probe.get("retried"), probe.get("text")), ("transient", "unknown"))
        self.assertTrue(detector._transient(RuntimeError("This model is currently experiencing high demand")))
        not_found = RuntimeError("model not found")
        not_found.status = 404
        self.assertFalse(detector._transient(not_found))

    def test_the_longest_probes_start_first(self) -> None:
        run, calls = self._run(lambda prompt, n: "unknown")
        self.assertEqual(run["state"], "done")
        self.assertTrue(calls[0].startswith("Reply with the single word OK"), "the tokenizer chain starts first")
        singles = [prompt for prompt in calls if not prompt.startswith("Reply with the single word OK")]
        self.assertTrue(singles[0].startswith("Answer each question with a few words"), "then the quiz")
        self.assertTrue(singles[1].startswith("Answer each question with the final answer only"), "and the reasoning batch")
        self.assertEqual(detector.PARALLEL_PROBES, 4)

class NewerQuizAndCatalogueTest(unittest.TestCase):
    def test_the_quiz_stays_the_list_current_models_answer_in_full(self) -> None:
        """Round 35 live finding: appending March–July 2026 questions made Claude Sonnet 5.5 answer "unknown"
        from 2025-09 on (it answers all 19 of this list). The list is pinned; extend it only after a live check."""
        dates = [date for date, _question, _pattern in detector.QUIZ]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual((len(dates), dates[-1]), (19, "2026-02"))
        source = (ROOT / "app" / "detector.py").read_text(encoding="utf-8")
        self.assertIn("do NOT append events newer than what current models know", source)

    def test_knowing_the_whole_list_gives_february_2026(self) -> None:

        answers = [re.sub(r"\[(.)[^\]]*\]", r"\1", pattern.split("|")[0].replace("\\b", "")).strip()
                   for _date, _question, pattern in detector.QUIZ]
        self.assertEqual(detector.score_quiz(json.dumps(answers))["estimate"], "2026-02")
        upto = [value if date <= "2025-07" else "unknown" for value, (date, _q, _p) in zip(answers, detector.QUIZ)]
        self.assertEqual(detector.score_quiz(json.dumps(upto))["estimate"], "2025-07")

    def test_new_models_are_in_the_catalogue(self) -> None:
        for model, expected in (("tooken-claude-sonnet-5-5", "claude-sonnet-5-5"), ("or-anthropic/claude-haiku-5.5", "claude-haiku-5-5"),
                                ("tooken-gpt-6.1-sol", "gpt-6-1"), ("gpt-6-sol", "gpt-6"), ("claude-sonnet-5", "claude-sonnet-5"),
                                ("genspark-claude-opus-5-5", "claude-opus-5-5")):
            self.assertEqual((detector.expected_model(model) or {}).get("id"), expected, model)
        self.assertEqual(set(detector.score_specific_model(["I am GPT-6.1 Sol"])), {"gpt-6-1"})

class ReferencePagesTest(unittest.TestCase):
    """Round 36: the Claude Opus 5.5 references were removed at the user's request (see round36_proxy_test.py)."""

    def test_no_model_page_is_ever_served_from_the_static_folder(self) -> None:
        static = ROOT / "app" / "static"
        self.assertFalse(list(static.rglob("*reference*.html")) + list(static.rglob("visual-refs*")))

    def test_the_panel_compares_side_by_side_in_sandboxed_frames(self) -> None:
        js = (ROOT / "app" / "static" / "detector.js").read_text(encoding="utf-8")
        for needle in ("function visualCompare", 'data-vis="compare"', 'sandbox="allow-scripts allow-pointer-lock"', "det-compare-modal"):
            self.assertIn(needle, js)
        self.assertNotIn("allow-same-origin", js)
        index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/detector-compare.css', index)
        self.assertTrue((ROOT / "app" / "static" / "detector-compare.css").exists())

class VisualRetryTest(unittest.TestCase):
    def test_a_gateway_timeout_before_the_first_token_is_retried_once(self) -> None:
        calls = []

        async def stream(name, messages, params=None):
            calls.append(dict(params or {}))
            if len(calls) == 1:
                raise RuntimeError("Модель не начала ответ вовремя (upstream_timeout)")
            yield {"type": "text", "text": '<svg viewBox="0 0 1 1"></svg>'}

        async def go():
            with patch.object(model_call, "stream", stream), patch.object(visual_test, "RETRY_PAUSE", 0.0):
                run = await visual_test.start("tooken-claude-opus-5-5", "pelican")
                for _ in range(300):
                    await asyncio.sleep(0.005)
                    state = visual_test.get(run["id"])
                    if state["state"] != "running":
                        return state
            return state

        with tempfile.TemporaryDirectory() as temp, patch.object(visual_test, "_folder", lambda: Path(temp)):
            visual_test._runs.clear()
            state = asyncio.run(go())
        self.assertEqual((state["state"], state["retried"]), ("done", True))
        self.assertEqual(calls, [{"max_tokens": visual_test.MAX_OUTPUT_TOKENS}] * 2)

if __name__ == "__main__":
    unittest.main()
