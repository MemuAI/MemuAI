"""Download a public video through yt-dlp, using the MemuUtils file workflow."""
from __future__ import annotations

import functools
import http.client
import ipaddress
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .core import MAX_FILE_BYTES, UtilError, human_size

_PROTOCOLS = {"http", "https", "m3u8", "m3u8_native", "http_dash_segments"}
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ".mpeg", ".mpg", ".ts", ".3gp", ".ogv"}
_LIMIT = ("Видео превышает 300 МБ.", "The video exceeds 300 MB.")
_TIMEOUT = ("Загрузка заняла слишком много времени.", "The download took too long.")


def _web_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 80, 443))
    except ValueError:
        return False


def _public_address(host: str) -> bool:
    ip = ipaddress.ip_address(host)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _connect(address, timeout, source_address=None):
    # Connect to the checked numeric address to avoid a second hostname lookup.
    addresses = socket.getaddrinfo(*address, type=socket.SOCK_STREAM)
    if not addresses or any(not _public_address(record[4][0]) for record in addresses):
        raise OSError("This address is not public")
    last_error = None
    for family, kind, protocol, _, target in addresses:
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
    raise last_error


def _downloader():
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import _urllib as network
    from yt_dlp.networking.exceptions import RequestError

    class PublicHTTP(network.HTTPHandler):
        @staticmethod
        def _connection(base, *args, **kwargs):
            conn = base(*args, **kwargs)
            conn._create_connection = _connect
            return conn

        def http_open(self, req):
            if not _web_url(req.full_url):
                raise urllib.error.URLError("Only public HTTP and HTTPS URLs are supported")
            return self.do_open(functools.partial(self._connection, http.client.HTTPConnection), req)

        def https_open(self, req):
            if not _web_url(req.full_url):
                raise urllib.error.URLError("Only public HTTP and HTTPS URLs are supported")
            return self.do_open(functools.partial(self._connection, http.client.HTTPSConnection),
                                req, context=self._context)

    class PublicRH(network.UrllibRH):
        _SUPPORTED_URL_SCHEMES = ("http", "https")

        def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
            opener = urllib.request.OpenerDirector()
            for handler in (
                PublicHTTP(context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support)),
                network.RedirectHandler(), urllib.request.HTTPCookieProcessor(cookiejar),
                urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(),
                urllib.request.UnknownHandler(),
            ):
                opener.add_handler(handler)
            return opener

        def _send(self, request):
            if not _web_url(request.url):
                raise RequestError("Only public HTTP and HTTPS URLs are supported")
            return super()._send(request)

    class PublicDownloader(YoutubeDL):
        def build_request_director(self, handlers, preferences=None):
            return super().build_request_director([PublicRH])

        def process_ie_result(self, ie_result, *args, **kwargs):
            if ie_result.get("_type") in ("playlist", "multi_video"):
                raise UtilError("Откройте отдельное видео и вставьте его ссылку.",
                                "Open an individual video and paste its link.")
            return super().process_ie_result(ie_result, *args, **kwargs)

    return PublicDownloader


def _runtimes():
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "video-runtime" / "qjs.exe"
    if getattr(sys, "frozen", False) and bundled.is_file():
        return {"quickjs": {"path": str(bundled)}}
    return {name: {"path": path} for name, exe in (("deno", "deno"), ("node", "node"), ("quickjs", "qjs"))
            if (path := shutil.which(exe))}


def download(ctx):
    from ..music.tools import ffmpeg_path

    url = ctx.need_text("Вставьте ссылку на видео.", "Paste a video link.").strip()
    if len(url) > 8192 or any(char.isspace() for char in url) or not _web_url(url):
        raise UtilError("Нужна одна публичная ссылка HTTP или HTTPS.", "Enter one public HTTP or HTTPS link.")
    try:
        cls = _downloader()
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise UtilError("Компонент загрузки видео yt-dlp отсутствует.",
                        "The yt-dlp video download component is missing.") from exc

    ffmpeg = ffmpeg_path()
    runtimes = _runtimes()
    seen = {}
    deadline = time.monotonic() + 15 * 60
    work = ctx.work / "video"
    work.mkdir()

    def check_time():
        if time.monotonic() > deadline:
            raise UtilError(*_TIMEOUT)

    def progress(status):
        check_time()
        name = status.get("filename")
        if name:
            seen[name] = max(seen.get(name, 0), status.get("downloaded_bytes") or 0)
            if sum(seen.values()) > MAX_FILE_BYTES:
                raise UtilError(*_LIMIT)

    def video_filter(info, *, incomplete):
        check_time()
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            return ctx.tr("Откройте готовое видео или запись трансляции.", "Open a finished video or stream replay.")
        return None

    height = ctx.o["height"]
    cap = "" if height == "best" else f"[height<=?{height}]"
    combined = f"b{cap}"
    selector = f"bv*{cap}+ba/{combined}" if ffmpeg else combined

    def formats(info):
        allowed = [item for item in info["formats"]
                   if item.get("protocol") in _PROTOCOLS and _web_url(item.get("url") or "")
                   and (item.get("filesize") or item.get("filesize_approx") or 0) <= MAX_FILE_BYTES]
        yield from select({**info, "formats": allowed})

    options = {
        "quiet": True, "noprogress": True, "noplaylist": True, "playlistend": 1, "cachedir": False,
        "proxy": "", "socket_timeout": 20, "retries": 1, "fragment_retries": 1,
        "skip_unavailable_fragments": False, "max_filesize": MAX_FILE_BYTES,
        "outtmpl": str(work / "video.%(ext)s"), "format": formats, "format_sort": ["res", "ext"],
        "js_runtimes": runtimes, "match_filter": video_filter, "progress_hooks": [progress],
        "external_downloader": "native",
        # A native HLS fallback may call ffmpeg. Keep its inputs local.
        "external_downloader_args": {"ffmpeg_i": ["-protocol_whitelist", "file,pipe"]},
        "postprocessor_args": {"ffmpeg_i": ["-protocol_whitelist", "file,pipe"]},
    }
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    try:
        with cls(options) as ydl:
            select = ydl.build_format_selector(selector)
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        detail = str(exc).removeprefix("ERROR: ").strip().partition("\n")[0][:250]
        detail = re.sub(r"https?://[^\s]+", "", detail).strip()
        raise UtilError(f"Не удалось скачать видео. {detail}", f"Couldn't download the video. {detail}") from exc

    candidates = [path for path in work.iterdir()
                  if path.stem == "video" and path.suffix.lower() in _VIDEO_SUFFIXES]
    if not info or len(candidates) != 1 or not candidates[0].stat().st_size:
        raise UtilError("Сайт не вернул доступный видеофайл до 300 МБ.",
                        "The site did not provide an available video file under 300 MB.")
    path = candidates[0]
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise UtilError(*_LIMIT)
    import av
    try:
        with av.open(str(path), options={"protocol_whitelist": "file"}) as container:
            if not container.streams.video:
                raise ValueError("No video stream")
    except (av.error.FFmpegError, ValueError) as exc:
        raise UtilError("По ссылке нет готового видеофайла.", "The link did not return a playable video file.") from exc
    title = str(info.get("title") or "video").strip()[:100] or "video"
    path.replace(ctx.out_path(title + path.suffix.lower()))
    shutil.rmtree(work)
    return {"note": ctx.tr("Видео загружено", "Video downloaded") + " · " + human_size(size, ctx.en)}
