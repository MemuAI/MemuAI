"""Local Team HTTP/SSE bridge. Shares the main window's origin/auth boundary."""
from __future__ import annotations

import asyncio
import codecs
import copy
import json
import time
import uuid
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import pgdata, teams
from .storage import load_json_with_recovery, mutate_json
from .team_runtime import TeamContextChanged, TeamRun, _short

MAX_BODY = 32 * 1024 * 1024
MAX_PENDING_RUNS = 8
SAVED_RUNS = 40

async def _body(request: Request, limit: int = MAX_BODY) -> dict:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("Запрос слишком большой / Request is too large")
    try:
        value = json.loads(data or b"{}")
    except (ValueError, TypeError) as exc:
        raise ValueError("Некорректный JSON / Invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Ожидается объект / Expected an object")
    return value

def _profile(expected: str = "") -> str:
    active = str(pgdata.load_profiles().get("active_id") or "default")
    if expected and expected != active:
        raise TeamContextChanged("Профиль MemuAI сменился. Откройте чат заново / MemuAI profile changed; reopen the chat")
    return active

def _save_run(profile_id: str, snapshot: dict):
    """Activity only: never save prompts, private memory, tool args or raw results."""
    path = pgdata.MEMU_DIR / pgdata._profile_data_name("team-runs.json", profile_id)
    def apply(raw):
        rows = raw if isinstance(raw, list) else []
        rows = [row for row in rows if isinstance(row, dict) and row.get("id") != snapshot["id"]]
        return True, (rows + [snapshot])[-SAVED_RUNS:]
    mutate_json(path, [], apply, backup=False, validator=lambda value: isinstance(value, list), repair_invalid=True)

@dataclass
class Live:
    profile_id: str
    chat_id: str
    run_id: str
    requests: dict[str, asyncio.Future] = field(default_factory=dict)
    runtime: TeamRun | None = None
    task: asyncio.Task | None = None
    reviews: set = field(default_factory=set)
    cancelled: bool = False

def register(app, *, proxy_sse=None, complete=None) -> None:
    """``complete`` injection keeps all orchestration tests offline and deterministic."""
    live: dict[str, Live] = {}
    app.state.team_runs = live

    async def completion(model: str, messages: list[dict], max_tokens: int) -> dict:
        if complete is not None:
            return await complete(model, messages, max_tokens)
        body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens,
                           "prompt_caching": True, "tools_available": True, "web_search": False, "thinking": False}, ensure_ascii=False).encode("utf-8")
        buffer = ""
        pieces = []
        usage = {}
        done = False
        total = 0
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        async for chunk in proxy_sse("/api/admin/playground/chat", body):

            buffer += decoder.decode(chunk) if isinstance(chunk, bytes) else str(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    done = True
                    continue
                try:
                    item = json.loads(raw)
                except ValueError:
                    continue
                if item.get("error"):
                    error = item["error"]
                    raise RuntimeError(_short(error.get("message") or error) if isinstance(error, dict) else _short(error))
                if isinstance(item.get("usage"), dict):
                    usage = item["usage"]
                choice = (item.get("choices") or [{}])[0]
                reason = choice.get("finish_reason")
                if reason in ("length", "content_filter", "error"):
                    raise RuntimeError("Ответ модели не завершён / Model response was truncated or blocked: " + reason)
                if reason:
                    done = True
                content = (choice.get("delta") or {}).get("content")
                if content:
                    text = str(content)
                    total += len(text)
                    if total > 160000:
                        raise RuntimeError("Ответ участника превысил лимит / Member output exceeded the limit")
                    pieces.append(text)
        if not done:
            raise RuntimeError("Ответ модели оборвался до завершения / The model response ended before completion")
        from .assistant_commands import clean_ai_text
        return {"text": clean_ai_text("".join(pieces)), "usage": usage}

    @app.get("/api/pg/teams")
    async def list_teams(profile_id: str = ""):
        try:
            active = _profile(profile_id)
            rows = await asyncio.to_thread(teams.load, active)
            _profile(active)
            return {"profile_id": active, "teams": rows, "presets": teams.presets(),
                    "limits": {"max_members": teams.MAX_MEMBERS, "max_teams": teams.MAX_TEAMS,
                               "default_budget": teams.DEFAULT_BUDGET}}
        except TeamContextChanged as exc:
            return JSONResponse({"error": str(exc)}, 409)

    @app.post("/api/pg/teams")
    async def save_team(request: Request):
        try:
            body = await _body(request, 512 * 1024)
            active = _profile(str(body.get("profile_id") or ""))
            value = body.get("team") if isinstance(body.get("team"), dict) else body
            team = await asyncio.to_thread(teams.save, value, active)
            _profile(active)
            return {"profile_id": active, "team": team}
        except TeamContextChanged as exc:
            return JSONResponse({"error": str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.delete("/api/pg/teams/{team_id}")
    async def delete_team(team_id: str, profile_id: str = ""):
        try:
            active = _profile(profile_id)
            await asyncio.to_thread(teams.delete, team_id, active)
            _profile(active)
            return {"ok": True}
        except TeamContextChanged as exc:
            return JSONResponse({"error": str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.get("/api/pg/teams/runs/{run_id}")
    async def get_run(run_id: str, profile_id: str = ""):
        try:
            active = _profile(profile_id)
            entry = live.get(run_id)
            if entry and entry.profile_id == active and entry.runtime:
                return {"run": entry.runtime.snapshot()}
            path = pgdata.MEMU_DIR / pgdata._profile_data_name("team-runs.json", active)
            rows = await asyncio.to_thread(load_json_with_recovery, path, [], validator=lambda value: isinstance(value, list))
            _profile(active)
            found = next((row for row in rows if isinstance(row, dict) and row.get("id") == run_id), None)
            if found:
                return {"run": found}
            return JSONResponse({"error": "Запуск не найден / Run not found"}, 404)
        except TeamContextChanged as exc:
            return JSONResponse({"error": str(exc)}, 409)

    @app.post("/api/pg/teams/runs/{run_id}/answer")
    async def tool_answer(run_id: str, request: Request):
        try:
            body = await _body(request, 12 * 1024 * 1024)
            active = _profile(str(body.get("profile_id") or ""))
            entry = live.get(run_id)
            if entry is None or entry.profile_id != active:
                return JSONResponse({"error": "Запуск больше не активен / Run is no longer active"}, 409)
            future = entry.requests.get(str(body.get("request_id") or ""))
            if future is None or future.done():
                return JSONResponse({"error": "Запрос уже неактуален / Request is no longer pending"}, 409)
            result = body.get("result")
            if not isinstance(result, dict):
                return JSONResponse({"error": "Нужен объект результата / Expected a result object"}, 400)
            future.set_result(copy.deepcopy(result))
            return {"ok": True}
        except TeamContextChanged as exc:
            return JSONResponse({"error": str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.post("/api/pg/teams/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request):
        try:
            body = await _body(request, 8192)
            active = _profile(str(body.get("profile_id") or ""))
            entry = live.get(run_id)
            if entry is None or entry.profile_id != active:
                return JSONResponse({"error": "Запуск больше не активен / Run is no longer active"}, 409)
            entry.cancelled = True
            if entry.task:
                entry.task.cancel()
            return {"ok": True}
        except TeamContextChanged as exc:
            return JSONResponse({"error": str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.post("/api/pg/teams/runs/{run_id}/review")
    async def review_approval(run_id: str, request: Request):
        entry = None
        current = asyncio.current_task()
        try:
            body = await _body(request, 24 * 1024)
            active = _profile(str(body.get("profile_id") or ""))
            entry = live.get(run_id)
            if entry is None or entry.profile_id != active or not entry.runtime or entry.runtime.status != "running":
                return JSONResponse({"error":"Запуск не активен / Run is no longer active"}, 409)
            entry.reviews.add(current)
            result = await entry.runtime.review_approval(str(body.get("member_id") or ""),
                str(body.get("kind") or ""), str(body.get("request_id") or ""),
                body.get("proposal") if isinstance(body.get("proposal"), dict) else {})
            _profile(active)
            return result
        except TeamContextChanged as exc:
            return JSONResponse({"error":str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({"error":str(exc)}, 400)
        finally:
            if entry:
                entry.reviews.discard(current)

    @app.post("/api/pg/teams/run")
    async def run_team(request: Request):
        try:
            body = await _body(request)
            active = _profile(str(body.get("profile_id") or ""))
            chat_id = str(body.get("chat_id") or "").strip()[:160]
            if not chat_id:
                raise ValueError("Нужен ID чата / Chat ID is required")
            rows = await asyncio.to_thread(teams.load, active)
            _profile(active)
            team = next((row for row in rows if row["id"] == str(body.get("team_id") or "")), None)
            if team is None and isinstance(body.get("team_snapshot"), dict):
                team = teams.normalize(body["team_snapshot"])
            if team is None:
                return JSONResponse({"error": "Команда удалена. Выберите другую / Team was deleted; choose another"}, 404)
            team = teams.normalize(team)
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("Нужно сообщение / A message is required")
            messages = [{"role": row["role"], "content": row.get("content", "")} for row in messages[-40:]
                        if isinstance(row, dict) and row.get("role") in ("user", "assistant")
                        and isinstance(row.get("content"), (str, list))]
            if not messages or not any(row["role"] == "user" and row["content"] for row in messages):
                raise ValueError("Нужно сообщение пользователя / A user message is required")
            if len(live) >= MAX_PENDING_RUNS:
                return JSONResponse({"error": "Слишком много команд работают одновременно / Too many teams are running"}, 429)
            if any(entry.profile_id == active and entry.chat_id == chat_id for entry in live.values()):
                return JSONResponse({"error": "Команда уже работает в этом чате / This team chat is already running"}, 409)
            run_id = "run_" + uuid.uuid4().hex
            entry = Live(active, chat_id, run_id)
            live[run_id] = entry
        except TeamContextChanged as exc:
            return JSONResponse({"error": str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

        async def stream():
            queue: asyncio.Queue = asyncio.Queue(maxsize=512)
            def guard():
                _profile(active)
                if entry.cancelled:
                    raise asyncio.CancelledError
            def emit(value):
                try:
                    queue.put_nowait(value)
                except asyncio.QueueFull:

                    raise RuntimeError("Окно не успевает получать события команды / Team event consumer is too slow")
            async def broker(actor, name, args):
                guard()
                request_id = "request_" + uuid.uuid4().hex
                future = asyncio.get_running_loop().create_future()
                entry.requests[request_id] = future
                emit({"event": "team_tool", "run_id": run_id, "request_id": request_id,
                      "member_id": actor, "tool": name, "args": args})
                try:
                    result = await future
                    guard()
                    return result
                finally:
                    entry.requests.pop(request_id, None)
            async def context_watch():
                while True:
                    await asyncio.sleep(.35)
                    try:
                        guard()
                    except TeamContextChanged:
                        if entry.task:
                            entry.task.cancel()
                        return
            manager_context = str(body.get("manager_context") or "")
            runtime = TeamRun(team, complete=completion, tool=broker, emit=emit, guard=guard,
                              messages=messages, manager_context=manager_context,
                              tools_prompt=str(body.get("tools_prompt") or ""),
                              available_models=[str(row)[:300] for row in body.get("available_models", [])[:100] if isinstance(row, str)] if isinstance(body.get("available_models"), list) else [],
                              run_id=run_id, mode="work" if body.get("mode") in ("work", "code") else "chat")
            entry.runtime = runtime
            async def worker():
                try:
                    result = await runtime.run()
                    guard()
                    emit({"event": "delta", "text": result["text"]})
                    emit({"event": "done", **result})
                except asyncio.CancelledError:
                    emit({"event": "cancelled", "run_id": run_id, "run": runtime.snapshot()})
                    raise
                except Exception as exc:
                    emit({"event": "error", "text": _short(exc, 1200), "run": runtime.snapshot()})
                finally:
                    for review in tuple(entry.reviews):
                        review.cancel()

                    try:
                        await asyncio.to_thread(_save_run, active, runtime.snapshot())
                    except Exception:
                        __import__("logging").getLogger("memuai.teams").exception("Could not save team run activity")
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        queue.get_nowait()
                        queue.put_nowait(None)
            watcher = None
            try:
                guard()
                entry.task = asyncio.create_task(worker())
                watcher = asyncio.create_task(context_watch())
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), 12)
                    except asyncio.TimeoutError:
                        guard()
                        yield ": keep-alive\n\n"
                        continue
                    if item is None:
                        break

                    if item.get("event") not in ("cancelled", "error"):
                        guard()
                    yield "data: " + json.dumps(item, ensure_ascii=False, default=str) + "\n\n"
                yield "data: [DONE]\n\n"
            finally:
                reviews = tuple(entry.reviews)
                for review in reviews:
                    review.cancel()
                if reviews:
                    await asyncio.gather(*reviews, return_exceptions=True)
                if watcher:
                    watcher.cancel()
                if entry.task and not entry.task.done():
                    entry.task.cancel()
                for future in entry.requests.values():
                    if not future.done():
                        future.cancel()
                if entry.task:
                    await asyncio.gather(entry.task, return_exceptions=True)
                if watcher:
                    await asyncio.gather(watcher, return_exceptions=True)
                live.pop(run_id, None)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
