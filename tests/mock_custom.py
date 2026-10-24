"""Мок произвольного OpenAI-совместимого API (для провайдера «Собственный API»)."""
from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
state = {"last_auth": "", "last_body": None}


def sse(events, delay: float = 0.0):
    async def gen():
        for ev in events:
            yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/models")
async def models(request: Request):
    state["last_auth"] = request.headers.get("authorization", "")
    return {"object": "list", "data": [
        {"id": "gpt-test", "object": "model"},
        {"id": "claude-test", "object": "model"},
    ]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    state["last_auth"] = request.headers.get("authorization", "")
    state["last_body"] = await request.json()
    body = state["last_body"]
    model = body.get("model", "?")
    text = f"[{model}] ответ мока"
    if not body.get("stream"):
        return {
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }
    return sse([
        {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"reasoning_content": "думаю…"}, "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": f"[{model}] "}, "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "ответ мока"}, "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ])


@app.get("/v1/anything")
async def anything(request: Request):
    return {"path": "/v1/anything", "auth": state["last_auth"],
            "custom_header": request.headers.get("x-custom", "")}


@app.post("/mock/reset")
async def reset():
    state.update({"last_auth": "", "last_body": None})
    return {"ok": True}
