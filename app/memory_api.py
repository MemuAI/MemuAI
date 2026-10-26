# -*- coding: utf-8 -*-
"""HTTP API of memory v2 on the MemuAI window server: ``/api/memory/*`` (Round 29).

Same localhost boundary as the rest of ``/api`` (Host + Origin check in playground_api).
Used by the Memory panel (``pgstatic/memory.js``), the chat pipeline of the window
(context + extraction) and by other MemuAI apps such as MemuNote::

    GET  /api/memory/search?service=note&q=…&limit=20     relevant facts for a service
    POST /api/memory/context  {service, query, budget_tokens?, temp?, name?, bio?}
    POST /api/memory/propose  {service, facts:[{fact, category?, confidence?, scope?, tags?}] | fact, source?}
    POST /api/memory/extract  {service, text, source?, temp?}   → cleaned text + what was stored

Everything else is for the panel: items CRUD, bulk actions, the pending inbox, rules,
import/export, «forget everything from this chat», settings and profile switching.

Round 40 (r40-memory): ``/api/memory/diary/*`` (session diary: edit, delete, «run now»), ``concepts`` in item
edits and proposals, and the background worker (:mod:`app.memory_worker`) kicked by ``/context``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import memory_v2 as mem
from . import memory_worker

def register(app, completion=None) -> None:
    memory_worker.configure(completion)

    def err(message: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": message}, status)

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            raw = await request.body()
            if len(raw) > 8 * 1024 * 1024:
                return {"__error__": "too large"}
            data = json.loads(raw or b"{}")
        except ValueError:
            return {"__error__": "invalid JSON"}
        return data if isinstance(data, dict) else {"__error__": "JSON object expected"}

    def profile_of(request: Request, body: Dict[str, Any] | None = None) -> str:
        value = str((body or {}).get("profile_id") or request.query_params.get("profile_id") or "")
        return value[:80]

    def service_of(value: Any, default: str = "") -> str:
        name = str(value or default).strip().lower()
        aliases = {"memunote": "note", "memucode": "code", "cli": "code", "miniapp": "telegram", "tg": "telegram"}
        return aliases.get(name, name)

    async def run(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs), None
        except KeyError:
            return None, err("not found", 404)
        except ValueError as exc:
            return None, err(str(exc) or "invalid value")
        except Exception as exc:   # pragma: no cover - disk errors
            return None, err(f"memory error: {type(exc).__name__}", 500)

    def doc_reply(out: Dict[str, Any], **extra) -> Dict[str, Any]:
        doc = out["doc"] if isinstance(out, dict) and "doc" in out else out
        reply = {"memory": mem.public(doc)}
        reply.update(extra)
        return reply

    @app.get("/api/memory")
    async def memory_doc(request: Request):
        doc, error = await run(mem.load, profile_of(request))
        if error:
            return error
        out = mem.public(doc)
        out["rule_matches"] = mem.matching_rule_count(doc)
        out["active_profile"] = mem.active_profile_id()
        out["worker"] = memory_worker.status()
        memory_worker.kick()
        return out

    @app.post("/api/memory/settings")
    async def memory_settings(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        out, error = await run(mem.set_settings, body, profile_id=profile_of(request, body))
        return error or doc_reply(out)

    @app.post("/api/memory/items")
    async def memory_add(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        text = str(body.get("text") or "").strip()
        if not text:
            return err("text")
        if mem.sensitivity(text)[0] == "hard":
            return err("secret")
        service = service_of(body.get("service"), "manual")
        if service not in mem.SOURCE_SERVICES:
            service = "manual"
        source = body.get("source") if isinstance(body.get("source"), dict) else None
        out, error = await run(mem.add_manual, text, category=str(body.get("category") or ""),
                               scope=body.get("scope"), tags=body.get("tags"), pinned=bool(body.get("pinned")),
                               profile_id=profile_of(request, body), service=service,
                               source=dict(source or {}, by="user") if source else None)
        return error or doc_reply(out, result=out["result"])

    @app.patch("/api/memory/items/{item_id}")
    async def memory_edit(item_id: str, request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        changes = {k: body[k] for k in ("text", "category", "scope", "tags", "pinned", "confidence", "status",
                                        "concepts") if k in body}
        out, error = await run(mem.update_item, item_id, changes, profile_id=profile_of(request, body))
        return error or doc_reply(out)

    @app.delete("/api/memory/items/{item_id}")
    async def memory_delete(item_id: str, request: Request):
        out, error = await run(mem.bulk, [item_id], "delete", profile_id=profile_of(request))
        return error or doc_reply(out)

    @app.post("/api/memory/items/{item_id}/approve")
    async def memory_approve(item_id: str, request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        changes = {k: body[k] for k in ("category", "scope", "tags", "pinned") if k in body}
        out, error = await run(mem.approve, item_id, text=str(body.get("text") or ""), changes=changes,
                               profile_id=profile_of(request, body), keep_both=bool(body.get("keep_both")))
        return error or doc_reply(out)

    @app.post("/api/memory/items/{item_id}/reject")
    async def memory_reject(item_id: str, request: Request):
        body = await body_of(request)
        out, error = await run(mem.reject, item_id, remember=body.get("remember", True) is not False,
                               profile_id=profile_of(request, body))
        return error or doc_reply(out)

    @app.post("/api/memory/bulk")
    async def memory_bulk(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        out, error = await run(mem.bulk, ids, str(body.get("action") or ""), body.get("value"),
                               profile_id=profile_of(request, body))
        return error or doc_reply(out, count=out["result"])

    @app.post("/api/memory/forget-chat")
    async def memory_forget_chat(request: Request):
        body = await body_of(request)
        out, error = await run(mem.forget_chat, str(body.get("chat_id") or ""), profile_id=profile_of(request, body))
        return error or doc_reply(out, count=out["result"])

    @app.post("/api/memory/rules")
    async def memory_rule_add(request: Request):
        body = await body_of(request)
        out, error = await run(mem.add_rule, str(body.get("text") or ""), profile_id=profile_of(request, body))
        if error:
            return error
        return doc_reply(out, rule=out["result"], rule_matches=mem.matching_rule_count(out["doc"]))

    @app.patch("/api/memory/rules/{rule_id}")
    async def memory_rule_edit(rule_id: str, request: Request):
        body = await body_of(request)
        out, error = await run(mem.update_rule, rule_id, body, profile_id=profile_of(request, body))
        if error:
            return error
        return doc_reply(out, rule_matches=mem.matching_rule_count(out["doc"]))

    @app.delete("/api/memory/rules/{rule_id}")
    async def memory_rule_delete(rule_id: str, request: Request):
        out, error = await run(mem.delete_rule, rule_id, profile_id=profile_of(request))
        if error:
            return error
        return doc_reply(out, rule_matches=mem.matching_rule_count(out["doc"]))

    @app.post("/api/memory/rules/preview")
    async def memory_rule_preview(request: Request):
        body = await body_of(request)
        return mem.rule_preview(str(body.get("text") or ""))

    @app.post("/api/memory/rules/apply")
    async def memory_rule_apply(request: Request):
        body = await body_of(request)
        out, error = await run(mem.apply_rules_to_existing, profile_of(request, body))
        return error or doc_reply(out, count=out["result"], rule_matches=0)

    @app.get("/api/memory/export")
    async def memory_export(request: Request):
        fmt = request.query_params.get("format") or "json"
        pid = profile_of(request)
        if fmt == "md":
            text, error = await run(mem.export_markdown, pid, request.query_params.get("lang") or "ru")
            return error or PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
        data, error = await run(mem.export_doc, pid, include_deleted=request.query_params.get("deleted") == "1")
        return error or data

    @app.post("/api/memory/import")
    async def memory_import(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        out, error = await run(mem.import_data, body.get("data"), replace=bool(body.get("replace")),
                               profile_id=profile_of(request, body))
        return error or doc_reply(out, result=out["result"])

    @app.get("/api/memory/search")
    async def memory_search(request: Request):
        q = request.query_params
        service = service_of(q.get("service"), "note")
        if service not in mem.SERVICES:
            return err("unknown service")
        try:
            limit = max(1, min(200, int(q.get("limit") or 20)))
        except ValueError:
            limit = 20
        facts, error = await run(mem.context_facts, service, str(q.get("q") or "")[:4000], budget_tokens=100000,
                                 limit=limit, profile_id=profile_of(request), note_use=False)
        return error or {"service": service, "facts": facts}

    @app.post("/api/memory/context")
    async def memory_context(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        service = service_of(body.get("service"), "chat")
        if service not in mem.SERVICES:
            return err("unknown service")
        temp = bool(body.get("temp"))
        if temp:
            return {"service": service, "facts": [], "block": "", "protocol": "", "notify": False}
        pid = profile_of(request, body)
        budget = body.get("budget_tokens")

        def build():
            doc = mem.load(pid)
            query = str(body.get("query") or "")[:8000]
            facts = mem.context_facts(service, query,
                                      budget_tokens=int(budget) if isinstance(budget, (int, float)) else None,
                                      profile_id=pid)
            diary = mem.context_diary(query, doc=doc) if mem.service_reads(doc, service) else []
            block = mem.profile_context(service, name=str(body.get("name") or ""), bio=str(body.get("bio") or ""),
                                        facts=facts, diary=diary) if body.get("block", True) is not False else ""
            protocol = mem.protocol_prompt(service, doc=doc) if body.get("protocol", True) is not False else ""
            return {"service": service, "facts": facts, "block": block, "protocol": protocol,
                    "enabled": doc["enabled"], "notify": doc["settings"]["notify"],
                    "reads": mem.service_reads(doc, service), "writes": mem.service_writes(doc, service)}
        out, error = await run(build)
        memory_worker.kick()
        return error or out

    @app.post("/api/memory/propose")
    async def memory_propose(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        service = service_of(body.get("service"))
        if service not in mem.SERVICES:
            return err("unknown service")
        rows = body.get("facts") if isinstance(body.get("facts"), list) else []
        if body.get("fact"):
            rows = rows + [{k: body.get(k) for k in ("fact", "category", "confidence", "scope", "tags", "op", "id",
                                                     "concepts")}]
        proposals = []
        for row in rows[:30]:
            if isinstance(row, str):
                row = {"fact": row}
            if isinstance(row, dict):
                proposals.append({"op": str(row.get("op") or "add"), "fact": row.get("fact") or row.get("text"),
                                  "id": row.get("id"), "category": row.get("category"),
                                  "confidence": row.get("confidence"), "scope": row.get("scope"),
                                  "tags": row.get("tags"), "concepts": row.get("concepts")})
        by = "user" if body.get("by") == "user" else "ai"
        out, error = await run(mem.propose, proposals, service=service,
                               source=body.get("source") if isinstance(body.get("source"), dict) else None,
                               profile_id=profile_of(request, body), temp=bool(body.get("temp")), by=by)
        return error or out

    @app.post("/api/memory/extract")
    async def memory_extract(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        service = service_of(body.get("service"), "chat")
        if service not in mem.SERVICES:
            return err("unknown service")
        out, error = await run(mem.process_answer, str(body.get("text") or ""), service=service,
                               source=body.get("source") if isinstance(body.get("source"), dict) else None,
                               profile_id=profile_of(request, body), temp=bool(body.get("temp")),
                               allowed=body.get("allowed", True) is not False)
        if error:
            return error
        clean, result = out
        result["text"] = clean
        return result

    @app.patch("/api/memory/diary/{entry_id}")
    async def memory_diary_edit(entry_id: str, request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        out, error = await run(mem.edit_diary, entry_id, str(body.get("text") or ""), profile_id=profile_of(request, body))
        return error or doc_reply(out)

    @app.delete("/api/memory/diary/{entry_id}")
    async def memory_diary_delete(entry_id: str, request: Request):
        out, error = await run(mem.delete_diary, [entry_id], profile_id=profile_of(request))
        return error or doc_reply(out, count=out["result"])

    @app.post("/api/memory/worker/run")
    async def memory_worker_run(request: Request):
        """«Обновить сейчас»: concepts and the diary without waiting for the next message."""
        started = memory_worker.kick(force=True)
        return {"started": started, **memory_worker.status()}

    @app.get("/api/memory/worker")
    async def memory_worker_status(request: Request):
        return memory_worker.status()

    @app.post("/api/memory/switch-profile")
    async def memory_switch(request: Request):
        body = await body_of(request)
        doc, error = await run(mem.switch_profile, str(body.get("from") or ""), str(body.get("to") or ""),
                               body.get("snapshot"))
        return error or {"memory": mem.public(doc)}

    @app.post("/api/memory/legacy")
    async def memory_legacy(request: Request):
        body = await body_of(request)
        if "__error__" in body:
            return err(body["__error__"])
        if "enabled" in body and not isinstance(body["enabled"], bool):
            return err("enabled")
        if "facts" in body and not isinstance(body["facts"], list):
            return err("facts")
        doc, error = await run(mem.save_legacy, body, profile_of(request, body))
        return error or {"ok": True, "memory": mem.public(doc)}
