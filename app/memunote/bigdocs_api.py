"""MemuNote ↔ big documents (Round 35, zone bigdocs; core: app/bigdocs.py).

The AI panel of MemuNote saw a 60 000-character window of the open document (and nothing of a PDF). For a long
PDF / DOCX / EPUB opened from disk it now asks ``POST /api/bigdocs {op, …}``:

* ``ingest {path}`` → summary (pages, sections, tokens) — the whole file, cached by hash;
* ``search {id, query}`` → the fragments block + citation rule for «Спросить»;
* ``estimate {id, model, prompt}`` / ``whole {id, model, prompt, confirmed}`` → a map-reduce job on the
  background loop (the server is threaded), ``job {job}`` → state + recent events, ``cancel {job}``;
* ``quote {id, page, claim}`` / ``page {id, page}`` → hover quotes and the page text.

Model calls go to the MemuAI Proxy through :func:`app.memunote.ai.stream_chat` like the panel's own answers.
"""
import asyncio
import threading
from typing import Any, Dict, List

from .. import bigdocs

def _complete_factory(base: str):
    from . import ai

    async def complete(model: str, messages: List[Dict[str, Any]]) -> str:
        def run() -> str:
            parts: List[str] = []
            cancel = threading.Event()
            for event in ai.stream_chat(base, {"model": model, "messages": messages, "web_search": False, "thinking": False},
                                        cancel, lambda _c: None):
                if event.get("t") == "delta":
                    parts.append(str(event.get("text") or ""))
                elif event.get("t") == "error":
                    raise RuntimeError(str(event.get("message") or event.get("error") or "model error"))
            return "".join(parts).strip()
        return await asyncio.to_thread(run)
    return complete

def handle(body: Dict[str, Any], settings: Dict[str, Any], lang: str) -> Dict[str, Any]:
    op = str(body.get("op") or "")
    lang = "en" if lang == "en" else "ru"
    try:
        if op == "ingest":
            return {"ok": True, "doc": bigdocs.summary(bigdocs.ingest_path(str(body.get("path") or "")))}
        if op == "job":
            job = bigdocs.JOBS.get(str(body.get("job") or ""))
            if job is None:
                return {"ok": False, "error": "unknown job", "code": "gone"}
            since = max(0, int(body.get("since") or 0))
            return {"ok": True, "state": job.state(), "events": job.events[since:since + 200], "next": len(job.events)}
        if op == "cancel":
            return {"ok": bigdocs.cancel_job(str(body.get("job") or ""))}
        doc = bigdocs.get_doc(str(body.get("id") or ""))
        conf = bigdocs.settings(settings)
        if op == "search":
            query = str(body.get("query") or "")[:2000]
            model = str(body.get("model") or settings.get("ai_model") or "")
            alt = ""
            if model and bigdocs.needs_translation(doc, query):
                try:
                    from . import ai
                    complete = _complete_factory(ai.discover(settings.get("proxy_url", "")))
                    alt = bigdocs.run_sync(bigdocs.translate_query(doc, query, model, complete), 35) or ""
                except Exception:
                    alt = ""
            ctx = bigdocs.qa_context(doc, query, k=conf["k"], lang=lang, extra=[alt] if alt else None)
            return {"ok": True, **ctx, "translated": alt}
        if op == "page":
            n = int(body.get("page") or 0)
            if not 0 < n <= len(doc["pages"]):
                return {"ok": False, "error": "no such page"}
            return {"ok": True, "n": n, "text": doc["pages"][n - 1].get("text", ""), "pages": len(doc["pages"])}
        if op == "quote":
            n = int(body.get("page") or 0)
            exists = 0 < n <= len(doc["pages"])
            quote, score = bigdocs.best_quote(doc, n, str(body.get("claim") or "")[:2000]) if exists else ("", 0.0)
            return {"ok": True, "exists": exists, "quote": quote, "score": score, "approx": bool(doc.get("approx_pages"))}
        model = str(body.get("model") or settings.get("ai_model") or "")
        prompt = str(body.get("prompt") or "")[:4000]
        est = bigdocs.estimate(doc, model, prompt=prompt, part_tokens=conf["part_tokens"], confirm_usd=conf["confirm_usd"], lang=lang)
        if op == "estimate":
            return {"ok": True, "estimate": est}
        if op == "whole":
            if not model:
                return {"ok": False, "error": "no_model"}
            if est["needs_confirm"] and not body.get("confirmed"):
                return {"ok": False, "needs_confirm": True, "estimate": est}
            from . import ai
            complete = _complete_factory(ai.discover(settings.get("proxy_url", "")))

            async def start():
                return bigdocs.start_job(doc, model, complete, prompt=prompt, lang=lang, part_tokens=conf["part_tokens"])
            job = bigdocs.run_sync(start(), 10)
            return {"ok": True, "job": job.state(), "estimate": est}
        return {"ok": False, "error": "unknown op"}
    except bigdocs.BigDocError as exc:
        return {"ok": False, "error": exc.text(lang), "code": exc.code}
