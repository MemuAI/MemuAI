"""Round 20: detector accuracy (reasoning blocks, refusals, guessable quiz answers,
timeouts, retries) and per-model cost in the Proxy statistics."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import detector as d
from app import metrics as metrics_module
from app import pricing

class AnswerHygieneTests(unittest.TestCase):
    def test_think_blocks_are_removed_and_an_unclosed_one_means_no_answer(self) -> None:
        visible, info = d.clean_answer("<think>Am I Claude or GPT? Let me think.</think>\nI am Grok, built by xAI.")
        self.assertEqual(visible, "I am Grok, built by xAI.")
        self.assertGreater(info["thinking_chars"], 10)
        visible, info = d.clean_answer("<think>The user asks... I should list [1, 2, 3]")
        self.assertEqual(visible, "")
        self.assertTrue(info["truncated_thinking"])
        visible, _ = d.clean_answer("<|channel|>analysis<|message|>maybe GPT-4o?<|end|><|start|>assistant<|channel|>final<|message|>I am gpt-oss.")
        self.assertEqual(visible, "I am gpt-oss.")
        self.assertEqual(d.clean_answer("plain answer")[0], "plain answer")

    def test_scratchpad_denials_and_api_formats_are_not_self_reports(self) -> None:
        self.assertEqual(d._mentions("<think>Is this Claude? Or GPT?</think>I am Qwen by Alibaba."), {"qwen": 2})
        self.assertNotIn("gpt", d._mentions("I am Claude, made by Anthropic — not ChatGPT."))
        self.assertNotIn("gpt", d._mentions("I expose an OpenAI-compatible API. I am DeepSeek."))
        self.assertEqual(d._mentions("I cannot search Google for you."), {})
        self.assertIn("gemini", d._mentions("I am a large language model, trained by Google."))
        self.assertIn("gemini", d._mentions("Я большая языковая модель, обученная Google."))
        self.assertNotIn("llama", d._mentions("Here is some meta information."))

    def test_refusals_are_recognised(self) -> None:
        self.assertTrue(d.is_refusal("I'm sorry, but I can't share details about my underlying model."))
        self.assertTrue(d.is_refusal("К сожалению, я не могу раскрыть эту информацию."))
        self.assertFalse(d.is_refusal("I am Claude, made by Anthropic."))

    def test_list_answers_are_parsed_robustly(self) -> None:
        rows, ok = d.parse_list('<think>maybe [x] or {y}</think>\n```json\n["a", "b", "c"]\n```', 3)
        self.assertTrue(ok)
        self.assertEqual(rows, ["a", "b", "c"])

        rows, ok = d.parse_list("1. Argentina\n2) Charles\n4. Qatar", 5)
        self.assertTrue(ok)
        self.assertEqual(rows, ["Argentina", "Charles", "", "Qatar", ""])
        rows, ok = d.parse_list('{"1": "x", "3": "z"}', 3)
        self.assertEqual(rows, ["x", "", "z"])

        rows, ok = d.parse_list("I'm sorry, I can't browse the internet.\nPlease check the news.", 19)
        self.assertFalse(ok)
        self.assertEqual(rows, [])

    def test_wrapper_and_judge_json_survive_reasoning_text(self) -> None:
        wrapper = d.score_wrapper('<think>{"draft": 1}</think>{"instructions": true, "persona": "Kiro", "gist": "IDE"}')
        self.assertTrue(wrapper["instructions"])
        self.assertEqual(wrapper["persona"], "Kiro")
        judged = d.parse_judge('<think>Weights {a: 1}</think> Result: {"distribution": [{"family": "claude", "percent": 80},'
                               ' {"family": "gpt", "percent": 20}], "best_guess": "Claude Opus 4.5"}')
        self.assertEqual(judged["best_guess"], "Claude Opus 4.5")
        self.assertEqual(len(judged["distribution"]), 2)

class QuizAndTasksTests(unittest.TestCase):
    def _answers(self, **known: str):
        rows = ["unknown"] * len(d.QUIZ)
        for index, value in known.items():
            rows[int(index[1:])] = value
        return json.dumps(rows)

    def test_an_isolated_guessable_answer_does_not_move_the_cutoff(self) -> None:

        result = d.score_quiz(self._answers(q0="Argentina", q2="Messi"))
        self.assertEqual(result["estimate"], "2022-12")

        result = d.score_quiz(self._answers(q0="Argentina", q1="Charles III", q2="Messi"))
        self.assertEqual(result["estimate"], "2023-10")

        known = {f"q{index}": value for index, value in enumerate(
            ["Argentina", "Charles", "Messi", "Qatar", "Anora", "Spain", "Noah Lyles", "Donald Trump"])}
        self.assertEqual(d.score_quiz(self._answers(**known))["estimate"], "2024-11")

    def test_hedged_answers_are_guesses(self) -> None:
        result = d.score_quiz(self._answers(q0="Argentina", q6="probably Noah Lyles", q7="Biden or Trump"))
        self.assertEqual(result["estimate"], "2022-12")
        self.assertEqual(result["hedged"], 2)

    def test_unparsable_quiz_is_not_evidence(self) -> None:
        result = d.score_quiz("Sorry, I cannot answer questions about current events.")
        self.assertFalse(result["parsed"])
        self.assertEqual(result["estimate"], "")

    def test_capability_answers_are_normalised(self) -> None:
        answers = ["3", "9.90", "8", "62\u202f615\u202f533", "azoolapallol", "Friday", "9", "29"]
        result = d.score_capability(json.dumps(answers))
        self.assertEqual(result["correct"], 8)
        answers[1] = "9.11 is larger than 9.9"
        self.assertEqual(d.score_capability(json.dumps(answers))["correct"], 7)

    def test_a_reworded_repeat_with_the_same_claim_is_stable(self) -> None:
        same = d.score_consistency({
            "identity_en": {"kind": "identity", "text": "I'm Claude, an AI model by Anthropic."},
            "identity_repeat": {"kind": "identity", "text": "This is Claude (Anthropic) answering you."}})
        self.assertGreaterEqual(same["repeat_stability"], 0.9)
        self.assertTrue(same["repeat_same_claim"])
        other = d.score_consistency({
            "identity_en": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "identity_repeat": {"kind": "identity", "text": "I am GPT, made by OpenAI."}})
        self.assertLessEqual(other["repeat_stability"], 0.4)

class AnalyseQualityTests(unittest.TestCase):
    def test_reasoning_in_think_tags_does_not_name_families(self) -> None:
        think = "<think>Could I be GPT-4o by OpenAI, or Gemini? The system says I'm Claude.</think>"
        results = {
            "identity_en": {"kind": "identity", "text": think + "I am Claude, made by Anthropic."},
            "identity_ru": {"kind": "identity", "text": think + "Я Claude от Anthropic."},
            "identity_base": {"kind": "identity", "text": think + "claude by anthropic"},
        }
        report = d.analyse("kiro-claude-sonnet-4-5", results)
        self.assertEqual([row["family"] for row in report["distribution"]], ["claude"])
        self.assertEqual(report["quality"]["thinking_stripped"], 3)
        self.assertTrue(any("think" in row["en"] for row in report["confidence_factors"]))

    def test_refusals_and_failures_cap_confidence_and_are_explained(self) -> None:
        results = {
            "identity_en": {"kind": "identity", "text": "I am Claude, made by Anthropic."},
            "identity_ru": {"kind": "identity", "text": "Я Claude, модель Anthropic."},
            "identity_base": {"kind": "identity", "text": "claude by anthropic"},
            "identity_complete": {"kind": "identity", "text": "I am called Claude, developed by Anthropic."},
            "identity_version": {"kind": "identity", "text": "I'm sorry, but I can't share that."},
            "quiz": {"kind": "quiz", "text": "I'm sorry, I can't help with current events."},
            "capability": {"kind": "capability", "error": "Нет ответа за 150 с", "timed_out": True},
            "specs": {"kind": "specs", "error": "upstream 502"},
            "cutoff_self": {"kind": "cutoff", "error": "upstream 502"},
        }
        report = d.analyse("kiro-claude-opus-4-1", results)
        quality = report["quality"]
        self.assertIn("identity_version", quality["refused"])
        self.assertIn("quiz", quality["refused"])
        self.assertIn("capability", quality["timed_out"])
        self.assertFalse(quality["partial"])
        self.assertGreaterEqual(quality["lost_share"], 0.34)
        self.assertNotEqual(report["confidence"], "high")
        self.assertTrue(any(row["level"] == "warn" and "probes gave no answer" in row["en"]
                            for row in report["confidence_factors"]))

class RunRobustnessTests(unittest.TestCase):
    def _run(self, fake, depth="quick"):
        async def go():
            with patch("app.model_call.complete", fake):
                started = await d.start("kiro-claude-sonnet-4-5", depth)
                for _ in range(400):
                    run = d.get_run(started["id"])
                    if run["state"] != "running":
                        return run
                    await asyncio.sleep(0.01)
            raise AssertionError("the run did not finish")

        with tempfile.TemporaryDirectory() as temp, patch("app.config.DATA_DIR", temp):
            return asyncio.run(go())

    @staticmethod
    def _answer(prompt: str) -> str:
        if "exact model name" in prompt or "Как точно" in prompt or "base model family" in prompt:
            return "I am Claude, made by Anthropic."
        return "unknown"

    def test_a_reply_budget_spent_on_thinking_is_retried_with_room(self) -> None:
        calls = []

        async def fake(model, messages, params=None):
            prompt = messages[-1]["content"]
            calls.append(params["max_tokens"])
            if "exact model name" in prompt and params["max_tokens"] < d.REASONING_RETRY_TOKENS:
                return {"text": "", "reasoning": "long thoughts " * 50, "ttft_ms": 5, "ms": 10, "chars": 0}
            text = self._answer(prompt)
            return {"text": text, "reasoning": "", "ttft_ms": 5, "ms": 10, "chars": len(text)}

        run = self._run(fake)
        self.assertEqual(run["state"], "done")
        probe = next(row for row in run["probes"] if row["id"] == "identity_en")
        self.assertEqual(probe["text"], "I am Claude, made by Anthropic.")
        self.assertEqual(probe["retried"], "reasoning_budget")
        self.assertIn(d.REASONING_RETRY_TOKENS, calls)

        self.assertEqual(calls.count(d.REASONING_RETRY_TOKENS), 1)

    def test_a_tokenizer_probe_starved_by_thinking_is_retried(self) -> None:
        budgets = []

        async def fake(model, messages, params=None):
            prompt = messages[-1]["content"]
            if prompt.startswith("Reply with the single word OK"):
                budgets.append(params["max_tokens"])
                if params["max_tokens"] < d.TOKENIZER_RETRY_TOKENS:
                    raise RuntimeError("kiro: провайдер завершил поток без текста. Чаще всего это отказ модели")
                return {"text": "OK", "reasoning": "hm", "ttft_ms": 5, "ms": 10, "chars": 2,
                        "usage": {"prompt_tokens": 40 + len(prompt) // 4, "completion_tokens": 30, "estimated": False}}
            text = self._answer(prompt)
            return {"text": text, "reasoning": "", "ttft_ms": 5, "ms": 10, "chars": len(text)}

        run = self._run(fake)
        rows = [row for row in run["probes"] if row["kind"] == "tokenizer"]
        self.assertTrue(all("error" not in row and row["retried"] == "reasoning_budget" for row in rows))
        self.assertNotIn(run["report"]["tokenizer"]["verdict"], ("failed", "not_run"))
        self.assertEqual(budgets.count(d.TOKENIZER_RETRY_TOKENS), 5)

    def test_a_transient_gateway_error_gets_one_retry(self) -> None:
        class Upstream(Exception):
            status = 503

        failed = set()

        async def fake(model, messages, params=None):
            prompt = messages[-1]["content"]
            if "Как точно" in prompt and prompt not in failed:
                failed.add(prompt)
                raise Upstream("busy")
            text = self._answer(prompt)
            return {"text": text, "reasoning": "", "ttft_ms": 5, "ms": 10, "chars": len(text)}

        with patch("app.detector.asyncio.sleep", new=_instant_sleep):
            run = self._run(fake)
        probe = next(row for row in run["probes"] if row["id"] == "identity_ru")
        self.assertNotIn("error", probe)
        self.assertEqual(probe["retried"], "transient")

    def test_a_hanging_probe_times_out_and_the_rest_still_gives_a_verdict(self) -> None:
        async def fake(model, messages, params=None):
            prompt = messages[-1]["content"]
            if "JSON array of strings" in prompt:
                await asyncio.sleep(30)
            text = self._answer(prompt)
            return {"text": text, "reasoning": "", "ttft_ms": 5, "ms": 10, "chars": len(text)}

        with patch.object(d, "PROBE_TIMEOUT", 0.05), patch.object(d, "TOKENIZER_TIMEOUT", 0.05), \
                patch.object(d, "MIN_PROBE_TIMEOUT", 0.01):
            run = self._run(fake)
        self.assertEqual(run["state"], "done")
        quiz = next(row for row in run["probes"] if row["id"] == "quiz")
        self.assertTrue(quiz["timed_out"])
        self.assertIn("quiz", run["report"]["quality"]["timed_out"])
        self.assertEqual(run["report"]["claimed"], "claude")

    def test_the_run_deadline_keeps_what_came_back(self) -> None:
        async def fake(model, messages, params=None):
            prompt = messages[-1]["content"]
            if "JSON array of strings" in prompt or "Repeat each numbered line" in prompt:
                await asyncio.sleep(30)
            text = self._answer(prompt)
            return {"text": text, "reasoning": "", "ttft_ms": 5, "ms": 10, "chars": len(text)}

        with patch.object(d, "RUN_DEADLINE", 0.3), patch.object(d, "MIN_PROBE_TIMEOUT", 0.01):
            run = self._run(fake)
        self.assertEqual(run["state"], "done")
        quality = run["report"]["quality"]
        self.assertTrue(quality["partial"])
        self.assertIn("quiz", quality["timed_out"])
        self.assertNotEqual(run["report"]["confidence"], "high")
        self.assertEqual(run["report"]["distribution"][0]["family"], "claude")

_real_sleep = asyncio.sleep

async def _instant_sleep(delay, *args, **kwargs):
    await _real_sleep(0)

class PricingLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        pricing._memo.clear()

    def test_prefix_dropping_never_leaves_a_bare_fragment(self) -> None:
        self.assertEqual(pricing._candidates("grok-grok-3"), ["grok-grok-3", "grok-3"])
        self.assertNotIn("pro", pricing._candidates("antigravity-gemini-3.1-pro-high"))
        self.assertNotIn("of-agents", pricing._candidates("genspark-mixture-of-agents"))

    def test_router_names_and_legacy_models_get_a_price(self) -> None:
        auto = pricing.lookup("kiro-auto")
        self.assertIsNotNone(auto)
        self.assertTrue(auto.approximate)
        self.assertEqual(pricing.lookup("genspark-o3-pro").input, 20.0)
        self.assertEqual(pricing.lookup("o1").input, 15.0)
        self.assertIsNone(pricing.lookup("genspark-mixture-of-agents"))

    def test_a_free_catalogue_listing_does_not_price_a_paid_model_at_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "pricing-cache.json"
            cache.write_text(json.dumps({"at": time.time(), "rows": [
                {"id": "promo/claude-sonnet-4.5", "price": {"input": 0, "output": 0}},
                {"id": "acme/brand-new-model", "price": {"input": 0, "output": 0}},
            ]}), encoding="utf-8")
            with patch.object(pricing, "OWN_CACHE", cache), \
                    patch.object(pricing, "CATALOG_MODULE_CACHE", Path(temp) / "none.json"):
                pricing._index_stamp = (-1.0, -1.0)
                price = pricing.lookup("kiro-claude-sonnet-4-5")
                self.assertEqual(price.source, "builtin")
                self.assertEqual(price.input, 3.0)

                self.assertEqual(pricing.lookup("custom-brand-new-model").input, 0.0)
            pricing._index_stamp = (-1.0, -1.0)

    def test_cache_and_reasoning_prices(self) -> None:
        full = pricing.estimate("claude-sonnet-4-5", input_tokens=1_000_000)
        cached = pricing.estimate("claude-sonnet-4-5", input_tokens=1_000_000, cache_read=800_000)
        self.assertAlmostEqual(full.cost, 3.0)
        self.assertAlmostEqual(cached.cost, 0.2 * 3.0 + 0.8 * 0.30)
        special = pricing.estimate("x", output_tokens=1000, reasoning_tokens=400,
                                   overrides={"x": {"input": 1, "output": 10, "reasoning": 20}})
        self.assertAlmostEqual(special.cost, (600 * 10 + 400 * 20) / 1e6)

class MetricsCostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "metrics.json")
        pricing._memo.clear()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_every_model_gets_a_cost_and_subscriptions_are_api_equivalent(self) -> None:
        m = metrics_module.Metrics(self.path)

        m.record(provider="kiro", model="claude-sonnet-4-5", ok=True, ms=900, prompt_tokens=10_000, completion_tokens=1_000)
        m.record(provider="custom", model="my-or-openai/gpt-4o-mini", ok=True, ms=900, prompt_tokens=10_000,
                 completion_tokens=1_000)
        m.record(provider="local", model="local-qwen3-8b", ok=True, ms=900, prompt_tokens=500, completion_tokens=100)
        m.record(provider="genspark", model="genspark-mixture-of-agents", ok=True, ms=900, prompt_tokens=800,
                 completion_tokens=200)
        snap = m.snapshot("day")
        rows = {row["model"]: row for row in snap["models"]}
        self.assertEqual(rows["claude-sonnet-4-5"]["billing"], "subscription")
        self.assertAlmostEqual(rows["claude-sonnet-4-5"]["cost_usd"], 0.045)
        self.assertGreater(rows["my-or-openai/gpt-4o-mini"]["cost_usd"], 0)
        self.assertEqual(rows["my-or-openai/gpt-4o-mini"]["billing"], "api")
        self.assertEqual(rows["local-qwen3-8b"]["cost_state"], "local")
        self.assertIsNone(rows["genspark-mixture-of-agents"]["cost_usd"])
        self.assertEqual(rows["genspark-mixture-of-agents"]["cost_state"], "unknown")
        self.assertFalse(snap["cost_complete"])
        self.assertEqual(snap["cost"]["unknown_models"], ["genspark-mixture-of-agents"])
        self.assertAlmostEqual(snap["cost"]["api_equivalent"], 0.045)
        self.assertAlmostEqual(snap["cost_usd"], snap["cost"]["api_equivalent"] + snap["cost"]["api_billed"], places=6)
        self.assertAlmostEqual(sum(point["cost"] for point in snap["series"]), snap["cost_usd"], places=5)

    def test_reported_charge_cache_and_reasoning_are_used(self) -> None:
        m = metrics_module.Metrics(self.path)
        m.record(provider="custom", model="or-anthropic/claude-sonnet-4.5", ok=True, ms=500, prompt_tokens=1000,
                 completion_tokens=100, cost_usd=0.0123)
        m.record(provider="kiro", model="kiro-claude-sonnet-4-5", ok=True, ms=500, prompt_tokens=1_000_000,
                 completion_tokens=0, cache_read_tokens=1_000_000, reasoning_tokens=0)
        rows = {row["model"]: row for row in m.snapshot("day")["models"]}
        self.assertEqual(rows["or-anthropic/claude-sonnet-4.5"]["cost_state"], "billed")
        self.assertAlmostEqual(rows["or-anthropic/claude-sonnet-4.5"]["cost_usd"], 0.0123)
        self.assertAlmostEqual(rows["kiro-claude-sonnet-4-5"]["cost_usd"], 0.30)
        self.assertEqual(rows["kiro-claude-sonnet-4-5"]["cache_tokens"], 1_000_000)

    def test_the_total_prices_models_beyond_the_table(self) -> None:
        m = metrics_module.Metrics(self.path)
        for index in range(30):
            m.record(provider="kiro", model=f"kiro-claude-sonnet-4-5-v{index}", ok=True, ms=10,
                     prompt_tokens=1_000_000, completion_tokens=0)
        snap = m.snapshot("day")
        self.assertEqual(len(snap["models"]), 25)
        self.assertEqual(snap["models_total"], 30)
        self.assertAlmostEqual(snap["cost_usd"], 90.0)

    def test_version_1_history_is_backfilled_from_hourly_tokens(self) -> None:
        stamp = int(time.time() // 3600) * 3600 - 3600
        Path(self.path).write_text(json.dumps({"hours": {str(stamp): {
            "total": 4, "ok": 4, "failed": 0, "prompt_tokens": 40_000, "completion_tokens": 8_000, "ms": 100,
            "providers": {"kiro": 3, "genspark": 1}, "models": {"kiro-claude-sonnet-4-5": 3, "genspark-gpt-5.4": 1},
            "errors": {}, "lat": [0] * (len(metrics_module.LAT_EDGES) + 1)}}}), encoding="utf-8")
        snap = metrics_module.Metrics(self.path).snapshot("day")
        rows = {row["model"]: row for row in snap["models"]}
        self.assertEqual(rows["kiro-claude-sonnet-4-5"]["prompt_tokens"], 30_000)
        self.assertEqual(rows["kiro-claude-sonnet-4-5"]["tokens_estimated"], 36_000)
        self.assertAlmostEqual(rows["kiro-claude-sonnet-4-5"]["cost_usd"], 30_000 * 3 / 1e6 + 6_000 * 15 / 1e6)
        self.assertGreater(rows["genspark-gpt-5.4"]["cost_usd"], 0)
        self.assertTrue(snap["cost"]["legacy_estimated"])
        self.assertTrue(snap["cost_complete"])

    def test_current_data_is_not_smeared_and_round_trips_through_the_file(self) -> None:
        m = metrics_module.Metrics(self.path)
        m.record(provider="kiro", model="kiro-claude-sonnet-4-5", ok=True, ms=10, prompt_tokens=100,
                 completion_tokens=10, cache_read_tokens=50, reasoning_tokens=5, cost_usd=None)
        m.record(provider="kiro", model="kiro-claude-haiku-4.5", ok=False, ms=10, error_code="rate_limit_error")
        m.flush(force=True)
        snap = metrics_module.Metrics(self.path).snapshot("day")
        rows = {row["model"]: row for row in snap["models"]}
        self.assertNotIn("tokens_estimated", rows["kiro-claude-sonnet-4-5"])
        self.assertEqual(rows["kiro-claude-sonnet-4-5"]["provider_id"], "kiro")
        self.assertEqual(rows["kiro-claude-sonnet-4-5"]["cache_tokens"], 50)
        self.assertEqual(rows["kiro-claude-haiku-4.5"]["cost_state"], "none")
        self.assertEqual(rows["kiro-claude-haiku-4.5"]["cost_usd"], 0.0)
        self.assertFalse(snap["cost"]["legacy_estimated"])
        saved = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 3)

class UsagePlumbingTests(unittest.TestCase):
    def test_chat_records_cached_reasoning_and_reported_cost(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "app" / "chat.py").read_text(encoding="utf-8")
        for needle in ("cache_read_tokens=", "reasoning_tokens=", "cost_usd=", '"prompt_tokens_details"'):
            self.assertIn(needle, source)
        custom = (Path(__file__).resolve().parents[1] / "app" / "providers" / "custom.py").read_text(encoding="utf-8")
        self.assertIn('usage_event["cached_tokens"]', custom)
        self.assertIn('usage_event["cost"]', custom)

if __name__ == "__main__":
    unittest.main()
