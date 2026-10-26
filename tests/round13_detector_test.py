"""Round 13: the detector checks the tokenizer and the provider's hidden instructions.

The reference counts were computed with published tokenizers or measured at
the vendors; these tests pin the arithmetic around them, not the network.
"""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import detector, model_call, tokenizer_probe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

def measured(base: int, counts, base2: int = 0, estimated: bool = False) -> dict:
    """Probe results as a provider that reports its own token counts would give them."""
    usage = lambda tokens: {"usage": {"prompt_tokens": tokens, "estimated": estimated}, "kind": "tokenizer", "text": "OK"}
    out = {"tokenizer_base": usage(base)}
    for name, count in zip(tokenizer_probe.ORDER, counts):
        out[f"tokenizer_{name}"] = usage((base2 or base) + count)
    if base2:
        out["tokenizer_base2"] = usage(base2)
    return out

class TokenizerFingerprintTest(unittest.TestCase):
    def test_the_probe_texts_are_frozen(self) -> None:

        blob = (tokenizer_probe.PREFIX + "|" + "|".join(tokenizer_probe.SEGMENTS[name] for name in tokenizer_probe.ORDER)
                + "|" + tokenizer_probe.SUFFIX).encode("utf-8")
        self.assertEqual(hashlib.sha256(blob).hexdigest(), FROZEN)
        plan = tokenizer_probe.probe_plan()
        self.assertEqual([step["id"] for step in plan],
                         ["tokenizer_base", "tokenizer_prose", "tokenizer_symbols", "tokenizer_scripts", "tokenizer_base2"])
        self.assertTrue(all(step["chain"] == "tokenizer" and step["max_tokens"] == 16 for step in plan))

    def test_references_are_distinguishable_where_it_matters(self) -> None:
        rows = {row["id"]: row for row in tokenizer_probe.REFERENCES}
        for name in ("o200k", "claude", "gemini", "qwen", "deepseek", "llama3", "mistral", "glm"):
            self.assertIn(name, rows)
        self.assertTrue(all(len(row["counts"]) == 3 and row["source"] in ("offline", "first-party", "observed")
                            for row in rows.values()))
        apart = [("o200k", "claude"), ("o200k", "gemini"), ("claude", "gemini"), ("o200k", "qwen"), ("claude", "claude-2026"),
                 ("o200k", "llama4"), ("o200k", "minimax"), ("gemini", "mistral"), ("qwen", "hunyuan"), ("o200k", "deepseek")]
        for left, right in apart:
            self.assertGreater(tokenizer_probe.distance(rows[left]["counts"], rows[right]["counts"]),
                               tokenizer_probe.MATCH_TOLERANCE * 1.5, (left, right))

    def test_exact_counts_confirm_or_refute_the_claimed_family(self) -> None:
        genuine = tokenizer_probe.assess(measured(79, [188, 224, 228], 79), "claude")
        self.assertEqual((genuine["verdict"], genuine["families"], genuine["strong"]), ("match", ["claude"], True))
        self.assertEqual(genuine["hidden_tokens"], 49)
        off_by_one = tokenizer_probe.assess(measured(20, [187, 223, 229]), "claude")
        self.assertEqual(off_by_one["verdict"], "match", "a token of boundary noise is tolerated")
        fake = tokenizer_probe.assess(measured(20, [159, 192, 147]), "claude")
        self.assertEqual((fake["verdict"], fake["families"]), ("mismatch", ["gpt"]))
        self.assertEqual(fake["claimed_reference"]["id"], "claude")
        self.assertEqual(tokenizer_probe.assess(measured(20, [175, 285, 132]), "")["verdict"], "identified")
        twins = tokenizer_probe.assess(measured(20, [160, 202, 159]), "deepseek")
        self.assertEqual((twins["verdict"], twins["families"]), ("match", ["deepseek", "llama"]),
                         "DeepSeek and Llama 3 count alike: both are reported, the claim still fits")
        self.assertEqual(tokenizer_probe.assess(measured(20, [279, 262, 229]), "claude")["strong"], False,
                         "a reference seen only through gateways is a hint")

    def test_silent_and_lying_providers_are_named_as_such(self) -> None:
        self.assertEqual(tokenizer_probe.assess({}, "claude")["verdict"], "not_run")
        self.assertEqual(tokenizer_probe.assess(measured(20, [188, 224, 228], estimated=True), "claude")["verdict"], "no_usage",
                         "Proxy's estimate by length is not a tokenizer")
        broken = measured(20, [188, 224, 228])
        broken["tokenizer_symbols"] = {"error": "429"}
        self.assertEqual(tokenizer_probe.assess(broken, "claude")["verdict"], "failed")
        made_up = tokenizer_probe.assess(measured(1484, [56, 53, 46]), "claude")
        self.assertEqual((made_up["verdict"], made_up["hidden_tokens"]), ("implausible", 1454))
        self.assertEqual(tokenizer_probe.assess(measured(20, [240, 240, 240]), "grok")["verdict"], "unknown")
        self.assertEqual(tokenizer_probe.assess(measured(20, [196, 216, 238]), "claude")["verdict"], "unknown",
                         "close to nothing, but not far from the claimed family either")
        self.assertEqual(tokenizer_probe.assess(measured(20, [120, 330, 90]), "claude")["verdict"], "unlike_claimed")

    def test_a_moving_hidden_prompt_does_not_hide_the_fingerprint(self) -> None:

        shifted = tokenizer_probe.assess(measured(4147, [217, 253, 257]), "claude")
        self.assertEqual((shifted["verdict"], shifted["shift"], shifted["strong"]), ("match", 29, False))

        flipping = tokenizer_probe.assess(measured(4147, [188, 225, 199], 4176), "claude")
        self.assertEqual((flipping["verdict"], flipping["stable"], flipping["strong"]), ("match", False, False))

        warmed = tokenizer_probe.assess(measured(4118, [188, 223, 228], 4147), "claude")
        self.assertEqual((warmed["verdict"], warmed["shift"], warmed["base"]), ("match", 0, 4147))

        self.assertEqual(tokenizer_probe.assess(measured(20, [200, 242, 199]), "deepseek")["families"], ["deepseek"])

        self.assertEqual(tokenizer_probe.assess(measured(20, [588, 624, 628]), "claude")["verdict"], "unlike_claimed")

        self.assertEqual(tokenizer_probe.assess(measured(20, [120, 330, 90], 400), "claude")["verdict"], "unknown")

FROZEN = "911c633e1cbb5a808bf85b8a4380e044bad72df2c6eb8d501ec93ba3bd6f3d41"

def answers(family_line: str) -> dict:
    return {
        "identity_en": {"kind": "identity", "text": family_line},
        "identity_ru": {"kind": "identity", "text": family_line},
        "identity_base": {"kind": "identity", "text": family_line},
        "identity_version": {"kind": "identity", "text": "no specific version known"},
        "tokens": {"kind": "tokens", "text": "unknown"},
        "quiz": {"kind": "quiz", "text": "[]"},
        "specs": {"kind": "specs", "text": "context: unknown, output: unknown"},
    }

class DetectorUsesTheTokenizerTest(unittest.TestCase):
    def test_the_plan_dropped_weak_probes_and_gained_the_tokenizer(self) -> None:
        quick = [step["id"] for step in detector._probe_plan("quick")]
        full = [step["id"] for step in detector._probe_plan("full")]
        self.assertEqual((len(quick), len(full)), (14, 17))
        for removed in ("style", "needle", "ability_logic", "ability_letters"):
            self.assertNotIn(removed, full)
        self.assertIn("tokenizer_scripts", quick)
        self.assertIn("wrapper", full)
        self.assertNotIn("wrapper", quick)
        self.assertFalse(hasattr(detector, "score_style"))

    def test_a_foreign_tokenizer_overrules_a_convincing_self_report(self) -> None:

        results = {**answers("I am Claude, made by Anthropic."), **measured(60, [159, 192, 147], 60)}
        report = detector.analyse("reseller-claude-opus-5", results)
        self.assertEqual(report["verdict"], "substituted")
        self.assertEqual(report["tokenizer"]["verdict"], "mismatch")
        self.assertTrue(report["spoof_suspected"])
        bad = [row for row in report["reasons"] if row["level"] == "bad"]
        self.assertTrue(any("Токенизатор чужой" in row["ru"] and "159 / 192 / 147" in row["ru"] for row in bad), report["reasons"])
        self.assertTrue(any("foreign tokenizer" in row["en"] for row in bad))

    def test_a_matching_tokenizer_is_strong_evidence_and_hidden_prompts_are_reported(self) -> None:
        results = {**answers("I am Claude, made by Anthropic."), **measured(4147, [188, 224, 228], 4147),
                   "wrapper": {"kind": "wrapper", "text": '{"instructions": true, "persona": "Kiro", "gist": "coding agent rules"}'}}
        report = detector.analyse("gateway-claude-sonnet-4-6", results)
        self.assertEqual(report["verdict"], "genuine")
        self.assertEqual(report["tokenizer"]["verdict"], "match")
        self.assertEqual(report["wrapper"], {"instructions": True, "persona": "Kiro", "gist": "coding agent rules"})
        text = " ".join(row["ru"] for row in report["reasons"])
        self.assertIn("Токенизатор совпал", text)
        self.assertIn("около 4117 токенов скрытых инструкций", text)
        self.assertIn("«Kiro»", text)
        evidence = {row["probe"]: row for row in report["evidence"]}
        self.assertEqual(evidence["tokenizer"]["weight"], "high")
        self.assertNotIn("style", evidence)
        self.assertNotIn("ability", evidence)
        without = detector.analyse("gateway-claude-sonnet-4-6", answers("I am Claude, made by Anthropic."))
        self.assertGreater(report["claimed_percent"], without["claimed_percent"], "the tokenizer adds confidence")
        self.assertEqual(without["tokenizer"]["verdict"], "not_run")

    def test_kiro_persona_is_not_amazon_nova_and_bad_json_is_no_wrapper_claim(self) -> None:
        self.assertEqual(detector._mentions("I'm Kiro, an AI assistant built by Amazon Web Services (AWS)."), {})
        self.assertIn("nova", detector._mentions("I am Amazon Nova Pro."))
        self.assertEqual(detector.score_wrapper("I have no idea"), {"instructions": None, "persona": "", "gist": ""})
        self.assertEqual(detector.score_wrapper('Sure: {"instructions": false, "persona": "", "gist": ""}')["instructions"], False)

    def test_tokenizer_probes_run_one_after_another_with_small_answers(self) -> None:
        calls: list = []
        running = {"tokenizer": 0, "peak": 0}

        async def fake_complete(model, messages, params):
            content = messages[-1]["content"]
            is_probe = content.startswith(tokenizer_probe.PREFIX)
            if is_probe:
                running["tokenizer"] += 1
                running["peak"] = max(running["peak"], running["tokenizer"])
            await asyncio.sleep(0.01)
            if is_probe:
                running["tokenizer"] -= 1
                calls.append(params["max_tokens"])
                segment = content[len(tokenizer_probe.PREFIX):-len(tokenizer_probe.SUFFIX)]
                extra = {tokenizer_probe.SEGMENTS[name]: count for name, count in zip(tokenizer_probe.ORDER, [175, 285, 132])}
                return {"text": "OK", "reasoning": "", "ttft_ms": 5, "ms": 10, "chars": 2,
                        "usage": {"prompt_tokens": 15 + extra.get(segment, 0), "completion_tokens": 1, "estimated": False}}
            return {"text": "I am Gemini, trained by Google.", "reasoning": "", "ttft_ms": 5, "ms": 10, "chars": 30, "usage": {}}

        async def go():
            with patch.object(model_call, "complete", fake_complete), patch.object(detector, "_remember", lambda run: None):
                run = await detector.start("gateway-gemini-3.8-flash", "quick")
                for _ in range(400):
                    await asyncio.sleep(0.01)
                    state = detector.get_run(run["id"])
                    if state["state"] != "running":
                        return state
            return state

        state = asyncio.run(go())
        self.assertEqual(state["state"], "done", state.get("error"))
        self.assertEqual(calls, [16] * 5)
        self.assertEqual(running["peak"], 1, "never two tokenizer requests at once")
        self.assertEqual(state["report"]["tokenizer"]["verdict"], "match")
        self.assertEqual(state["report"]["tokenizer"]["matches"][0]["id"], "gemini")
        probe = next(row for row in state["probes"] if row["id"] == "tokenizer_prose")
        self.assertEqual(probe["usage"]["prompt_tokens"], 190)

    def test_the_panel_shows_the_tokenizer_and_the_real_counts(self) -> None:
        js = (ROOT / "app" / "static" / "detector.js").read_text(encoding="utf-8")
        for needle in ("tokenizerStat(r.tokenizer)", "tokenizer_base2", "Скрытые инструкции", "14 запросов", "17 запросов",
                       "счёт провайдера", "tokens_per_s"):
            self.assertIn(needle, js)
        self.assertNotIn("Полная проверка добавляет поиск фразы", js)

if __name__ == "__main__":
    unittest.main()
