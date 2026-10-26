"""Round 29 (r29b-media-routing): an offline OpenAI-compatible media API for tests.

* ``POST /v1/images/generations`` — a generated PNG as ``b64_json`` (prompt containing ``FAIL`` → HTTP 400);
* ``POST /v1/chat/completions``  — ``*-image*`` models answer with ``message.images`` (OpenRouter style), other
  models with text (streaming or not);
* ``POST /v1/videos`` → ``GET /v1/videos/{id}`` (queued → in_progress → completed) → ``GET /v1/videos/{id}/content``;
* Google AI Studio shape: ``/v1beta/models/{m}:predictLongRunning`` + ``/v1beta/operations/{id}`` + file download,
  ``/v1beta/models/{m}:generateContent`` with inline image data.
``state`` records the last bodies so tests can assert what MemuAI sent.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI()
state = {"images": [], "chats": [], "videos": {}, "video_bodies": [], "auth": [], "delay": 0.4, "polls": 2}

def png_bytes(color=(90, 140, 230)) -> bytes:
    try:
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (256, 256), color)
        draw = ImageDraw.Draw(image)
        draw.ellipse((64, 64, 192, 192), fill=(250, 210, 80))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

_VIDEO = {}

def video_bytes() -> bytes:
    """A real 2-second WebM (VP8) when ffmpeg exists — headless Chromium can play it; else a stub with the EBML magic."""
    if "raw" in _VIDEO:
        return _VIDEO["raw"]
    raw = b"\x1aE\xdf\xa3" + b"\x00" * 2048
    if shutil.which("ffmpeg"):
        target = Path(tempfile.mkdtemp(prefix="mm-video-")) / "clip.webm"
        try:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=320x180:rate=12:duration=2", "-c:v", "libvpx", "-b:v", "200k", str(target)],
                           check=True, timeout=60)
            raw = target.read_bytes()
        except Exception:
            pass
    _VIDEO["raw"] = raw
    return raw

@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": x, "object": "model"} for x in
                                       ("gpt-image-1", "flux-pro", "gemini-3-flash-image", "sora-2", "chat-small")]}

@app.post("/v1/images/generations")
async def images(request: Request):
    body = await request.json()
    state["images"].append(body)
    state["auth"].append(request.headers.get("authorization", ""))
    await asyncio.sleep(state["delay"])
    if "FAIL" in str(body.get("prompt", "")):
        return JSONResponse({"error": {"message": "Your request was rejected by the safety system."}}, 400)
    return {"created": int(time.time()), "data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}

@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    state["chats"].append(body)
    model = str(body.get("model") or "")
    if "image" in model:
        await asyncio.sleep(state["delay"])
        url = "data:image/png;base64," + base64.b64encode(png_bytes((200, 90, 120))).decode()
        return {"id": "c1", "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "Here is your image.",
                    "images": [{"type": "image_url", "image_url": {"url": url}}]}}]}

    text = str(state.get("chat_reply") or "text answer")
    if not body.get("stream"):
        return {"id": "c2", "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}

    async def gen():
        for part in ([text[:len(text) // 2], text[len(text) // 2:]] if state.get("chat_reply") else ("text ", "answer")):
            yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": part}}]}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.post("/v1/videos")
async def create_video(request: Request):
    body = await request.json()
    state["video_bodies"].append(body)
    if "FAIL" in str(body.get("prompt", "")):
        job = {"id": "vid_" + uuid.uuid4().hex[:8], "status": "queued", "fail": True, "polls": 0}
    else:
        job = {"id": "vid_" + uuid.uuid4().hex[:8], "status": "queued", "polls": 0}
    state["videos"][job["id"]] = job
    return {"id": job["id"], "object": "video", "status": "queued", "model": body.get("model")}

@app.get("/v1/videos/{job_id}")
async def poll_video(job_id: str):
    job = state["videos"].get(job_id)
    if not job:
        return JSONResponse({"error": {"message": "not found"}}, 404)
    job["polls"] += 1
    if job["polls"] >= state["polls"]:
        job["status"] = "failed" if job.get("fail") else "completed"
    else:
        job["status"] = "in_progress"
    out = {"id": job_id, "status": job["status"], "progress": min(100, job["polls"] * 50)}
    if job["status"] == "failed":
        out["error"] = {"message": "Content policy violation"}
    return out

@app.get("/v1/videos/{job_id}/content")
async def video_content(job_id: str):
    if job_id not in state["videos"]:
        return JSONResponse({"error": {"message": "not found"}}, 404)
    return Response(video_bytes(), media_type="video/webm")

@app.post("/v1beta/models/{name}:generateContent")
async def gemini_image(name: str, request: Request):
    body = await request.json()
    state["chats"].append({"gemini": name, **body, "key": request.headers.get("x-goog-api-key", "")})
    return {"candidates": [{"content": {"parts": [
        {"text": "Here it is"},
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png_bytes((40, 160, 90))).decode()}}]}}]}

@app.post("/v1beta/models/{name}:predictLongRunning")
async def veo_start(name: str, request: Request):
    body = await request.json()
    state["video_bodies"].append({"veo": name, **body, "key": request.headers.get("x-goog-api-key", "")})
    op = "operations/op-" + uuid.uuid4().hex[:8]
    state["videos"][op] = {"polls": 0}
    return {"name": op}

@app.get("/v1beta/operations/{op}")
async def veo_poll(op: str, request: Request):
    job = state["videos"].get("operations/" + op)
    if not job:
        return JSONResponse({"error": {"message": "not found"}}, 404)
    job["polls"] += 1
    if job["polls"] < state["polls"]:
        return {"name": "operations/" + op, "done": False}
    base = str(request.base_url).rstrip("/")
    return {"name": "operations/" + op, "done": True, "response": {"generateVideoResponse": {"generatedSamples": [
        {"video": {"uri": f"{base}/v1beta/files/{op}:download?alt=media"}}]}}}

@app.get("/v1beta/files/{name}")
async def veo_file(name: str, request: Request):
    if not request.headers.get("x-goog-api-key"):
        return JSONResponse({"error": {"message": "key required"}}, 403)
    return Response(video_bytes(), media_type="video/webm")
