"""Round 36 (perf-core-2): cheap answers for the window's few-second polls.

``/api/pg/state`` and ``/api/pg/hub`` are asked every ~3 s and almost always
return the same bytes.  Each answer now carries an ETag; a caller that sends
it back (``If-None-Match`` or ``?since=``) gets ``304`` / ``{"unchanged": true}``
with no body to download, parse and compare.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi.responses import JSONResponse, Response

def etag_of(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.blake2b(raw.encode("utf-8", "surrogatepass"), digest_size=8).hexdigest()

def answer(data: Any, status: int = 200, since: str = "", if_none_match: str = "") -> Response:
    """The JSON answer, or a bodiless "not changed" when the caller already has these bytes."""
    if status != 200 or not isinstance(data, dict):
        return JSONResponse(data, status_code=status)
    tag = etag_of(data)
    headers = {"ETag": f'"{tag}"', "Cache-Control": "no-cache"}
    if if_none_match and tag in {part.strip().removeprefix("W/").strip('"') for part in if_none_match.split(",")}:
        return Response(status_code=304, headers=headers)
    if since and since == tag:
        return JSONResponse({"unchanged": True, "etag": tag}, headers=headers)
    return JSONResponse({**data, "etag": tag}, headers=headers)
