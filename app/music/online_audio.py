from __future__ import annotations

import asyncio
import secrets
import threading
import time
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi.responses import Response, StreamingResponse

from . import sources

_streams = {}
_lock = threading.Lock()
_HOSTS = ("googlevideo.com", "sndcdn.com", "soundcloud.com")


def _allowed(url):
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        return p.scheme == "https" and not p.username and not p.password and p.port in (None, 443) and any(host == d or host.endswith('.' + d) for d in _HOSTS)
    except ValueError:
        return False


def resolve(url, base, route):
    from .downloader import Runner
    if sources.detect(url).get('source') not in ('youtube', 'ytmusic', 'soundcloud'):
        raise ValueError('Unsupported streaming source')
    info = Runner().json(url, flat=False, timeout=45)
    choices = [f for f in info.get('formats', []) if isinstance(f, dict) and f.get('acodec') not in (None, 'none') and f.get('vcodec') in (None, 'none') and _allowed(str(f.get('url') or '')) and str(f.get('protocol') or 'https') in ('http', 'https')]
    if not choices:
        raise ValueError('Audio stream unavailable / Аудиопоток недоступен')
    choices.sort(key=lambda f: (f.get('ext') in ('m4a', 'mp3', 'webm', 'opus'), float(f.get('abr') or 0)), reverse=True)
    chosen = choices[0]
    token = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for key in list(_streams):
            if _streams[key][1] <= now: del _streams[key]
        if len(_streams) >= 100:
            del _streams[next(iter(_streams))]
        _streams[token] = (chosen['url'], now + 3600)
    return {'url': f'{base}{route}/{token}', 'duration': float(info.get('duration') or 0)}


async def stream(token, request, cors=False):
    with _lock:
        entry = _streams.get(token)
    if not entry or entry[1] <= time.time():
        return Response(status_code=404)
    headers = {'User-Agent': 'Mozilla/5.0'}
    if request.headers.get('range'):
        headers['Range'] = request.headers['range']
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=60), follow_redirects=False)
    url = entry[0]
    try:
        for _ in range(5):
            if not _allowed(url): raise ValueError('Untrusted audio host')
            upstream = await client.send(client.build_request(request.method, url, headers=headers), stream=True)
            if upstream.status_code not in (301, 302, 303, 307, 308): break
            location = upstream.headers.get('location', '')
            await upstream.aclose()
            url = urljoin(url, location)
        else: raise ValueError('Too many redirects')
        if upstream.status_code not in (200, 206):
            await upstream.aclose(); await client.aclose()
            return Response(status_code=502)
        response_headers = {k: upstream.headers[k] for k in ('content-type', 'content-length', 'content-range', 'accept-ranges') if k in upstream.headers}
        if cors: response_headers['Access-Control-Allow-Origin'] = '*'
        if request.method == 'HEAD':
            await upstream.aclose(); await client.aclose()
            return Response(status_code=upstream.status_code, headers=response_headers)
        async def body():
            try:
                async for chunk in upstream.aiter_bytes(): yield chunk
            finally:
                await upstream.aclose(); await client.aclose()
        return StreamingResponse(body(), status_code=upstream.status_code, headers=response_headers)
    except Exception:
        await client.aclose()
        return Response(status_code=502)
