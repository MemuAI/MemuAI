"""Bounded, profile-bound attachment staging for the Chrome workspace."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

MAX_FILE = 5 * 1024 * 1024
MAX_TURN = 12 * 1024 * 1024
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html", ".htm",
                   ".log", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".py", ".js", ".ts",
                   ".jsx", ".tsx", ".css", ".sql", ".sh", ".ps1", ".c", ".cpp", ".h",
                   ".rs", ".go", ".java", ".rb", ".php", ".tex", ".srt", ".vtt", ".pdf", ".docx"}

async def limited_json(request: Request, limit: int):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise HTTPException(413, "Вложение слишком большое (до 5 МБ на файл)")
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Некорректный JSON") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "Ожидается объект")
    return value

def prepare(name, data, extract_file):
    name = str(name or "file").replace("\\", "/").rsplit("/", 1)[-1][:200]
    if not isinstance(data, str) or not data.startswith("data:") or ";base64," not in data:
        raise ValueError("Файл должен содержать данные base64")
    try:
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
    except (ValueError, TypeError):
        raise ValueError("Повреждённые данные файла") from None
    if not raw or len(raw) > MAX_FILE:
        raise ValueError("Пустой файл или размер больше 5 МБ")
    from .media_cache import sniff_image
    mime = sniff_image(raw)
    item = {"name": name, "size": len(raw)}
    if mime:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Изображение слишком большое: максимум 40 мегапикселей")
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2048, 2048))
            out = io.BytesIO()
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                image.convert("RGBA").save(out, format="PNG", optimize=True)
                mime = "image/png"
            else:
                image.convert("RGB").save(out, format="JPEG", quality=90)
                mime = "image/jpeg"
            raw = out.getvalue()
        item.update(data=f"data:{mime};base64," + base64.b64encode(raw).decode(), isImg=True)
    else:
        if Path(name).suffix.lower() not in TEXT_EXTENSIONS:
            raise ValueError("Поддерживаются изображения, PDF, DOCX, текст и исходный код")
        if extract_file is None:
            raise ValueError("Чтение документов недоступно. Перезапустите MemuAI")
        result = extract_file(name, data)
        if isinstance(result, JSONResponse):
            raise ValueError(json.loads(result.body).get("error") or "Не удалось прочитать файл")
        text = str(result.get("text") or "")
        item.update(data=data, text=text[:100000], truncated=bool(result.get("truncated") or len(text) > 100000))
    return item

class Attachments:
    def __init__(self, extract_file):
        self.extract_file = extract_file
        self.staged = {}

    def prune(self):
        now = time.monotonic()
        self.staged = {key: row for key, row in self.staged.items() if row[0] > now}

        while self.staged and (len(self.staged) >= 32 or sum(len(row[2]["data"]) for row in self.staged.values()) > 48 * 1024 * 1024):
            self.staged.pop(next(iter(self.staged)))

    async def upload(self, request):
        from . import pgdata
        body = await limited_json(request, 8 * 1024 * 1024)
        profile = pgdata.load_profiles().get("active_id")
        if body.get("profile_id") and str(body["profile_id"]) != str(profile):
            return JSONResponse({"error": "Профиль MemuAI сменился. Обновите панель и прикрепите файл заново"}, 409)
        try:
            item = await asyncio.to_thread(prepare, body.get("name"), body.get("data"), self.extract_file)
        except Exception as exc:
            return JSONResponse({"error": str(exc)[:500]}, 400)
        if pgdata.load_profiles().get("active_id") != profile:
            return JSONResponse({"error": "Профиль MemuAI сменился во время чтения файла. Прикрепите его заново"}, 409)
        self.prune()
        token = uuid.uuid4().hex
        self.staged[token] = (time.monotonic() + 1800, profile, item)
        return {"id": token, "name": item["name"], "size": item["size"], "isImg": bool(item.get("isImg")),
                "preview": item["data"] if item.get("isImg") else "", "truncated": bool(item.get("truncated"))}

    def resolve(self, tokens, profile):
        self.prune()
        if not isinstance(tokens, list) or len(tokens) > 8:
            raise ValueError("Можно прикрепить до 8 файлов")
        result = []
        for token in dict.fromkeys(str(t) for t in tokens):
            row = self.staged.get(token)
            if row is None or row[1] != profile:
                raise ValueError("Вложение устарело или профиль сменился. Прикрепите файл заново")
            result.append(dict(row[2]))
        if sum(len(item["data"]) for item in result) > MAX_TURN:
            raise ValueError("Общий размер вложений слишком большой: прикрепите меньше файлов")
        if sum(bool(item.get("isImg")) for item in result) > 4:
            raise ValueError("Можно прикрепить до 4 изображений за сообщение")
        return result

def context(item):
    """Desktop chats use the same attachment dictionaries as this workspace."""
    text = str(item.get("text") or "")
    if not text:
        return ""
    return "\n\n<untrusted_file name=" + json.dumps(item.get("name"), ensure_ascii=False) + ">\n" + text[:100000] + (
        "\n[File excerpt truncated]" if item.get("truncated") or len(text) > 100000 else "") + "\n</untrusted_file>"

def history_image(value, profile_id):
    """Restore only bounded inline or active-profile cached desktop pictures."""
    if not isinstance(value, str):
        return None
    from . import media_cache, pgdata
    if not value.startswith("data:image/"):
        if not media_cache.is_local(value) or pgdata.load_profiles().get("active_id") != profile_id:
            return None
        path = media_cache.local_path(value)
        if path is None or path.stat().st_size > MAX_FILE:
            return None
        value = media_cache.local_data_url(value) or ""
    if len(value) > (MAX_FILE * 4 + 2) // 3 + 128 or ";base64," not in value:
        return None
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
    except (ValueError, TypeError):
        return None
    if not raw or len(raw) > MAX_FILE:
        return None
    mime = media_cache.sniff_image(raw)
    if not mime:
        return None
    return f"data:{mime};base64," + base64.b64encode(raw).decode()
