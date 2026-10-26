"""Round 36 (r36-proxy): MemuAI Proxy — model detector (visual test), composite models, quota planner.

No network: the composite model checks reuse the fakes of round35_composite_test.py.
Run: NO_PROXY=127.0.0.1,localhost python -m pytest -q tests/round36_proxy_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r36-proxy-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import composite_models as cm, visual_test  # noqa: E402
from tests.round35_composite_test import FakeService, isolated, model, run_composite  # noqa: E402,F401

STATIC = ROOT / "app" / "static"
PELICAN_URL = "https://pelicanbenchmark.com/?track=animated_html"

def test_the_claude_opus_references_are_gone_everywhere():
    assert not (STATIC / "visual-refs.json").exists()
    assert not hasattr(visual_test, "REFS_FILE") and not hasattr(visual_test, "reference_page")
    js = (STATIC / "detector.js").read_text(encoding="utf-8")
    for needle in ("visual/refs/", "V.refs", "visualRefLine", "det-ref-badge", "рядом с эталоном", "next to the reference"):
        assert needle not in js, needle
    css = (STATIC / "detector-compare.css").read_text(encoding="utf-8")
    assert ".det-visual-ref" not in css and ".det-ref-badge" not in css and "is-ref" not in css
    api = (ROOT / "app" / "proxy_tools_api.py").read_text(encoding="utf-8")
    assert "/detector/visual/refs/" not in api and "reference_list" not in api
    for doc in ("app/docs/ru/model-detector.md", "app/docs/en/model-detector.md", "app/companion_kb/detector.md"):
        assert "Opus 5.5" not in (ROOT / doc).read_text(encoding="utf-8"), doc

def test_the_reference_route_is_gone_and_prompts_carry_no_refs():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.proxy_tools_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/api/admin/detector/visual/refs/pelican-claude-opus-5-5/page").status_code == 404

def test_the_pelican_links_to_pelicanbenchmark_and_only_the_pelican():
    rows = {row["id"]: row for row in visual_test.prompt_list()}
    assert rows["pelican"]["compare_url"] == PELICAN_URL
    assert rows["aquarium"]["compare_url"] == ""
    assert [row_id for row_id, row in rows.items() if row["compare_url"]] == ["pelican"]
    data = json.loads((STATIC / "visual-prompts.json").read_text(encoding="utf-8"))
    assert any(row.get("compare_url") == PELICAN_URL for row in data["prompts"])

def test_the_gallery_link_opens_outside_and_is_bilingual():
    js = (STATIC / "detector.js").read_text(encoding="utf-8")
    start = js.index("function visualGalleryLink")
    body = js[start:js.index("\n  }\n", start)]
    assert 'target="_blank" rel="noopener noreferrer"' in body
    assert 'L("Как этот тест рисуют разные ИИ", "How different AIs draw this test")' in body
    assert "Сравнение на ${host}" in body and "Comparison on ${host}" in body
    assert "${visualGalleryLink(row)}" in js
    css = (STATIC / "detector-compare.css").read_text(encoding="utf-8")
    assert ".det-visual-gallery {" in css and ".det-visual-gallery:focus-visible" in css

def test_compare_is_offered_only_between_two_finished_runs():
    js = (STATIC / "detector.js").read_text(encoding="utf-8")
    assert "if (sources.length < 2)" in js
    assert 'run.state === "done" && (V.runs.some((other) => other !== run && other.prompt_id === run.prompt_id' in js

def test_a_non_https_gallery_address_is_dropped():
    saved = visual_test._prompts_cache
    try:
        visual_test._prompts_cache = [{"id": "x", "prompt": "p", "compare_url": "javascript:alert(1)"}]
        assert visual_test.prompt_list()[0]["compare_url"] == ""
    finally:
        visual_test._prompts_cache = saved

def test_a_rejected_key_is_unavailable_and_always_switches():
    from app.chat import CompletionError

    assert cm.classify(CompletionError("rejected", status=503, err_type="authentication_error")) == "unavailable"
    assert cm.classify(CompletionError("gone", status=404, err_type="model_unavailable")) == "unavailable"
    assert cm.classify(CompletionError("down", status=503, err_type="service_unavailable")) == "5xx"
    cm.store.save_all([model(default={"candidates": ["fast-text", "big-ctx"]}, fallback={"on": ["429"]})])
    svc = FakeService({"fast-text": (503, "authentication_error", "Account rejected")})
    out = run_composite(svc)
    assert svc.calls[-1] == "big-ctx"
    assert any("big-ctx" in str(item) for item in out)
    recent = cm.stats.get("memu/smart")["recent"][0]
    assert [a["trigger"] for a in recent["attempts"]] == ["unavailable", ""]

def test_stream_chunks_carry_the_composite_name():
    cm.store.save_all([model()])
    out = run_composite(FakeService({}))
    chunks = [item for item in out if item.get("object") == "chat.completion.chunk"]
    assert chunks and all(item.get("model") in (None, "memu/smart") for item in chunks)

def test_try_gets_its_own_route_not_the_newest_stats_row():
    import asyncio
    cm.store.save_all([model()])
    sink = {}

    async def go():
        async for _ in cm.run(FakeService({"fast-text": (429, "rate_limit_error", "slow down")}), "memu/smart",
                              [{"role": "user", "content": "hi"}], {"_stream": True, "_composite_route_sink": sink}):
            pass
    asyncio.run(go())
    assert sink["chosen"] == "big-ctx" and [a["model"] for a in sink["attempts"]] == ["fast-text", "big-ctx"]
    api = (ROOT / "app" / "composite_models_api.py").read_text(encoding="utf-8")
    assert '"_composite_route_sink": route' in api and 'stats.get(model_id).get("recent")' not in api

def test_a_tokenizer_unlike_the_claimed_family_caps_the_confidence():
    src = (ROOT / "app" / "detector.py").read_text(encoding="utf-8")
    assert 'if confidence == "high" and tokenizer["verdict"] in ("recount", "unlike_claimed"):' in src

def test_the_recommended_pace_never_exceeds_the_remainder_in_the_last_hour():
    from app import quota_planner as qp
    now = 1_000_000.0
    out = qp._finish({"remaining": 0.95, "limit": 10000, "unit": "запросов"}, now + 49 * 60, now)
    assert out["pace_units"] <= 0.95 * 10000
    js = (STATIC / "quota-planner.js").read_text(encoding="utf-8")
    assert 'const steady = w.status !== "insufficient";' in js

def test_the_same_model_on_two_accounts_is_told_apart_in_the_route():
    js = (STATIC / "composite.js").read_text(encoding="utf-8")
    assert "const twin = avail().some((o) => o !== m && !o.composite && o.name === m.name);" in js
    assert 'unavailable: ["модель недоступна (ключ, доступ)", "model unavailable (key, access)"]' in js
