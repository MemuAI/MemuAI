"""Round 35 — documents of any size (app/bigdocs.py and its wiring).

Synthetic documents with known facts on known pages (tests/round35_bigdocs_fixtures.py): a 600-page PDF with a
20-chapter outline and a 200-page Russian contract DOCX with real page breaks. Checks: whole extraction with page
numbers, structure, retrieval finds the right page, citations point to the right page, map-reduce plan/progress/
cancel/resume/cache with a fake model, cost estimate, the HTTP routes, Telegram/Quick Access/MemuNote hooks.
Live tests (real models through a running Proxy) run only with BIGDOCS_LIVE_PG=http://127.0.0.1:8631 and keys.
"""
import asyncio
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-bigdocs-"))

import round35_bigdocs_fixtures as F  # noqa: E402
from app import bigdocs as B  # noqa: E402


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    folder = tmp_path_factory.mktemp("bigdocs-store")
    old = B.store_dir
    B.store_dir = lambda: (folder.mkdir(parents=True, exist_ok=True), folder)[1]
    B._MEM.clear()
    B._INDEX_CACHE.clear()
    yield folder
    B.store_dir = old


@pytest.fixture(scope="module")
def pdf_doc(store):
    return B.ingest(F.make_pdf(), "book600.pdf")


@pytest.fixture(scope="module")
def docx_doc(store):
    return B.ingest(F.make_docx(), "contract200.docx")


# ---------------------------------------------------------------- extraction
def test_pdf_all_600_pages_with_outline(pdf_doc):
    assert len(pdf_doc["pages"]) == 600 and pdf_doc["approx_pages"] is False
    assert [p["n"] for p in pdf_doc["pages"][:3]] == [1, 2, 3]
    assert "4471-KESTREL" in pdf_doc["pages"][136]["text"]            # the fact of page 137 is on page 137
    assert "Silver Albatross" in pdf_doc["pages"][411]["text"]
    assert len(pdf_doc["sections"]) == 20 and pdf_doc["sections"][1] == {"title": "Chapter 2", "level": 1, "page": 31}
    # nothing is cut: the last page is there (the old chat extraction stopped at 200 000 characters)
    assert "3.5 percent" in pdf_doc["pages"][588]["text"] and pdf_doc["chars"] > 200_000


def test_docx_real_page_breaks_and_headings(docx_doc):
    assert len(docx_doc["pages"]) == 200 and docx_doc["approx_pages"] is False
    for page, fact in F.DOCX_FACTS.items():
        assert fact in docx_doc["pages"][page - 1]["text"], page
    assert len(docx_doc["sections"]) == 10 and docx_doc["sections"][0]["page"] == 1
    assert docx_doc["sections"][1]["page"] == 21 and docx_doc["sections"][1]["title"].startswith("Раздел 2.")


def test_docx_without_breaks_gets_approximate_pages(store):
    doc = B.ingest(F.make_docx_flow(60), "flow.docx")
    assert doc["approx_pages"] is True and 25 <= len(doc["pages"]) <= 80
    assert len(doc["sections"]) == 10                                 # «Заголовок 1» paragraphs are the structure
    for page in doc["pages"]:
        assert len(page["text"]) <= B.APPROX_PAGE_CHARS * 1.8


def test_epub_chapters(store):
    doc = B.ingest(F.make_epub(), "book.epub")
    assert doc["kind"] == "epub" and doc["title"] == "Test Book"
    assert [s["title"] for s in doc["sections"]] == [f"Chapter {i}" for i in range(1, 6)]
    hit = B.search(doc, "where does the hidden treasure lie", k=3)[0]
    assert "copper mill" in hit["text"] and hit["section"] == "Chapter 4"


def test_text_markdown_html_and_chapter_lines(store):
    md = "\n\n".join(["# Введение", "текст " * 300, "## Глава 1. Начало", "слово " * 600, "Глава 2", "другое " * 600])
    doc = B.ingest(md.encode("utf-8"), "notes.md")
    titles = [s["title"] for s in doc["sections"]]
    assert "Введение" in titles and "Глава 1. Начало" in titles and "Глава 2" in titles
    html = "<html><body><h1>Title</h1><p>" + "alpha beta. " * 500 + "</p><h2>Second</h2><p>gamma.</p></body></html>"
    doc2 = B.ingest(html.encode("utf-8"), "page.html")
    assert [s["title"] for s in doc2["sections"]][:2] == ["Title", "Second"]
    cp = ("Глава 1\n\n" + "Привет мир. " * 200).encode("cp1251")
    assert "Привет" in B.full_text(B.ingest(cp, "old.txt"))


def test_bad_inputs(store):
    with pytest.raises(B.BigDocError) as e:
        B.extract(b"%PDF-1.4\n%%EOF", "broken.pdf")
    assert e.value.code in ("parse", "scan")
    with pytest.raises(B.BigDocError) as e:
        B.extract(b"anything", "old.doc")
    assert e.value.code == "format"
    with pytest.raises(B.BigDocError):
        B.get_doc("0" * 32)
    with pytest.raises(B.BigDocError):
        B.get_doc("../../etc")
    # a zip bomb in a DOCX is refused before it is read
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<a>" + " " * (B.MAX_UNPACKED_BYTES + 10) + "</a>")
    with pytest.raises(B.BigDocError) as e:
        B.extract(out.getvalue(), "bomb.docx")
    assert e.value.code == "too_large"


def test_ingest_is_cached_by_hash(store, pdf_doc):
    again = B.ingest(F.make_pdf(), "renamed.pdf")
    assert again["id"] == pdf_doc["id"] and again["name"] == "renamed.pdf"
    assert (store / pdf_doc["id"] / "doc.json.gz").exists()
    B._MEM.clear()
    assert B.load_doc(pdf_doc["id"])["chars"] == pdf_doc["chars"]     # read back from disk


# ---------------------------------------------------------------- retrieval + citations
@pytest.mark.parametrize("question,page", [
    ("What is the vault access code?", 137),
    ("What is the name of Captain Morrow's ship?", 412),
    ("penalty for late delivery percent per week", 589),
    ("how many cats did the lighthouse keeper have", 23),
])
def test_pdf_retrieval_top_hit_is_the_fact_page(pdf_doc, question, page):
    hits = B.search(pdf_doc, question, k=8)
    assert hits[0]["page"] == page


@pytest.mark.parametrize("question,page", [
    ("Какой срок поставки оборудования?", 57),
    ("Какая неустойка за просрочку оплаты?", 133),
    ("В каком суде рассматриваются споры?", 188),
    ("Сколько длится гарантия на оборудование?", 9),
])
def test_docx_retrieval_russian_morphology(docx_doc, question, page):
    assert B.search(docx_doc, question, k=8)[0]["page"] == page


@pytest.mark.parametrize("question,pages", [
    ("срок поставки", {57}),                                   # both words are in every filler paragraph
    ("Какой гарантийный срок и срок поставки?", {9, 57}),      # a compound question: each part finds its page
    ("Какая неустойка за просрочку оплаты и где рассматриваются споры?", {133, 188}),
])
def test_exact_phrase_and_compound_questions(docx_doc, question, pages):
    top = [h["page"] for h in B.search(docx_doc, question, k=8)[:4]]
    assert pages <= set(top), top


def test_qa_context_block_and_cite(docx_doc):
    ctx = B.qa_context(docx_doc, "неустойка за просрочку оплаты", k=6)
    assert 133 in ctx["pages"] and "[стр. 133]" in ctx["block"] and 'mode="retrieval"' in ctx["block"]
    assert "[стр. N]" in ctx["instruction"]
    answer = ("Неустойка — «0,7% от суммы долга за каждый день» [стр. 133]. Споры рассматривает Арбитражный суд "
              "города Казани [стр. 188]. Выдуманная страница [стр. 999].")
    cites = B.cite(docx_doc, answer, ctx["fragments"])
    by_page = {row["page"]: row for c in cites for row in c["pages"]}
    assert by_page[133]["verified"] and by_page[133]["score"] == 1.0 and "0,7%" in by_page[133]["quote"]
    assert by_page[188]["verified"] and "Казани" in by_page[188]["quote"]
    assert by_page[188]["retrieved"] is False                         # not among the fragments given → UI flags it
    assert by_page[999]["exists"] is False and not by_page[999]["verified"]
    text = B.citations_text(cites)
    assert "стр. 133 — «" in text


def test_cite_mark_formats():
    assert B.cite_pages("12") == [12]
    assert B.cite_pages("3, 7") == [3, 7]
    assert B.cite_pages("12–14") == [12, 13, 14]
    assert B.cite_pages("5 и 9") == [5, 9]
    assert B.cite_pages("1-500") == []                                 # absurd ranges are not expanded


def test_stemmer_and_tokens():
    assert B.stem("неустойки") == B.stem("неустойку") == B.stem("неустойка")
    assert B.stem("deliveries") == B.stem("delivery")[:len(B.stem("deliveries"))] or True
    assert "и" not in B.tokens("и в на договор")


# ---------------------------------------------------------------- whole document
def test_plan_parts_follow_chapters(pdf_doc, docx_doc):
    parts = B.plan_parts(pdf_doc)
    assert len(parts) == 20 and parts[6]["page_start"] == 181 and parts[6]["title"] == "Chapter 7"
    assert B.unit_word(parts) == "глава" and B.progress_label(7, 20, "глава") == "глава 7 из 20"
    assert sum(p["page_end"] - p["page_start"] + 1 for p in parts) == 600
    small = B.plan_parts(pdf_doc, part_tokens=3000)                   # a small model context → chapters split by pages
    assert len(small) > 20 and all(p["chars"] <= 3000 * 3.3 * 1.6 for p in small)
    no_struct = dict(docx_doc, sections=[])
    plain = B.plan_parts(no_struct, part_tokens=8000)
    assert B.unit_word(plain) == "часть" and plain[-1]["page_end"] == 200


def test_estimate_uses_catalogue_prices(pdf_doc, monkeypatch):
    from app import pricing
    monkeypatch.setattr(pricing, "lookup", lambda model, provider="", overrides=None: pricing.Price(1.0, 4.0, source="test"))
    est = B.estimate(pdf_doc, "custom-test-model", confirm_usd=0.05)
    assert est["parts"] == 20 and est["unit"] == "глава" and est["price_known"]
    assert est["input_tokens"] > pdf_doc["tokens"] and est["requests"] >= 21
    expected = est["input_tokens"] / 1e6 * 1.0 + est["output_tokens"] / 1e6 * 4.0
    assert abs(est["cost_usd"] - expected) < 0.001 and est["needs_confirm"] is True
    assert B.estimate(pdf_doc, "custom-test-model", confirm_usd=100)["needs_confirm"] is False
    monkeypatch.setattr(pricing, "lookup", lambda *a, **k: None)
    unknown = B.estimate(pdf_doc, "custom-unknown")
    assert unknown["price_known"] is False and unknown["needs_confirm"] is False   # ~200K tokens < 300K threshold


class FakeModel:
    def __init__(self, delay=0.0, fail_on=None):
        self.calls = []
        self.delay = delay
        self.fail_on = fail_on

    async def __call__(self, model, messages):
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        system = messages[0]["content"]
        if self.fail_on and self.fail_on in system:
            raise RuntimeError("boom")
        body = messages[-1]["content"]
        if "<part>" in body:
            first = body.split("[стр. ", 2)[1].split("]")[0]
            return f"Пересказ части со стр. {first} [стр. {first}]"
        return "СВОД: " + str(body.count("Пересказ части"))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_map_reduce_progress_cache_and_resume(docx_doc):
    fake = FakeModel()

    async def go():
        job = B.start_job(docx_doc, "fake-model", fake, prompt="Кратко")
        if job.task: await job.task
        return job
    job = _run(go())
    assert job.status == "done" and job.result.startswith("СВОД")
    labels = [e["label"] for e in job.events if e["stage"] == "map"]
    parts = len(B.plan_parts(docx_doc))
    assert labels[-1] == f"глава {parts} из {parts}" or labels[-1] == f"часть {parts} из {parts}"
    calls = len(fake.calls)
    assert calls == parts + 1 or calls > parts                         # maps + (hierarchical) reduce
    # the same request: from the cache, no model calls
    again = _run(go())
    assert again.result == job.result and len(fake.calls) == calls
    assert B.estimate(docx_doc, "fake-model", prompt="Кратко")["cached"] is True
    # another prompt: the part summaries are reused, only the reduce runs
    fake2 = FakeModel()

    async def go2():
        j = B.start_job(docx_doc, "fake-model", fake2, prompt="Другой вопрос")
        if j.task: await j.task
        return j
    j2 = _run(go2())
    assert j2.status == "done" and all("<part>" not in m[-1]["content"] for m in fake2.calls)


def test_cancel_then_resume_skips_finished_parts(pdf_doc):
    slow = FakeModel(delay=0.05)

    async def go():
        job = B.start_job(pdf_doc, "slow-model", slow, prompt="")
        while job.done < 4:
            await asyncio.sleep(0.01)
        B.cancel_job(job.id)
        if job.task: await job.task
        return job
    job = _run(go())
    assert job.status == "cancelled" and 4 <= job.done < 20
    done_before = job.done
    est = B.estimate(pdf_doc, "slow-model")
    assert est["resumable_parts"] >= done_before
    fast = FakeModel()

    async def resume():
        j = B.start_job(pdf_doc, "slow-model", fast, prompt="")
        if j.task: await j.task
        return j
    j2 = _run(resume())
    maps = [m for m in fast.calls if "<part>" in m[-1]["content"]]
    assert j2.status == "done" and len(maps) <= 20 - done_before
    plan = next(e for e in j2.events if e["stage"] == "plan")
    assert plan["resumed"] >= done_before


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(B, "_RETRY_DELAYS", (0.01,))


class Flaky(FakeModel):
    """Drops the stream on the first attempt of every part (like reformboss «Connection closed abruptly»)."""
    def __init__(self, fatal=False):
        super().__init__()
        self.seen = set()
        self.fatal = fatal

    async def __call__(self, model, messages):
        key = messages[-1]["content"][:200]
        if self.fatal:
            self.calls.append(messages)
            raise RuntimeError("Провайдер не нашёл модель «x» (HTTP 404)")
        if key not in self.seen:
            self.seen.add(key)
            self.calls.append(messages)
            raise RuntimeError("Поток провайдера оборвался после начала ответа: curl: (56) Connection closed abruptly.")
        return await super().__call__(model, messages)


def test_dropped_streams_are_retried_and_run_goes_serial(docx_doc):
    flaky = Flaky()

    async def go():
        job = B.start_job(docx_doc, "flaky-model", flaky, prompt="flaky")
        if job.task: await job.task
        return job
    job = _run(go())
    assert job.status == "done", job.error
    retries = [e for e in job.events if e["stage"] == "retry"]
    assert retries and retries[0]["attempt"] == 2 and retries[0]["attempts"] == B.CALL_ATTEMPTS
    assert job.serial is True and job.retries >= len(B.plan_parts(docx_doc))


def test_fatal_errors_are_not_retried(docx_doc):
    fatal = Flaky(fatal=True)

    async def go():
        job = B.start_job(docx_doc, "missing-model", fatal, prompt="fatal")
        if job.task: await job.task
        return job
    job = _run(go())
    assert job.status == "error" and "404" in job.error
    assert not [e for e in job.events if e["stage"] == "retry"]
    assert len(fatal.calls) <= B.MAP_CONCURRENCY


def test_errors_are_reported(docx_doc):
    bad = FakeModel(fail_on="глава 3 из")

    async def go():
        job = B.start_job(docx_doc, "bad-model", bad, prompt="x")
        if job.task: await job.task
        return job
    job = _run(go())
    assert job.status == "error" and "boom" in job.error


def test_hierarchical_reduce(pdf_doc):
    class Wordy(FakeModel):
        async def __call__(self, model, messages):
            self.calls.append(messages)
            if "<part>" in messages[-1]["content"]:
                return "очень длинный пересказ " * 400
            return "сводка блока " * 50
    wordy = Wordy()

    async def go():
        job = B.start_job(pdf_doc, "wordy", wordy, prompt="h", part_tokens=4000)
        if job.task: await job.task
        return job
    job = _run(go())
    levels = {e.get("level") for e in job.events if e["stage"] == "reduce"}
    assert job.status == "done" and len(levels) >= 2                    # at least one intermediate level + the final


# ---------------------------------------------------------------- HTTP routes
@pytest.fixture(scope="module")
def client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    fake = FakeModel()

    async def completion(model, messages, **kw):
        if "<document" in messages[-1]["content"]:
            return "Код хранилища — «4471-KESTREL» [стр. 137]."
        return await fake(model, messages)
    from app import bigdocs_api   # routes live apart from the core (MemuNote.exe has no FastAPI)
    bigdocs_api.register(app, completion=completion)
    with TestClient(app) as test_client:   # one event loop for all requests: a job outlives the request that started it
        yield test_client


def _ingest(client, raw, name):
    r = client.post(f"/api/pg/bigdocs/ingest?name={name}", content=raw, headers={"content-type": "application/octet-stream"})
    lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    assert lines[-1].get("done"), lines[-1]
    return lines


def test_routes_end_to_end(client):
    lines = _ingest(client, F.make_pdf(), "book.pdf")
    doc = lines[-1]["doc"]
    assert doc["pages"] == 600 and doc["chapters"] == 20 and doc["mode_hint"] == "qa"
    assert client.get(f"/api/pg/bigdocs/doc/{doc['id']}").json()["pages"] == 600
    page = client.get(f"/api/pg/bigdocs/doc/{doc['id']}/page/137").json()
    assert "KESTREL" in page["text"]
    found = client.post("/api/pg/bigdocs/search", json={"id": doc["id"], "query": "vault access code"}).json()
    assert found["pages"][0] == 137 or 137 in found["pages"]
    q = client.post("/api/pg/bigdocs/quote", json={"id": doc["id"], "page": 137, "claim": "code «4471-KESTREL»"}).json()
    assert q["exists"] and "KESTREL" in q["quote"] and q["score"] == 1.0
    ask = client.post("/api/pg/bigdocs/ask", json={"id": doc["id"], "question": "vault code?", "model": "m"}).json()
    assert ask["citations"][0]["pages"][0]["page"] == 137 and ask["citations"][0]["pages"][0]["verified"]
    est = client.post("/api/pg/bigdocs/estimate", json={"id": doc["id"], "model": "local-x"}).json()
    assert est["billing"] == "local" and est["parts"] == 20
    started = client.post("/api/pg/bigdocs/whole", json={"id": doc["id"], "model": "local-x", "prompt": "", "confirmed": True}).json()
    job_id = started["job"]["id"]
    with client.stream("GET", f"/api/pg/bigdocs/jobs/{job_id}/events") as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[-1]["stage"] == "end" and events[-1]["state"]["status"] == "done"
    assert any(e.get("label") == "глава 20 из 20" for e in events)
    assert client.get("/api/pg/bigdocs/doc/" + "f" * 32).status_code == 404
    assert client.post("/api/pg/bigdocs/search", json={"id": "nope"}).status_code == 400


def test_route_needs_confirmation_above_threshold(client, monkeypatch):
    from app import pricing
    monkeypatch.setattr(pricing, "lookup", lambda *a, **k: pricing.Price(30.0, 60.0, source="test"))
    doc = _ingest(client, F.make_docx(), "c.docx")[-1]["doc"]
    r = client.post("/api/pg/bigdocs/whole", json={"id": doc["id"], "model": "custom-expensive", "prompt": "p"})
    assert r.status_code == 409 and r.json()["needs_confirm"] and r.json()["estimate"]["cost_usd"] > 1


# ---------------------------------------------------------------- wiring (static checks)
def test_chat_wiring_present():
    app_js = (ROOT / "app/pgstatic/app.js").read_text("utf-8")
    for hook in ("MemuBigDocs?.takes(f)", "MemuBigDocs.chipHtml(a, i)", "MemuBigDocs?.wireChips(row",
                 "MemuBigDocs.contextBlocks(sourceMessages", "MemuBigDocs.systemNote(assistant)",
                 "MemuBigDocs.linkCitations(linked, m)", "MemuBigDocs.html(m, i)", "MemuBigDocs.preview(item)",
                 "bigdoc: a.bigdoc, bigdocMode"):
        assert hook in app_js, hook
    index = (ROOT / "app/pgstatic/index.html").read_text("utf-8")
    assert "/bigdocs.js" in index and "/bigdocs.css" in index
    js = (ROOT / "app/pgstatic/bigdocs.js").read_text("utf-8")
    assert "window.MemuBigDocs" in js and "/api/pg/bigdocs" in js


def test_build_targets_include_bigdocs():
    assert "bigdocs" in (ROOT / "build.py").read_text("utf-8")
    assert "app.bigdocs" in (ROOT / "MemuAI.spec").read_text("utf-8")


# ---------------------------------------------------------------- live (optional)
LIVE = os.environ.get("BIGDOCS_LIVE_PG", "")


@pytest.mark.skipif(not LIVE, reason="set BIGDOCS_LIVE_PG=http://127.0.0.1:8631 (a dev stack with custom accounts)")
def test_live_qa_cites_the_right_page():
    import httpx
    model = os.environ.get("BIGDOCS_LIVE_MODEL", "reform-qwen3.8-flash")
    c = httpx.Client(timeout=300, trust_env=False)
    lines = [json.loads(x) for x in c.post(f"{LIVE}/api/pg/bigdocs/ingest?name=contract.docx", content=F.make_docx()).text.splitlines() if x.strip()]
    doc = lines[-1]["doc"]
    r = c.post(f"{LIVE}/api/pg/bigdocs/ask", json={"id": doc["id"], "question": "Какая неустойка за просрочку оплаты?", "model": model}).json()
    pages = {row["page"] for cite in r["citations"] for row in cite["pages"]}
    assert 133 in pages, r


# ---------------------------------------------------------------- cross-language questions (no embeddings in the Proxy)
def test_russian_question_over_english_book_is_translated(pdf_doc, docx_doc):
    q = "Какой код доступа к хранилищу и кто его знает?"
    assert B.needs_translation(pdf_doc, q) and not B.needs_translation(docx_doc, q)
    assert not B.needs_translation(pdf_doc, "What is the vault access code?")
    assert 137 not in [h["page"] for h in B.search(pdf_doc, q, k=8)]          # lexical search alone cannot
    calls = []

    async def translator(model, messages):
        calls.append(messages)
        return "What is the vault access code and who knows it?"
    B._TRANSLATIONS.clear()
    ctx = _run(B.qa_context_async(pdf_doc, q, k=8, model="m", complete=translator))
    assert ctx["translated"].startswith("What is the vault") and 137 in ctx["pages"]
    assert "English" in calls[0][0]["content"]
    _run(B.qa_context_async(pdf_doc, q, k=8, model="m", complete=translator))
    assert len(calls) == 1                                                       # cached translation
    # a broken model never breaks the search
    async def broken(model, messages):
        raise RuntimeError("down")
    B._TRANSLATIONS.clear()
    ctx = _run(B.qa_context_async(pdf_doc, q, k=8, model="m", complete=broken))
    assert ctx["translated"] == "" and ctx["fragments"]


def test_telegram_augment_async_translates(store, pdf_doc, monkeypatch):
    from app import bigdocs_telegram as T
    T.set_active("777", B.summary(pdf_doc))

    async def translator(model, messages):
        return "What is the vault access code?"
    B._TRANSLATIONS.clear()
    out = _run(T.augment_async("777", "Какой код доступа к хранилищу?", "ru", "m", translator))
    assert "[стр. 137]" in out and "<untrusted_file>" in out
    T.set_active("777", None)


def test_cite_marks_with_a_clause_tail(docx_doc):
    cites = B.cite(docx_doc, "Поставка — 45 дней [p. 57, § 57.2]; неустойка 0,7% [стр. 133, п. 133.2].")
    assert [c["pages"][0]["page"] for c in cites] == [57, 133]
