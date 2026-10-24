"""HTTP routes of big documents (Round 35, zone bigdocs; core: app/bigdocs.py).

Split out of ``app/bigdocs.py`` by the integrator: the core is reachable from MemuNote.exe
(``app.memunote.bigdocs_api``), which must not pull FastAPI in. The playground calls
``bigdocs_api.register(app, completion=…)`` in ``create_app``.
"""

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .bigdocs import (BigDocError, JOBS, MAX_FILE_BYTES, _BRIDGE, _prefs, _tr, answer_question, best_quote, cancel_job, cite, estimate, full_text, get_doc, ingest, ingest_path, job_events, qa_context_async, settings, start_job, summary)


def _decode_data_url(data_url: str) -> bytes:
    import base64
    import urllib.parse
    if "," not in data_url:
        raise BigDocError("Файл не содержит данных", "The file has no data", "empty")
    meta, encoded = data_url.split(",", 1)
    if len(encoded) > MAX_FILE_BYTES * 4 // 3 + 16:
        raise BigDocError("Файл больше 200 МБ", "The file is larger than 200 MB", "too_large")
    return base64.b64decode(encoded, validate=False) if ";base64" in meta else urllib.parse.unquote_to_bytes(encoded)


_STATUS = {"too_large": 413, "gone": 410, "bad_id": 400, "not_found": 404, "format": 415, "scan": 415, "empty": 415,
           "pypdf": 415, "parse": 415, "encrypted": 415}


def register(app: Any, completion: Optional[Callable[..., Awaitable[str]]] = None, prefix: str = "/api/pg/bigdocs") -> None:
    """Routes for the chat window, Quick Access and MemuNote (via the playground):

    * ``POST {prefix}/ingest?name=…`` — raw file bytes as the body (or JSON ``{name, data}`` / ``{path}``);
      streams NDJSON progress lines, the last one ``{"done": true, "doc": summary}``;
    * ``GET  {prefix}/doc/{id}`` — summary (404 → the client re-uploads);
    * ``GET  {prefix}/doc/{id}/page/{n}`` — one page's text (citation preview);
    * ``POST {prefix}/search`` ``{id, query, k, lang}`` → ``{fragments, block, instruction, pages}``;
    * ``POST {prefix}/cite`` ``{id, answer, pages?}`` → ``{citations}``;
    * ``POST {prefix}/full`` ``{id, lang}`` → whole text with page marks (small documents);
    * ``POST {prefix}/ask`` ``{id, question, model, lang}`` → answer + citations (server-side Q&A);
    * ``POST {prefix}/estimate`` ``{id, model, prompt}``; ``POST {prefix}/whole`` ``{id, model, prompt, confirmed}`` → job;
    * ``GET  {prefix}/jobs/{job}`` state; ``GET {prefix}/jobs/{job}/events?since=`` SSE; ``POST {prefix}/jobs/{job}/cancel``.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse, StreamingResponse

    def lang_of(body: Dict[str, Any]) -> str:
        return "en" if str(body.get("lang") or "") == "en" else "ru"

    def fail(exc: BigDocError, lang: str):
        return JSONResponse({"error": exc.text(lang), "code": exc.code}, _STATUS.get(exc.code, 400))

    async def body_json(request: Request) -> Dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def complete(model: str, messages: List[Dict[str, Any]]) -> str:
        if completion is None:
            raise RuntimeError("no completion bridge")
        return await completion(model, messages, prompt_caching=True)

    if completion is not None:
        _BRIDGE["complete"] = complete

    def remember_loop() -> None:
        try:
            _BRIDGE["loop"] = asyncio.get_running_loop()
        except RuntimeError:
            pass
    try:
        app.add_event_handler("startup", remember_loop)
    except Exception:
        pass

    @app.post(prefix + "/ingest")
    async def bigdocs_ingest(request: Request):
        remember_loop()
        lang = "en" if request.query_params.get("lang") == "en" else "ru"
        ctype = request.headers.get("content-type", "")
        name = request.query_params.get("name") or "file"
        raw: Optional[bytes] = None
        path = ""
        try:
            if "application/json" in ctype:
                body = await body_json(request)
                lang = lang_of(body) if body.get("lang") else lang
                name = str(body.get("name") or name)
                if body.get("path"):
                    path = str(body["path"])
                else:
                    raw = _decode_data_url(str(body.get("data") or ""))
            else:
                declared = int(request.headers.get("content-length") or 0)
                if declared > MAX_FILE_BYTES:
                    raise BigDocError("Файл больше 200 МБ", "The file is larger than 200 MB", "too_large")
                buf = bytearray()
                async for piece in request.stream():
                    buf.extend(piece)
                    if len(buf) > MAX_FILE_BYTES:
                        raise BigDocError("Файл больше 200 МБ", "The file is larger than 200 MB", "too_large")
                raw = bytes(buf)
        except BigDocError as exc:
            return fail(exc, lang)
        queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(event: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def work() -> Dict[str, Any]:
            if path:
                return ingest_path(path, progress)
            return ingest(raw or b"", name, progress)

        async def produce():
            task = asyncio.ensure_future(asyncio.to_thread(work))
            task.add_done_callback(lambda _t: loop.call_soon_threadsafe(queue.put_nowait, None))
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False) + "\n"
            try:
                doc = task.result()
                info = summary(doc)
                info["mode_hint"] = "qa" if doc["tokens"] > 60000 else "full"
                yield json.dumps({"done": True, "doc": info}, ensure_ascii=False) + "\n"
            except BigDocError as exc:
                yield json.dumps({"error": exc.text(lang), "code": exc.code}, ensure_ascii=False) + "\n"
            except Exception as exc:
                yield json.dumps({"error": str(exc)[:300], "code": "failed"}, ensure_ascii=False) + "\n"
        return StreamingResponse(produce(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})

    @app.get(prefix + "/doc/{doc_id}")
    async def bigdocs_doc(doc_id: str, lang: str = "ru"):
        try:
            doc = await asyncio.to_thread(get_doc, doc_id)
        except BigDocError as exc:
            return JSONResponse({"error": exc.text(lang), "code": exc.code}, 404 if exc.code == "gone" else 400)
        return summary(doc)

    @app.get(prefix + "/doc/{doc_id}/page/{n}")
    async def bigdocs_page(doc_id: str, n: int, lang: str = "ru"):
        try:
            doc = await asyncio.to_thread(get_doc, doc_id)
        except BigDocError as exc:
            return fail(exc, lang)
        if not (0 < n <= len(doc["pages"])):
            return JSONResponse({"error": _tr(lang, "Нет такой страницы", "No such page")}, 404)
        page = doc["pages"][n - 1]
        return {"n": n, "label": page.get("label", ""), "text": page.get("text", ""), "pages": len(doc["pages"]),
                "approx": bool(doc.get("approx_pages")), "name": doc.get("name", "")}

    @app.post(prefix + "/search")
    async def bigdocs_search(request: Request):
        body = await body_json(request)
        lang = lang_of(body)
        try:
            doc = await asyncio.to_thread(get_doc, str(body.get("id") or ""))
            k = int(body.get("k") or settings(_prefs())["k"])
            remember_loop()
            # «model» lets a question in another language than the document be translated for the search first
            return await qa_context_async(doc, str(body.get("query") or "")[:2000], min(20, max(2, k)), lang,
                                          model=str(body.get("model") or ""), complete=complete if completion else None)
        except BigDocError as exc:
            return fail(exc, lang)

    @app.post(prefix + "/cite")
    async def bigdocs_cite(request: Request):
        body = await body_json(request)
        lang = lang_of(body)
        try:
            doc = await asyncio.to_thread(get_doc, str(body.get("id") or ""))
        except BigDocError as exc:
            return fail(exc, lang)
        pages = body.get("pages") if isinstance(body.get("pages"), list) else None
        frags = [{"page": int(p)} for p in pages if str(p).isdigit()] if pages is not None else None
        return {"citations": await asyncio.to_thread(cite, doc, str(body.get("answer") or "")[:200000], frags)}

    @app.post(prefix + "/quote")
    async def bigdocs_quote(request: Request):
        body = await body_json(request)
        lang = lang_of(body)
        try:
            doc = await asyncio.to_thread(get_doc, str(body.get("id") or ""))
        except BigDocError as exc:
            return fail(exc, lang)
        try:
            page = int(body.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        exists = 0 < page <= len(doc["pages"])
        quote, score = best_quote(doc, page, str(body.get("claim") or "")[:2000]) if exists else ("", 0.0)
        return {"page": page, "exists": exists, "quote": quote, "score": score, "approx": bool(doc.get("approx_pages")),
                "label": doc["pages"][page - 1].get("label", "") if exists else ""}

    @app.post(prefix + "/full")
    async def bigdocs_full(request: Request):
        body = await body_json(request)
        lang = lang_of(body)
        try:
            doc = await asyncio.to_thread(get_doc, str(body.get("id") or ""))
        except BigDocError as exc:
            return fail(exc, lang)
        text = full_text(doc, True, lang)
        limit = int(body.get("limit") or 2_000_000)
        return {"text": text[:limit], "truncated": len(text) > limit, "tokens": doc.get("tokens", 0)}

    @app.post(prefix + "/ask")
    async def bigdocs_ask(request: Request):
        body = await body_json(request)
        lang = lang_of(body)
        try:
            doc = await asyncio.to_thread(get_doc, str(body.get("id") or ""))
        except BigDocError as exc:
            return fail(exc, lang)
        model = str(body.get("model") or "")
        if not model:
            return JSONResponse({"error": _tr(lang, "Не выбрана модель", "No model selected")}, 400)
        try:
            return await answer_question(doc, str(body.get("question") or "")[:2000], model, complete, lang=lang,
                                         k=settings(_prefs())["k"])
        except Exception as exc:
            return JSONResponse({"error": str(exc)[:400]}, 502)

    @app.post(prefix + "/estimate")
    async def bigdocs_estimate(request: Request):
        body = await body_json(request)
        lang = lang_of(body)
        try:
            doc = await asyncio.to_thread(get_doc, str(body.get("id") or ""))
        except BigDocError as exc:
            return fail(exc, lang)
        conf = settings(_prefs())
        return await asyncio.to_thread(estimate, doc, str(body.get("model") or ""), prompt=str(body.get("prompt") or "")[:4000],
                                       part_tokens=conf["part_tokens"], confirm_usd=conf["confirm_usd"], lang=lang)

    @app.post(prefix + "/whole")
    async def bigdocs_whole(request: Request):
        body = await body_json(request)
        lang = lang_of(body)
        try:
            doc = await asyncio.to_thread(get_doc, str(body.get("id") or ""))
        except BigDocError as exc:
            return fail(exc, lang)
        model = str(body.get("model") or "")
        if not model:
            return JSONResponse({"error": _tr(lang, "Не выбрана модель", "No model selected")}, 400)
        prompt = str(body.get("prompt") or "")[:4000]
        conf = settings(_prefs())
        est = await asyncio.to_thread(estimate, doc, model, prompt=prompt, part_tokens=conf["part_tokens"],
                                      confirm_usd=conf["confirm_usd"], lang=lang)
        if est["needs_confirm"] and not body.get("confirmed"):
            return JSONResponse({"needs_confirm": True, "estimate": est}, 409)
        job = start_job(doc, model, complete, prompt=prompt, lang=lang, part_tokens=conf["part_tokens"])
        return {"job": job.state(), "estimate": est}

    @app.get(prefix + "/jobs/{job_id}")
    async def bigdocs_job(job_id: str):
        job = JOBS.get(job_id)
        if job is None:
            return JSONResponse({"error": "unknown job", "code": "gone"}, 404)
        return job.state()

    @app.post(prefix + "/jobs/{job_id}/cancel")
    async def bigdocs_job_cancel(job_id: str):
        return {"ok": cancel_job(job_id)}

    @app.get(prefix + "/jobs/{job_id}/events")
    async def bigdocs_job_events(job_id: str, since: int = 0):
        job = JOBS.get(job_id)
        if job is None:
            return JSONResponse({"error": "unknown job", "code": "gone"}, 404)

        async def stream():
            async for index, event in job_events(job, since):
                payload = dict(event, i=index, done_parts=job.done, total=job.total, status=job.status)
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"stage": "end", "state": job.state()}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
