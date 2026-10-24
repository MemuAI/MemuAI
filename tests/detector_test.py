"""Model detector: scoring of each probe and a whole run with scripted answers."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import detector as d


class ScoringTests(unittest.TestCase):
    def test_claimed_family_comes_from_the_model_id(self) -> None:
        self.assertEqual(d.claimed_family("genspark-claude-opus-5"), "claude")
        self.assertEqual(d.claimed_family("custom-gpt-5.4"), "gpt")
        self.assertEqual(d.claimed_family("antigravity-gemini-3-pro-high"), "gemini")
        self.assertEqual(d.claimed_family("trash-kimi-k2.7"), "kimi")
        self.assertEqual(d.claimed_family("local-my-model"), "")

    def test_identity_answers_are_weighted(self) -> None:
        scores, notes = d.score_identity(["I am Claude, made by Anthropic.", "Я Qwen от Alibaba Cloud."])
        self.assertGreater(scores["claude"], 0)
        self.assertGreater(scores["qwen"], 0)
        self.assertEqual(len(notes), 2)

    def test_token_probe_detects_a_stop_on_its_own_eos(self) -> None:
        # The answer stops at line 2 (<|im_end|>): a ChatML model ended its turn there.
        answer = "1. alpha <|endoftext|> 1\n2. bravo "
        scores, info = d.score_tokens(answer)
        self.assertEqual(info["stopped_at"], "im_end")
        self.assertGreater(scores.get("qwen", 0), 3)

    def test_intact_echo_is_reported_as_uninformative(self) -> None:
        lines = [f"{index + 1}. {d._WORDS[index]} {token} {index + 1}" for index, (_, token) in enumerate(d.SPECIAL_TOKENS)]
        lines.append(f"{len(d.SPECIAL_TOKENS) + 1}. {d._WORDS[len(d.SPECIAL_TOKENS)]} end {len(d.SPECIAL_TOKENS) + 1}")
        scores, info = d.score_tokens("\n".join(lines))
        self.assertEqual(scores, {})
        self.assertFalse(info["informative"])

    def test_short_or_reformatted_echo_is_not_a_token_fingerprint(self) -> None:
        for answer in ("Sorry, I cannot repeat those tokens.",
                       "1. alpha <|endoftext|> 1\n2. bravo omitted on purpose",
                       "1. alpha <|endoftext|> 1", "2. bravo"):
            scores, info = d.score_tokens(answer)
            self.assertEqual(scores, {})
            self.assertFalse(info["informative"])

    def test_quiz_estimates_the_cutoff_and_ignores_a_lucky_last_guess(self) -> None:
        answers = ["Argentina", "Charles III", "Messi", "Qatar", "unknown", "unknown", "unknown", "unknown",
                   "unknown", "unknown", "unknown", "Sinner"]
        result = d.score_quiz(json.dumps(answers))
        self.assertEqual(result["estimate"], "2024-02")
        self.assertEqual(result["correct"], 5)

    def test_distribution_sums_to_100(self) -> None:
        rows = d.distribution({"claude": 5, "gpt": 2, "qwen": 1.3})
        self.assertEqual(sum(row["percent"] for row in rows), 100)
        self.assertEqual(rows[0]["family"], "claude")

    def test_judge_output_is_parsed_defensively(self) -> None:
        parsed = d.parse_judge('noise {"distribution":[{"family":"kimi","percent":60},{"family":"nope","percent":40}],'
                               '"best_guess":"Kimi K2","reasoning":"r"} tail')
        self.assertEqual(parsed["distribution"], [{"family": "kimi", "percent": 60.0}])
        self.assertEqual(d.parse_judge("no json at all"), {})
        self.assertEqual(d.parse_judge('{"distribution": [1, null, {"family": "claude", "percent": "NaN"}]}')["distribution"], [])
        self.assertEqual(d.parse_judge('{"distribution": [{"family": "claude", "percent": 70}, '
                                       '{"family": "claude", "percent": 60}]}')["distribution"],
                         [{"family": "claude", "percent": 100.0}])
        blended = d.blend([{"family": "claude", "percent": 100}], [{"family": "kimi", "percent": 100}])
        self.assertEqual({row["family"]: row["percent"] for row in blended}, {"claude": 50, "kimi": 50})

    # -- calibrated confidence: the point of the rewrite is that it must not --
    # -- be overconfident on thin or single-source evidence.                --

    def test_calibrated_distribution_never_reaches_100_on_a_lone_signal(self) -> None:
        rows = d.calibrated_distribution({"claude": 3.0}, fired=0, agreement=None)
        real = next(row for row in rows if row["family"] == "claude")
        uncertain = next(row for row in rows if row["family"] == d._UNCERTAIN)
        self.assertLess(real["percent"], 100)
        # With literally no other corroborating evidence, "not enough evidence"
        # should be at least as prominent as the lone real candidate.
        self.assertGreaterEqual(uncertain["percent"], real["percent"])

    def test_calibrated_distribution_sharpens_as_more_evidence_agrees(self) -> None:
        thin = d.calibrated_distribution({"claude": 15.0}, fired=1, agreement=None)
        rich = d.calibrated_distribution({"claude": 15.0}, fired=5, agreement=1.0)
        thin_top = next(row["percent"] for row in thin if row["family"] == "claude")
        rich_top = next(row["percent"] for row in rich if row["family"] == "claude")
        self.assertGreater(rich_top, thin_top)

    def test_calibrated_distribution_widens_on_disagreement(self) -> None:
        agreeing = d.calibrated_distribution({"claude": 10.0}, fired=3, agreement=1.0)
        disagreeing = d.calibrated_distribution({"claude": 10.0}, fired=3, agreement=0.3)
        agree_top = next(row["percent"] for row in agreeing if row["family"] == "claude")
        disagree_top = next(row["percent"] for row in disagreeing if row["family"] == "claude")
        self.assertGreater(agree_top, disagree_top)

    def test_entropy_ratio_is_zero_for_a_single_candidate_and_one_for_uniform(self) -> None:
        self.assertEqual(d._entropy_ratio([1.0]), 0.0)
        self.assertEqual(d._entropy_ratio([0.5, 0.5]), 1.0)
        self.assertLess(d._entropy_ratio([0.9, 0.1]), 1.0)

    # -- consistency: agreement across phrasings + literal-repeat stability --

    def test_consistency_detects_full_agreement(self) -> None:
        results = {
            "identity_en": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "identity_ru": {"kind": "identity", "text": "Я Клод от Anthropic."},
            "identity_base": {"kind": "identity", "text": "claude by anthropic"},
            "identity_complete": {"kind": "identity", "text": "I am called Claude, developed by Anthropic."},
            "identity_repeat": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
        }
        info = d.score_consistency(results)
        self.assertEqual(info["identity_agreement"], 1.0)
        self.assertEqual(info["repeat_stability"], 1.0)

    def test_consistency_detects_disagreement_across_phrasings(self) -> None:
        results = {
            "identity_en": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "identity_ru": {"kind": "identity", "text": "Я Qwen от Alibaba Cloud."},
            "identity_base": {"kind": "identity", "text": "gpt by openai"},
        }
        info = d.score_consistency(results)
        self.assertLess(info["identity_agreement"], 0.6)

    def test_consistency_flags_an_unstable_literal_repeat(self) -> None:
        results = {
            "identity_en": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "identity_repeat": {"kind": "identity", "text": "I am Qwen, made by Alibaba."},
        }
        info = d.score_consistency(results)
        # Different brand name and vendor in the literal repeat: clearly less
        # similar than the perfect-match case above, even though the sentence
        # shape ("I am X, made by Y") is shared.
        self.assertLess(info["repeat_stability"], 0.7)

    # -- specific-model catalogue --

    def test_specific_model_matches_a_precise_self_report(self) -> None:
        scores = d.score_specific_model(["My exact version is Opus 5.5, from Anthropic."])
        self.assertIn("claude-opus-5-5", scores)
        rows = d.specific_distribution(scores, "claude")
        self.assertEqual(rows[0]["id"], "claude-opus-5-5")
        self.assertEqual(rows[0]["tier"], "uncertain")

    def test_specific_model_finds_nothing_on_a_family_only_answer(self) -> None:
        scores = d.score_specific_model(["I am Claude, made by Anthropic."])
        self.assertEqual(scores, {})

    def test_expected_model_matches_the_provider_id(self) -> None:
        row = d.expected_model("genspark-claude-opus-4-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], "claude-opus-4-1")
        self.assertEqual(row["cutoff"], "2025-03")
        # A brand-new, uncertain-tier model must not fabricate a cutoff.
        row2 = d.expected_model("genspark-claude-opus-5-5")
        self.assertEqual(row2["cutoff"], "")
        self.assertEqual(d.expected_model("custom-gpt-5-mini")["id"], "gpt-5-mini")
        self.assertEqual(d.score_specific_model(["I am GPT-5 mini"]), {"gpt-5-mini": 2.5})

    def test_distributions_do_not_drop_families_after_rounding(self) -> None:
        scores = {row["id"]: 1.0 for row in d.FAMILIES}
        self.assertEqual(sum(row["percent"] for row in d.distribution(scores)), 100)
        self.assertEqual(sum(row["percent"] for row in d.calibrated_distribution(scores, 1, None)), 100)

    # -- spoof / substitution detection --

    def test_detect_spoof_flags_conflicting_self_report_and_behaviour(self) -> None:
        suspected, id_family, behavior_family = d.detect_spoof({"kimi": 6.0}, {"qwen": 4.0})
        self.assertTrue(suspected)
        self.assertEqual(id_family, "kimi")
        self.assertEqual(behavior_family, "qwen")

    def test_detect_spoof_is_quiet_when_behaviour_is_absent_or_agrees(self) -> None:
        self.assertFalse(d.detect_spoof({"claude": 6.0}, {})[0])
        self.assertFalse(d.detect_spoof({"claude": 6.0}, {"claude": 4.0})[0])

    # -- context/specs probe --

    def test_context_claim_parses_common_phrasings(self) -> None:
        self.assertEqual(d._parse_context_claim("context: 200000, output: 8192"), 200000)
        self.assertEqual(d._parse_context_claim("context: 1m, output: unknown"), 1_000_000)
        self.assertEqual(d._parse_context_claim("context: 128k, output: 4k"), 128_000)
        self.assertIsNone(d._parse_context_claim("context: unknown, output: unknown"))

    def test_score_context_rewards_a_close_match_and_penalises_a_mismatch(self) -> None:
        expected = d.expected_model("genspark-claude-opus-4-1")  # context_claim = 200000
        close_scores, close_info = d.score_context(205_000, expected)
        self.assertTrue(close_info["match"])
        self.assertGreater(close_scores.get("claude", 0), 0)
        far_scores, far_info = d.score_context(8_000, expected)
        self.assertFalse(far_info["match"])
        self.assertLess(far_scores.get("claude", 0), 0)


class AnalyseHonestyTests(unittest.TestCase):
    """Regression tests for the overconfidence complaint that motivated this
    rewrite: a single, easily-spoofed self-report should not alone produce a
    confident "genuine" verdict, while several independently-agreeing probes
    should. Pure calls to ``analyse`` — no network, no Run/asyncio."""

    def test_a_single_self_report_alone_is_not_confidently_genuine(self) -> None:
        results = {
            "identity_en": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "identity_ru": {"kind": "identity", "text": "unknown"},
            "identity_base": {"kind": "identity", "text": "unknown"},
            "identity_version": {"kind": "identity", "text": "unknown"},
            "tokens": {"kind": "tokens", "text": "unknown"},
            "cutoff_self": {"kind": "cutoff", "text": "unknown"},
            "quiz": {"kind": "quiz", "text": "unknown"},
            "specs": {"kind": "specs", "text": "unknown"},
            "ability_logic": {"kind": "ability", "passed": False},
        }
        report = d.analyse("genspark-claude-opus-5-5", results, "claude")
        self.assertEqual(report["confidence"], "low")
        self.assertLess(report["confidence_percent"], 60)

    def test_rich_agreeing_evidence_is_confidently_genuine(self) -> None:
        results = {
            "identity_en": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "identity_ru": {"kind": "identity", "text": "Я Клод, модель от Anthropic."},
            "identity_base": {"kind": "identity", "text": "claude by anthropic"},
            "identity_version": {"kind": "identity", "text": "Opus 4.1"},
            "identity_complete": {"kind": "identity", "text": "I am called Claude, developed by Anthropic."},
            "identity_repeat": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "tokens": {"kind": "tokens", "text": "unknown"},
            "cutoff_self": {"kind": "cutoff", "text": "unknown"},
            "quiz": {"kind": "quiz", "text": "unknown"},
            "specs": {"kind": "specs", "text": "context: 200000, output: 8192"},
            "ability_logic": {"kind": "ability", "passed": True},
        }
        report = d.analyse("genspark-claude-opus-4-1", results, "claude")
        self.assertEqual(report["verdict"], "genuine")
        self.assertEqual(report["confidence"], "high")
        self.assertGreater(report["confidence_percent"], 60)
        self.assertEqual(report["specific"]["top"]["id"], "claude-opus-4-1")

    def test_conflicting_identity_and_tokens_is_flagged_as_possibly_spoofed(self) -> None:
        results = {
            "identity_en": {"kind": "identity", "text": "I am Kimi, developed by Moonshot AI."},
            "identity_ru": {"kind": "identity", "text": "Я Kimi от Moonshot AI."},
            "identity_base": {"kind": "identity", "text": "kimi by moonshot"},
            "identity_version": {"kind": "identity", "text": "unknown"},
            "tokens": {"kind": "tokens", "text": "1. alpha <|endoftext|> 1\n2. bravo "},
            "cutoff_self": {"kind": "cutoff", "text": "unknown"},
            "quiz": {"kind": "quiz", "text": "unknown"},
            "specs": {"kind": "specs", "text": "unknown"},
            "ability_logic": {"kind": "ability", "passed": False},
        }
        report = d.analyse("genspark-claude-opus-5", results, "claude")
        self.assertTrue(report["spoof_suspected"])
        self.assertEqual(report["verdict"], "substituted")


class RunTests(unittest.TestCase):
    def _run(self, answer_for, judge=""):
        async def fake_complete(model, messages, params=None):
            text = answer_for(model, messages[-1]["content"])
            return {"text": text, "reasoning": "", "ttft_ms": 120, "ms": 800, "chars": len(text)}

        async def go():
            with patch("app.model_call.complete", fake_complete):
                started = await d.start("genspark-claude-opus-5", "full", judge)
                for _ in range(200):
                    run = d.get_run(started["id"])
                    if run["state"] != "running":
                        return run
                    await asyncio.sleep(0.01)
            raise AssertionError("the run did not finish")

        with tempfile.TemporaryDirectory() as temp, patch("app.config.DATA_DIR", temp):
            return asyncio.run(go())

    def test_a_substituted_model_is_flagged(self) -> None:
        def answer(model, prompt):
            if "exact model name" in prompt or "Как точно" in prompt or "base model family" in prompt or "Complete honestly" in prompt:
                return "I am Kimi, developed by Moonshot AI."
            if "Repeat each numbered line" in prompt:
                return "1. alpha <|endoftext|> 1\n2. bravo "
            return "unknown"

        run = self._run(answer)
        self.assertEqual(run["state"], "done")
        report = run["report"]
        self.assertEqual(report["claimed"], "claude")
        self.assertEqual(report["distribution"][0]["family"], "kimi")
        self.assertEqual(report["verdict"], "substituted")

    def test_a_genuine_model_with_a_judge(self) -> None:
        def answer(model, prompt):
            if model == "judge-model":
                return '{"distribution":[{"family":"claude","percent":90},{"family":"gpt","percent":10}],"best_guess":"Claude Opus","reasoning":"ok"}'
            if "exact model name" in prompt or "Как точно" in prompt or "base model family" in prompt or "Complete honestly" in prompt:
                return "I'm Claude, an AI assistant made by Anthropic."
            if "How many times" in prompt:
                return "3"
            return "unknown"

        run = self._run(answer, judge="judge-model")
        report = run["report"]
        self.assertEqual(report["verdict"], "genuine")
        self.assertEqual(report["judge"]["best_guess"], "Claude Opus")
        self.assertLess(report["claimed_percent"], 90)
        self.assertGreater(report["distribution"][0]["percent"], report["candidates"][0]["percent"])

    def test_judge_cannot_upgrade_thin_evidence_to_genuine(self) -> None:
        def answer(model, prompt):
            if model == "judge-model":
                return '{"distribution":[{"family":"claude","percent":100}],"best_guess":"Claude"}'
            return "I am Claude, by Anthropic." if "exact model name" in prompt else "unknown"

        run = self._run(answer, judge="judge-model")
        self.assertNotEqual(run["report"]["verdict"], "genuine")
        self.assertNotEqual(run["report"]["confidence"], "high")

    def test_every_probe_failing_is_an_error_not_a_verdict(self) -> None:
        async def failing(model, messages, params=None):
            raise RuntimeError("upstream down")

        async def go():
            with patch("app.model_call.complete", failing):
                started = await d.start("kiro-claude-sonnet-4-5")
                for _ in range(200):
                    run = d.get_run(started["id"])
                    if run["state"] != "running":
                        return run
                    await asyncio.sleep(0.01)

        with tempfile.TemporaryDirectory() as temp, patch("app.config.DATA_DIR", temp):
            run = asyncio.run(go())
            self.assertEqual(run["state"], "error")
            self.assertIn("upstream down", run["error"])
            self.assertTrue((Path(temp) / "detector-history.json").exists())

    def test_cancel_stops_in_flight_probes(self) -> None:
        async def go():
            entered = asyncio.Event()
            released = asyncio.Event()

            async def hanging(model, messages, params=None):
                entered.set()
                await released.wait()

            with patch("app.model_call.complete", hanging):
                started = await d.start("genspark-claude-opus-5", "full")
                await asyncio.wait_for(entered.wait(), 1)
                self.assertTrue(d.cancel(started["id"]))
                for _ in range(100):
                    run = d.get_run(started["id"])
                    if run["state"] == "cancelled":
                        return run
                    await asyncio.sleep(0.01)
                raise AssertionError("cancelled run did not stop")

        with tempfile.TemporaryDirectory() as temp, patch("app.config.DATA_DIR", temp):
            run = asyncio.run(go())
        self.assertEqual(run["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
