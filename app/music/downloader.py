# -*- coding: utf-8 -*-
"""MemuMusic job queue: downloads (any link / search text) and local folder imports.

One worker thread runs the jobs in order; a job with many items (album, playlist) reports
``progress`` 0..1 across all of them, can be cancelled (the yt-dlp child is killed) and retried
(only the failed items run again). Every item is deduplicated against the library first.
"""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import sources, tools
from .library import AUDIO_EXTS, Library, folder_cover, get_library, norm_text, read_tags

FORMATS = {
    "best": ("bestaudio[ext=m4a]/bestaudio/best", "bestaudio/best"),
    "normal": ("bestaudio[ext=m4a][abr<=160]/bestaudio[abr<=160]/bestaudio/best", "bestaudio[abr<=160]/bestaudio/best"),
    "small": ("bestaudio[ext=m4a][abr<=96]/worstaudio[ext=m4a]/worstaudio/worst", "bestaudio[abr<=96]/worstaudio/worst"),
}
_FIELDS = "id,title,track,artist,creator,uploader,channel,album,duration,thumbnail,webpage_url,abr,ext,extractor_key,filepath,release_year,track_number"
_PROG = "MEMUPROG"
_JSON = "MEMUJSON"
MAX_JOBS_KEPT = 60

class Cancelled(Exception):
    pass

def format_for(quality: str, prefer_m4a: bool = True) -> str:
    pair = FORMATS.get(quality or "best", FORMATS["best"])
    return pair[0] if prefer_m4a else pair[1]

def source_key(url: str, info: Optional[Dict[str, Any]] = None) -> str:
    """A stable id of the ORIGINAL item for dedup («yt:<id>», «sc:<path>», else the clean URL)."""
    info = info or sources.detect(url)
    if info.get("source") in ("youtube", "ytmusic") and info.get("id") and info.get("kind") == "track":
        return "yt:" + info["id"]
    clean = re.sub(r"[?#].*$", "", str(url or "")).rstrip("/").lower()
    clean = re.sub(r"^https?://(www\.|m\.)?", "", clean)
    return f"{info.get('source') or 'web'}:{clean}"

def split_title(title: str, uploader: str) -> Dict[str, str]:
    """«Artist - Title (Official Video)» → artist/title; «Name - Topic» channels are the artist."""
    artist = re.sub(r"\s*-\s*Topic$", "", uploader or "").strip()
    clean = re.sub(r"\s*[\(\[](official|official video|official audio|official music video|lyrics?|lyric video|audio|"
                   r"video|clip|клип|hd|hq|4k|visualizer|mv)[^\)\]]*[\)\]]", "", title or "", flags=re.I).strip()
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", clean)
    if not m or re.search(r"-\s*Topic$", uploader or ""):
        return {"artist": artist, "title": clean or title}
    return {"artist": m.group(1).strip(), "title": m.group(2).strip()}

class Runner:
    """Thin wrapper over the yt-dlp binary or python module (same results either way)."""

    def __init__(self) -> None:
        tools.ensure_ytdlp()
        self.binary = tools.ytdlp_binary()
        self.module = None if self.binary else tools.python_module()
        self.ffmpeg = tools.ffmpeg_path()

    def json(self, target: str, flat: bool = True, limit: int = 0, timeout: int = 90) -> Dict[str, Any]:
        if self.binary:
            cmd = [self.binary, "--no-warnings", "--encoding", "utf-8", "-J", "--ignore-errors"]
            if flat:
                cmd.append("--flat-playlist")
            if limit:
                cmd += ["--playlist-end", str(limit)]
            cmd.append(target)
            out = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True, encoding="utf-8",
                                 errors="replace", **tools.popen_kwargs())
            if not out.stdout.strip():
                raise RuntimeError(_last_error(out.stderr) or "yt-dlp: пустой ответ / empty answer")
            return json.loads(out.stdout)
        opts = {"quiet": True, "no_warnings": True, "ignoreerrors": True, "skip_download": True}
        if flat:
            opts["extract_flat"] = "in_playlist"
        if limit:
            opts["playlistend"] = limit
        with self.module.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
            return ydl.sanitize_info(info) or {}

    def download(self, url: str, fmt: str, outdir: Path, embed: bool, on_progress: Callable[[float], None],
                 cancel: threading.Event, holder: Dict[str, Any], cookies_browser: str = "") -> Dict[str, Any]:
        template = str(outdir / "%(artist,creator,uploader|Unknown).60B - %(track,title).90B [%(id)s].%(ext)s")
        if self.binary:
            cmd = [self.binary, "--no-warnings", "--encoding", "utf-8", "--no-playlist", "--newline", "--progress",
                   "--no-simulate", "--windows-filenames", "--no-mtime", "--retries", "5", "--fragment-retries", "5",
                   "-f", fmt, "-o", template,
                   "--progress-template", f"download:{_PROG} %(progress.downloaded_bytes)s %(progress.total_bytes)s "
                                          f"%(progress.total_bytes_estimate)s",
                   "--print", f"after_move:{_JSON} %(.{{{_FIELDS}}})j"]
            if self.ffmpeg:
                cmd += ["--ffmpeg-location", self.ffmpeg]
                if embed:
                    cmd.append("--embed-metadata")
            if cookies_browser:
                cmd += ["--cookies-from-browser", cookies_browser]
            cmd.append(url)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                    errors="replace", bufsize=1, **tools.popen_kwargs())
            holder["proc"] = proc
            errors: List[str] = []
            reader = threading.Thread(target=lambda: errors.extend(proc.stderr.readlines()), daemon=True)
            reader.start()
            result: Dict[str, Any] = {}
            for line in proc.stdout:
                if cancel.is_set():
                    break
                line = line.strip()
                if line.startswith(_PROG):
                    nums = [float(x) if x.replace(".", "", 1).isdigit() else 0.0 for x in line.split()[1:4]]
                    total = nums[1] or nums[2] if len(nums) >= 3 else 0
                    if total:
                        on_progress(min(1.0, nums[0] / total))
                elif line.startswith(_JSON):
                    try:
                        result = json.loads(line[len(_JSON):].strip())
                    except ValueError:
                        pass
            if cancel.is_set():
                _kill(proc)
                raise Cancelled()
            proc.wait(timeout=60)
            reader.join(timeout=5)
            holder.pop("proc", None)
            if proc.returncode != 0 or not result.get("filepath"):
                raise RuntimeError(_last_error("".join(errors)) or f"yt-dlp exit {proc.returncode}")
            return result

        def hook(data: Dict[str, Any]) -> None:
            if cancel.is_set():
                raise Cancelled()
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            if data.get("status") == "downloading" and total:
                on_progress(min(1.0, (data.get("downloaded_bytes") or 0) / total))

        opts: Dict[str, Any] = {"format": fmt, "outtmpl": template, "noplaylist": True, "quiet": True, "no_warnings": True,
                                "windowsfilenames": True, "updatetime": False, "retries": 5, "progress_hooks": [hook]}
        if self.ffmpeg:
            opts["ffmpeg_location"] = self.ffmpeg
            if embed:
                opts["postprocessors"] = [{"key": "FFmpegMetadata"}]
        if cookies_browser:
            opts["cookiesfrombrowser"] = (cookies_browser,)
        try:
            with self.module.YoutubeDL(opts) as ydl:
                info = ydl.sanitize_info(ydl.extract_info(url, download=True)) or {}
        except Exception:
            if cancel.is_set():
                raise Cancelled()
            raise
        downloads = info.get("requested_downloads") or [{}]
        info["filepath"] = downloads[0].get("filepath") or info.get("filepath")
        return {k: info.get(k) for k in _FIELDS.split(",")}

def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:
        pass

def _last_error(stderr: str) -> str:
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    errs = [l for l in lines if l.startswith("ERROR")]
    return (errs or lines or [""])[-1][:400]

def friendly_error(text: str) -> str:
    """yt-dlp's most common failures in plain words (ru / en), the original line kept at the end."""
    low = (text or "").lower()
    if "confirm you" in low and "bot" in low or "sign in to confirm" in low:
        hint = ("YouTube просит подтвердить, что вы не бот. Выберите браузер, где вы вошли в YouTube, в настройках "
                "MemuMusic («Cookies из браузера»), и/или обновите загрузчик (yt-dlp); помогает и отключение VPN / "
                "YouTube asks to confirm you are not a bot. Pick the browser you are signed in to YouTube with in "
                "MemuMusic settings («Cookies from browser») and/or update the downloader (yt-dlp); turning a VPN off helps too")
    elif "video unavailable" in low or "private video" in low or "404" in low:
        hint = "Трек недоступен или удалён / The track is unavailable or removed"
    elif any(word in low for word in ("login required", "login is required", "sign in", "authentication", "403 forbidden", "http error 401")):
        hint = ("Сайт ограничивает доступ: проверьте ссылку и доступ к публикации; при необходимости выберите cookies браузера в настройках / "
                "The site restricts access: check the link and post visibility; select browser cookies in settings if needed")
    elif "unsupported url" in low:
        hint = "Этот сайт не поддерживается / This site is not supported"
    elif "requested format is not available" in low:
        hint = "Нет аудиодорожки нужного качества / No audio in the requested quality"
    else:
        return text
    return f"{hint} — {text[:160]}"

_ITEM_PUBLIC = ("title", "artist", "status", "error", "error_code", "track_id", "progress", "duration", "via")
_ITEM_SAVED = ("title", "artist", "album", "duration", "url", "query", "status", "error", "error_code", "track_id",
               "meta_source", "source_key", "source_url", "cover_url", "track_no", "year", "path", "via", "orig_url")
INTERRUPTED = "Прервано перезапуском — нажмите «Повторить» / Interrupted by a restart — press Retry"

class Job:
    def __init__(self, url: str, kind: str = "download", quality: str = "", info: Optional[Dict[str, Any]] = None):
        self.job_id = uuid.uuid4().hex[:10]
        self.url = url
        self.kind = kind
        self.quality = quality
        self.info = info or {}
        self.source = self.info.get("source", "local" if kind == "import" else "")
        self.title = self.info.get("title") or url
        self.status = "queued"
        self.progress = 0.0
        self.error = ""
        self.error_code = ""
        self.interrupted = False
        self.track_ids: List[str] = []
        self.items: List[Dict[str, Any]] = []
        self.current = ""
        self.created_at = time.time()
        self.finished_at = 0.0
        self.cancel = threading.Event()
        self.holder: Dict[str, Any] = {}
        self.holders: List[Dict[str, Any]] = []
        self.fracs: Dict[int, float] = {}
        self.lock = threading.Lock()

    def public(self) -> Dict[str, Any]:
        done = sum(1 for i in self.items if i.get("status") in ("done", "exists"))
        failed = sum(1 for i in self.items if i.get("status") == "error")
        running = [i for i in self.items if i.get("status") == "running"]
        current = self.current
        if len(running) > 1:
            current = " · ".join(" — ".join(x for x in (i.get("artist"), i.get("title")) if x) or i.get("url", "")
                                 for i in running[:3])
        return {"job_id": self.job_id, "url": self.url, "kind": self.kind, "source": self.source,
                "source_label": sources.LABELS.get(self.source, self.source), "item_kind": self.info.get("kind", ""),
                "title": self.title, "progress": round(self.progress, 4), "status": self.status, "error": self.error,
                "error_code": self.error_code, "interrupted": self.interrupted,
                "track_ids": list(self.track_ids), "current": current, "total": len(self.items),
                "done": done, "failed": failed, "running": len(running), "quality": self.quality,
                "created_at": self.created_at, "finished_at": self.finished_at,
                "items": [{k: i.get(k, "" if k not in ("progress", "duration") else 0) for k in _ITEM_PUBLIC}
                          for i in self.items[:500]]}

    def to_dict(self) -> Dict[str, Any]:
        return {"job_id": self.job_id, "url": self.url, "kind": self.kind, "quality": self.quality,
                "info": {k: v for k, v in self.info.items() if isinstance(v, (str, int, float, bool))},
                "source": self.source, "title": self.title, "status": self.status, "progress": self.progress,
                "error": self.error, "error_code": self.error_code, "interrupted": self.interrupted,
                "track_ids": self.track_ids, "created_at": self.created_at, "finished_at": self.finished_at,
                "items": [{k: i[k] for k in _ITEM_SAVED if k in i} for i in self.items[:5000]]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        job = cls(str(data.get("url") or ""), str(data.get("kind") or "download"), str(data.get("quality") or ""),
                  dict(data.get("info") or {}))
        job.job_id = str(data.get("job_id") or job.job_id)
        for key in ("source", "title", "status", "error", "error_code"):
            if data.get(key) is not None:
                setattr(job, key, str(data[key]))
        job.progress = float(data.get("progress") or 0)
        job.interrupted = bool(data.get("interrupted"))
        job.track_ids = [str(x) for x in data.get("track_ids") or []]
        job.created_at = float(data.get("created_at") or time.time())
        job.finished_at = float(data.get("finished_at") or 0)
        job.items = [dict(i) for i in data.get("items") or [] if isinstance(i, dict)]
        if job.status in ("queued", "running"):

            job.status, job.error, job.error_code, job.interrupted = "error", INTERRUPTED, "interrupted", True
            job.finished_at = job.finished_at or time.time()
            for item in job.items:
                if item.get("status") in ("running", "queued"):
                    item["status"] = "pending"
        return job

def error_code(text: str) -> str:
    low = (text or "").lower()
    if "confirm you" in low and "bot" in low or "sign in to confirm" in low or "не бот" in low:
        return "youtube_bot"
    if "video unavailable" in low or "private video" in low or "404" in low or "недоступен" in low:
        return "unavailable"
    if "unsupported url" in low or "не поддерживается" in low:
        return "unsupported"
    if "requested format is not available" in low:
        return "format"
    if "not found on" in low or "не нашли" in low:
        return "not_found"
    return ""

class Manager:
    SAVE_EVERY = 1.5

    def __init__(self, library_factory: Callable[[], Library] = get_library,
                 runner_factory: Callable[[], Runner] = Runner, client_factory: Optional[Callable[[], Any]] = None,
                 state_path: Optional[Path] = None, on_track_added: Optional[Callable[[str], Any]] = None):
        self.library_factory = library_factory
        self.runner_factory = runner_factory
        self.client_factory = client_factory or sources._client
        self.jobs: "collections.OrderedDict[str, Job]" = collections.OrderedDict()
        self.queue: "collections.deque[Job]" = collections.deque()
        self.lock = threading.RLock()
        self.qcond = threading.Condition(self.lock)
        self.wake = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.workers: List[threading.Thread] = []
        self.slots = threading.Condition()
        self.active = 0
        self.version = 0
        self.state_path = state_path
        self._loaded = False
        self._saved_at = 0.0
        self._save_lock = threading.Lock()
        if on_track_added is None and library_factory is get_library:

            from . import lyrics as _lyrics
            from . import extras as _extras

            def on_track_added(tid: str) -> None:
                _lyrics.prefetcher().add(tid)
                _extras.analyzer().add(tid)
        self.on_track_added = on_track_added

    def _path(self) -> Optional[Path]:
        if self.state_path:
            return Path(self.state_path)
        try:
            return Path(self.library_factory().root) / "download-jobs.json"
        except Exception:
            return None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self._path()
        if not path or not path.is_file():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self.lock:
            for data in (saved.get("jobs") if isinstance(saved, dict) else saved) or []:
                try:
                    job = Job.from_dict(data)
                except (TypeError, ValueError, AttributeError):
                    continue
                if job.job_id not in self.jobs:
                    self.jobs[job.job_id] = job

    def save(self, force: bool = True) -> None:
        if not force and time.time() - self._saved_at < self.SAVE_EVERY:
            return
        path = self._path()
        if not path:
            return
        with self._save_lock:
            self._saved_at = time.time()
            with self.lock:
                payload = {"version": 1, "jobs": [job.to_dict() for job in self.jobs.values()]}
            try:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                pass

    def parallel(self) -> int:
        try:
            return max(1, min(4, int(self.library_factory().settings().get("parallel_downloads", 2))))
        except Exception:
            return 2

    def start(self, url: str, quality: str = "", playlist_id: str = "", replace_id: str = "") -> Dict[str, Any]:
        """``playlist_id`` — add the result to that playlist when done (r38 «save from search to a playlist»);
        ``replace_id`` — re-download that track (no dedup) and swap it in place (r38 «re-download in best quality»)."""
        self._ensure_loaded()
        url = str(url or "").strip()
        if not url:
            raise ValueError("Пустая ссылка / Empty link")
        info = sources.detect(url)
        if not info.get("source"):
            raise ValueError("Не похоже на ссылку / This does not look like a link")
        with self.lock:
            for job in self.jobs.values():
                if job.url == url and job.status in ("queued", "running"):
                    return {"job_id": job.job_id, "existing": True}
        info = {**info, "playlist_id": str(playlist_id or ""), "replace_id": str(replace_id or "")}
        job = Job(url, "download", quality, info)
        job.title = url if info["source"] != "search" else url
        return {"job_id": self._enqueue(job), "source": info["source"], "kind": info["kind"]}

    def start_import(self, path: str) -> Dict[str, Any]:
        self._ensure_loaded()
        folder = Path(str(path or "")).expanduser()
        if not folder.is_dir():
            raise ValueError("Папка не найдена / Folder not found")
        job = Job(str(folder), "import", info={"source": "local", "kind": "folder", "title": folder.name})
        return {"job_id": self._enqueue(job)}

    def _job(self, job_id: str) -> Job:
        self._ensure_loaded()
        job = self.jobs.get(str(job_id))
        if not job:
            raise KeyError("Задание не найдено / Job not found")
        return job

    def cancel(self, job_id: str) -> Dict[str, Any]:
        job = self._job(job_id)
        job.cancel.set()
        for holder in [job.holder, *list(job.holders)]:
            proc = holder.get("proc")
            if proc is not None:
                _kill(proc)
        with self.lock:
            if job in self.queue:
                self.queue.remove(job)
                job.status, job.finished_at = "cancelled", time.time()
        with self.slots:
            self.slots.notify_all()
        self._touch(True)
        return {"ok": True, "job_id": job.job_id}

    def retry(self, job_id: str) -> Dict[str, Any]:
        """Run the job again — only its failed / cancelled / unfinished items; done ones are kept."""
        job = self._job(job_id)
        if job.status in ("queued", "running"):
            return {"job_id": job.job_id, "retried": 0}
        job.cancel = threading.Event()
        job.status, job.error, job.error_code, job.progress, job.interrupted = "queued", "", "", 0.0, False
        retried = 0
        try:
            lib = self.library_factory()
        except Exception:
            lib = None
        for item in job.items:
            if (item.get("status") in ("done", "exists") and lib is not None and item.get("track_id")
                    and job.kind != "import" and not lib.row(item["track_id"])):
                item["status"] = "pending"
                item.pop("track_id", None)
            if item.get("status") in ("error", "cancelled", "pending", "running", "queued"):
                item.update(status="pending", error="", error_code="", progress=0)
                retried += 1
        with self.lock:
            self.queue.append(job)
        self._kick()
        return {"job_id": job.job_id, "retried": retried}

    def clear(self) -> Dict[str, Any]:
        self._ensure_loaded()
        with self.lock:
            for key in [k for k, j in self.jobs.items() if j.status in ("done", "error", "cancelled")]:
                self.jobs.pop(key, None)
        self._touch(True)
        return {"ok": True}

    def list(self) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        with self.lock:
            return [job.public() for job in reversed(self.jobs.values())]

    def _touch(self, force: bool = False) -> None:
        self.version += 1
        self.save(force)

    def _enqueue(self, job: Job) -> str:
        with self.lock:
            self.jobs[job.job_id] = job
            while len(self.jobs) > MAX_JOBS_KEPT:
                oldest = next((k for k, j in self.jobs.items() if j.status not in ("queued", "running")), None)
                if not oldest:
                    break
                self.jobs.pop(oldest)
            self.queue.append(job)
        self._kick()
        return job.job_id

    def _kick(self) -> None:
        self._touch(True)
        with self.qcond:

            want = self.parallel()
            while len(self.workers) < want:
                worker = threading.Thread(target=self._loop, name=f"memumusic-jobs-{len(self.workers) + 1}", daemon=True)
                self.workers.append(worker)
                worker.start()
            self.worker = self.workers[0] if self.workers else None
            self.qcond.notify_all()

    def _loop(self) -> None:
        me = threading.current_thread()
        while True:
            with self.qcond:
                while not self.queue:
                    if not self.qcond.wait(timeout=60) and not self.queue:
                        if me in self.workers:
                            self.workers.remove(me)
                        return
                job = self.queue.popleft()
            try:
                self.run(job)
            except BaseException:
                with self.qcond:
                    if me in self.workers:
                        self.workers.remove(me)
                raise

    def _acquire(self, job: Job) -> None:
        with self.slots:
            while self.active >= self.parallel():
                if job.cancel.is_set():
                    raise Cancelled()
                self.slots.wait(timeout=0.5)
            if job.cancel.is_set():
                raise Cancelled()
            self.active += 1

    def _release(self) -> None:
        with self.slots:
            self.active = max(0, self.active - 1)
            self.slots.notify_all()

    def run(self, job: Job) -> None:
        job.status = "running"
        self._touch(True)
        try:
            if job.kind == "import":
                self._run_import(job)
            else:
                self._run_download(job)
            if job.cancel.is_set():
                job.status = "cancelled"
            elif job.items and all(i.get("status") == "error" for i in job.items):
                job.status = "error"
                job.error = job.error or next((i.get("error") for i in job.items if i.get("error")), "")
                job.error_code = next((i.get("error_code") for i in job.items if i.get("error_code")), "")
            else:
                job.status = "done"
                job.progress = 1.0
                failed = [i for i in job.items if i.get("status") == "error"]
                if failed:
                    job.error = f"{len(failed)} не скачано / {len(failed)} failed"
                    job.error_code = next((i.get("error_code") for i in failed if i.get("error_code")), "")
                self._after_done(job)
        except Cancelled:
            job.status = "cancelled"
        except Exception as exc:
            job.status = "cancelled" if job.cancel.is_set() else "error"
            job.error = friendly_error(f"{exc}")[:500]
            job.error_code = error_code(job.error)
        if job.status == "cancelled":
            for item in job.items:
                if item.get("status") in ("running", "pending"):
                    item["status"] = "cancelled"
        job.current = ""
        job.finished_at = time.time()
        self._touch(True)

    def _after_done(self, job: Job) -> None:
        """r38: a finished job adds its tracks to the chosen playlist / replaces the re-downloaded track."""
        if not job.track_ids:
            return
        lib = self.library_factory()
        try:
            if job.info.get("playlist_id"):
                lib.update_playlist(str(job.info["playlist_id"]), {"add": list(job.track_ids)})
            if job.info.get("replace_id") and job.track_ids[0] != job.info["replace_id"]:
                from . import extras
                extras.replace_track(lib, str(job.info["replace_id"]), job.track_ids[0])
        except (KeyError, ValueError):
            pass

    def _set_progress(self, job: Job, index: int, fraction: float) -> None:
        total = max(1, len(job.items))
        fraction = max(0.0, min(1.0, fraction))
        with job.lock:
            job.fracs[index] = fraction
            if 0 <= index < len(job.items):
                job.items[index]["progress"] = round(fraction, 3)
            job.progress = min(0.999, sum(job.fracs.values()) / total)
        self._touch()

    def _added(self, track_id: str) -> None:
        if self.on_track_added:
            try:
                self.on_track_added(track_id)
            except Exception:
                pass

    def _run_download(self, job: Job) -> None:
        lib = self.library_factory()
        info = job.info
        if not job.items:
            job.items = self._plan(job, info)
        if not job.items:
            raise RuntimeError("Ничего не найдено по ссылке / Nothing found at the link")
        settings = lib.settings()
        fmt = format_for(job.quality or settings.get("quality", "best"), settings.get("prefer_m4a", True))
        job.fracs = {i: 1.0 for i, it in enumerate(job.items) if it.get("status") in ("done", "exists")}
        pending = [i for i, it in enumerate(job.items) if it.get("status") not in ("done", "exists")]
        shared: Dict[str, Any] = {"runner": None, "lock": threading.Lock()}
        workers = min(self.parallel(), len(pending))
        if workers <= 1:
            for index in pending:
                if job.cancel.is_set():
                    raise Cancelled()
                self._process_item(lib, shared, job, index, fmt, settings)
        else:
            feed = iter(pending)
            feed_lock = threading.Lock()

            def pull() -> None:
                while not job.cancel.is_set():
                    with feed_lock:
                        index = next(feed, None)
                    if index is None:
                        return
                    self._process_item(lib, shared, job, index, fmt, settings)

            threads = [threading.Thread(target=pull, name=f"memumusic-item-{n}", daemon=True) for n in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        if job.cancel.is_set():
            raise Cancelled()

        job.track_ids = list(dict.fromkeys([i["track_id"] for i in job.items if i.get("track_id")] + job.track_ids))

    def _runner(self, shared: Dict[str, Any]) -> "Runner":
        with shared["lock"]:
            if shared["runner"] is None:
                shared["runner"] = self.runner_factory()
            return shared["runner"]

    def _process_item(self, lib: Library, shared: Dict[str, Any], job: Job, index: int, fmt: str,
                      settings: Dict[str, Any]) -> None:
        item = job.items[index]
        try:
            existing = None if job.info.get("replace_id") else self._existing(lib, item)
            if existing:
                item.update(status="exists", track_id=existing["id"])
            else:
                self._acquire(job)
                holder: Dict[str, Any] = {}
                job.holders.append(holder)
                try:
                    item["status"] = "running"
                    job.current = (" — ".join(x for x in (item.get("artist"), item.get("title")) if x)) or item.get("url", "")
                    self._set_progress(job, index, 0)
                    row = self._download_with_fallbacks(lib, self._runner(shared), job, item, index, fmt, settings, holder)
                finally:
                    try:
                        job.holders.remove(holder)
                    except ValueError:
                        pass
                    self._release()
                item.update(status="done", track_id=row["id"], error="", error_code="")
                if len(job.items) == 1:
                    job.title = " — ".join(x for x in (row.get("artist"), row.get("title")) if x) or job.title
                self._added(row["id"])
            with job.lock:
                if item["track_id"] not in job.track_ids:
                    job.track_ids.append(item["track_id"])
        except Cancelled:
            item["status"] = "cancelled"
            return
        except Exception as exc:
            if job.cancel.is_set():
                item["status"] = "cancelled"
                return
            message = friendly_error(str(exc))[:400]
            item.update(status="error", error=message, error_code=error_code(message))
        self._set_progress(job, index, 1)

    def _candidates(self, runner: "Runner", item: Dict[str, Any], settings: Dict[str, Any]):
        """URLs to try, lazily: the item's own link first; then (for Spotify/Apple/… matches, search text, or a blocked
        YouTube link when «youtube_fallback» is on) YouTube Music → YouTube → SoundCloud. Yields (url, via)."""
        own = item.get("orig_url") or ("" if item.get("matched") else item.get("url") or "")
        if own:
            yield own, "link"
            own_source = sources.detect(own).get("source")
            if own_source not in ("youtube", "ytmusic") or not settings.get("youtube_fallback", True):
                return
        query = item.get("query") or sources.search_query(item)
        if not query.strip() and own:
            query = self._query_from_oembed(own, item)
        if not query.strip():
            return
        seen = {own}
        for via, finder in (("ytmusic", self._match_ytmusic), ("youtube", self._match_youtube)):
            try:
                url = finder(runner, {**item, "query": query})
            except Exception:
                url = ""
            if url and url not in seen:
                seen.add(url)
                yield url, via
        for url in self._match_soundcloud(runner, {**item, "query": query}):
            if url not in seen:
                seen.add(url)
                yield url, "soundcloud"

    def _query_from_oembed(self, url: str, item: Dict[str, Any]) -> str:
        """A blocked YouTube link still has a public oEmbed (title + channel) — enough to look the song up elsewhere."""
        try:
            client = self.client_factory()
            try:
                response = client.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"})
            finally:
                client.close()
            if response.status_code != 200:
                return ""
            data = response.json()
        except Exception:
            return ""
        names = split_title(str(data.get("title") or ""), str(data.get("author_name") or ""))
        if names.get("title"):
            item.setdefault("title", names["title"])
            if not item.get("title"):
                item["title"] = names["title"]
            if not item.get("artist"):
                item["artist"] = names.get("artist", "")
        return " ".join(x for x in (names.get("artist"), names.get("title")) if x)

    def _download_with_fallbacks(self, lib: Library, runner: "Runner", job: Job, item: Dict[str, Any], index: int,
                                 fmt: str, settings: Dict[str, Any], holder: Dict[str, Any]) -> Dict[str, Any]:
        if not item.get("url") and not item.get("orig_url"):
            item["matched"] = True
        elif not item.get("orig_url") and not item.get("matched"):
            item["orig_url"] = item["url"]
        first_error: Optional[Exception] = None
        bot_blocked = False
        tried = 0
        for url, via in self._candidates(runner, item, settings):
            if job.cancel.is_set():
                raise Cancelled()
            if bot_blocked and sources.detect(url).get("source") in ("youtube", "ytmusic"):
                continue
            tried += 1
            try:
                row = self._download_item(lib, runner, job, {**item, "url": url}, index, fmt, settings, holder)
                item["via"] = via
                item["url"] = url
                return row
            except Cancelled:
                raise
            except (RuntimeError, OSError) as exc:
                if job.cancel.is_set():
                    raise Cancelled()
                if error_code(str(exc)) == "youtube_bot":
                    bot_blocked = True
                    if first_error is None or error_code(str(first_error)) != "youtube_bot":
                        first_error = exc
                elif first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
        raise RuntimeError("Не нашли трек на YouTube Music, YouTube и SoundCloud / "
                           "Track not found on YouTube Music, YouTube or SoundCloud" if not tried else "yt-dlp")

    def _plan(self, job: Job, info: Dict[str, Any]) -> List[Dict[str, Any]]:
        source, kind = info["source"], info["kind"]
        if source == "search":
            return [{"title": info["url"], "artist": "", "url": "", "query": info["url"], "status": "pending"}]
        if source in sources.META:
            client = self.client_factory()
            try:
                meta = sources.resolve_meta(info, client)
            finally:
                client.close()
            job.title = " — ".join(x for x in (meta.get("artist"), meta.get("title")) if x) or job.title
            job.info = {**info, "kind": meta.get("kind", kind), "cover_url": meta.get("cover_url", "")}
            return [{**it, "url": "", "meta_source": source, "status": "pending",
                     "source_key": f"{source}:{it.get('source_url') or info['url']}" if it.get("source_url") else ""}
                    for it in meta.get("items", []) if it.get("title")]
        if kind in ("playlist", "album", "artist"):
            runner = self.runner_factory()
            data = runner.json(info["url"], flat=True)
            job.title = data.get("title") or job.title
            entries = [e for e in (data.get("entries") or []) if e]
            if not entries and data.get("webpage_url"):
                entries = [data]
            items = []
            for e in entries:
                url = e.get("webpage_url") or e.get("url") or ""
                if url and not url.startswith("http") and e.get("ie_key") == "Youtube":
                    url = "https://www.youtube.com/watch?v=" + url
                if url:
                    items.append({"title": e.get("title") or "", "artist": e.get("uploader") or e.get("channel") or "",
                                  "url": url, "duration": e.get("duration") or 0, "status": "pending",
                                  "album": data.get("title", "") if kind == "album" else ""})
            return items
        return [{"title": "", "artist": "", "url": info["url"], "status": "pending"}]

    def _existing(self, lib: Library, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if item.get("url"):
            found = lib.find_by_source(source_key(item["url"]))
            if found:
                return found
        if item.get("source_key"):
            found = lib.find_by_source(item["source_key"])
            if found:
                return found
        if item.get("meta_source") and item.get("title"):
            return lib.find_similar(item["title"], item.get("artist", ""), item.get("duration") or 0)
        return None

    def _match(self, runner: Runner, item: Dict[str, Any]) -> str:
        """YouTube Music «songs» first (official audio), then YouTube scored by duration and title."""
        try:
            url = self._match_ytmusic(runner, item)
            if url:
                return url
        except Exception:
            pass
        return self._match_youtube(runner, item)

    def _match_ytmusic(self, runner: Runner, item: Dict[str, Any]) -> str:
        query = item.get("query") or sources.search_query(item)
        data = runner.json("https://music.youtube.com/search?q=" + _quote(query) + "#songs", flat=True, limit=3)
        for e in data.get("entries") or []:
            if e and e.get("id"):
                return "https://music.youtube.com/watch?v=" + e["id"]
        return ""

    def _match_youtube(self, runner: Runner, item: Dict[str, Any]) -> str:
        query = item.get("query") or sources.search_query(item)
        want = float(item.get("duration") or 0)
        data = runner.json(f"ytsearch5:{query}", flat=True)
        best, best_score = "", -1e9
        title_key = norm_text(item.get("title", ""))
        for e in data.get("entries") or []:
            if not e or not e.get("id"):
                continue
            score = 0.0
            if want and e.get("duration"):
                score -= abs(float(e["duration"]) - want) / 4
            name = norm_text(e.get("title", ""))
            if title_key and title_key in name:
                score += 10
            if re.search(r"\b(live|cover|karaoke|remix|nightcore|slowed|sped up|8d)\b", name) and not re.search(
                    r"\b(live|cover|karaoke|remix|nightcore|slowed|sped up|8d)\b", title_key):
                score -= 15
            if str(e.get("channel") or "").endswith("- Topic"):
                score += 5
            if score > best_score:
                best, best_score = "https://www.youtube.com/watch?v=" + e["id"], score
        if not best:
            raise RuntimeError("Не нашли трек на YouTube / Track not found on YouTube")
        return best

    def _match_soundcloud(self, runner: Runner, item: Dict[str, Any]) -> List[str]:
        want = float(item.get("duration") or 0)
        title_key = norm_text(item.get("title", ""))
        try:
            data = runner.json("scsearch5:" + (item.get("query") or sources.search_query(item)), flat=True)
        except Exception:
            return []
        found = []
        for e in data.get("entries") or []:
            url = (e or {}).get("webpage_url") or (e or {}).get("url") or ""
            if not url.startswith("http") or (title_key and title_key not in norm_text(e.get("title", ""))):
                continue
            if want and e.get("duration") and abs(float(e["duration"]) - want) > 15:
                continue
            found.append(url)
        return found[:3]

    def _download_item(self, lib: Library, runner: Runner, job: Job, item: Dict[str, Any], index: int, fmt: str,
                       settings: Dict[str, Any], holder: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = runner.download(item["url"], fmt, lib.download_dir(), bool(settings.get("embed_metadata", True)),
                                 lambda f: self._set_progress(job, index, f * 0.95), job.cancel,
                                 job.holder if holder is None else holder,
                                 str(settings.get("cookies_from_browser") or ""))
        path = Path(result.get("filepath") or "")
        if not path.is_file():
            raise RuntimeError("Файл не появился / The file did not appear")
        meta_source = item.get("meta_source")
        if meta_source:
            fields = {"title": item.get("title"), "artist": item.get("artist"), "album": item.get("album"),
                      "track_no": item.get("track_no"), "year": item.get("year"), "source": meta_source,
                      "source_url": item.get("source_url") or job.url,
                      "source_key": item.get("source_key") or source_key(item.get("source_url") or job.url)}
            cover_url = item.get("cover_url") or job.info.get("cover_url") or result.get("thumbnail")
        else:
            uploader = result.get("artist") or result.get("creator") or result.get("uploader") or result.get("channel") or ""
            if result.get("track") and result.get("artist"):
                names = {"title": result["track"], "artist": result["artist"]}
            else:
                names = split_title(result.get("title") or path.stem, uploader)
            detected = sources.detect(result.get("webpage_url") or item["url"])
            fields = {**names, "album": result.get("album") or item.get("album") or "",
                      "track_no": result.get("track_number") or 0, "year": result.get("release_year") or "",
                      "source": detected.get("source") or job.source,
                      "source_url": result.get("webpage_url") or item["url"],
                      "source_key": source_key(item.get("orig_url") or item["url"])}
            cover_url = result.get("thumbnail")
        fields.update(file=str(path), duration=result.get("duration") or item.get("duration") or 0,
                      bitrate=int(result.get("abr") or 0))
        if not fields.get("duration"):
            fields["duration"] = tools.probe_duration(path)
        cover = self._fetch_cover(cover_url)
        if settings.get("embed_cover", True):

            from . import tagger
            tagger.embed(path, fields, cover)
        return lib.add(fields, cover)

    def _fetch_cover(self, url: Optional[str]) -> Optional[bytes]:
        if not url or not str(url).startswith("http"):
            return None
        try:
            client = self.client_factory()
            try:
                response = client.get(url)
            finally:
                client.close()
            if response.status_code == 200 and len(response.content) > 64:
                return response.content
        except Exception:
            pass
        return None

    def _run_import(self, job: Job) -> None:
        lib = self.library_factory()
        folder = Path(job.url)
        files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXTS and p.is_file())
        if not job.items:
            job.items = [{"title": p.name, "artist": "", "path": str(p), "status": "pending"} for p in files[:20000]]
        covers: Dict[str, Optional[bytes]] = {}
        for index, item in enumerate(job.items):
            if job.cancel.is_set():
                raise Cancelled()
            if item.get("status") in ("done", "exists"):
                continue
            path = Path(item["path"])
            job.current = path.name
            try:
                existing = lib.find_by_file(path)
                if existing:
                    item.update(status="exists", track_id=existing["id"])
                else:
                    tags = read_tags(path)
                    fields = {**tags["fields"], "file": str(path), "source": "local", "source_key": "file:" + str(path)}
                    if not fields.get("duration"):
                        fields["duration"] = tools.probe_duration(path)
                    cover = tags.get("cover")
                    if cover is None:
                        key = str(path.parent)
                        if key not in covers:
                            covers[key] = folder_cover(path.parent)
                        cover = covers[key]
                    row = lib.add(fields, cover)
                    item.update(status="done", track_id=row["id"], title=row["title"], artist=row["artist"])
                    self._added(row["id"])
                job.track_ids.append(item["track_id"])
            except Exception as exc:
                item.update(status="error", error=str(exc)[:300])
            if index % 10 == 0 or index == len(job.items) - 1:
                self._set_progress(job, index, 1)

def _quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote_plus(text)

_MANAGER: Optional[Manager] = None
_MANAGER_LOCK = threading.Lock()

def manager() -> Manager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = Manager()
        return _MANAGER

def search_online(q: str, limit: int = 12, music: bool = False, runner_factory: Callable[[], Runner] = Runner) -> List[Dict[str, Any]]:
    q = str(q or "").strip()
    if not q:
        return []
    limit = max(1, min(30, int(limit or 12)))
    runner = runner_factory()
    if music:
        data = runner.json("https://music.youtube.com/search?q=" + _quote(q) + "#songs", flat=True, limit=limit)
    else:
        data = runner.json(f"ytsearch{limit}:{q}", flat=True)
    out = []
    for e in (data.get("entries") or [])[:limit]:
        if not e or not e.get("id"):
            continue
        uploader = e.get("channel") or e.get("uploader") or ""
        names = split_title(e.get("title") or "", uploader)
        thumbs = e.get("thumbnails") or []
        thumb = thumbs[-1].get("url") if thumbs else f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"
        url = e.get("url") if str(e.get("url") or "").startswith("http") else "https://www.youtube.com/watch?v=" + e["id"]
        out.append({"title": names["title"], "artist": names["artist"], "raw_title": e.get("title") or "",
                    "duration": e.get("duration") or 0, "url": url, "thumb": thumb, "id": e["id"],
                    "source": "ytmusic" if music else "youtube", "views": e.get("view_count") or 0, "channel": uploader})
    return out
