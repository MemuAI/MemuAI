"""Visual test: the model writes a page from a demanding prompt, a person judges the result.

No AI grades anything here. A run sends one prompt from the built-in set
(``static/visual-prompts.json``: the aquarium, the pelican and a collection of
large 3D scenes) through the normal Proxy pipeline, saves what came back and
serves it as a web page. The quality of a clownfish or of a pelican on a
bicycle is a matter for the eyes — and it tells strong models from weak ones
faster than any questionnaire.

The saved page is model output, so it is never trusted: it is served with a
``sandbox`` Content-Security-Policy, which gives it an origin of its own —
scripts and CDN libraries work, but the page cannot reach the panel, its
storage or the admin API.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROMPTS_FILE = Path(__file__).resolve().parent / "static" / "visual-prompts.json"
KEEP_RUNS = 40
MAX_OUTPUT_TOKENS = 32000
MAX_CUSTOM_PROMPT = 60000

_prompts_cache: Optional[List[Dict[str, Any]]] = None
_runs: Dict[str, "Run"] = {}
_lock = threading.Lock()

def prompts() -> List[Dict[str, Any]]:
    global _prompts_cache
    if _prompts_cache is None:
        try:
            data = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
            rows = data.get("prompts") if isinstance(data, dict) else []
        except (OSError, ValueError):
            rows = []
        _prompts_cache = [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("prompt")]
    return _prompts_cache

def prompt_list() -> List[Dict[str, Any]]:
    """The set without the texts (they are long): enough for the picker."""
    return [{"id": row["id"], "title": row.get("title", row["id"]), "title_en": row.get("title_en", row["id"]),
             "group": row.get("group", "3d"), "output": row.get("output", "html"),
             "about": row.get("about", ""), "about_en": row.get("about_en", ""), "chars": len(row["prompt"]),

             "compare_url": row.get("compare_url", "") if str(row.get("compare_url", "")).startswith("https://") else ""}
            for row in prompts()]

def find_prompt(prompt_id: str) -> Optional[Dict[str, Any]]:
    return next((row for row in prompts() if row["id"] == prompt_id), None)

_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)(?:```|\Z)", re.S)
_SVG_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>html,body{{height:100%;margin:0;background:#11131a}}body{{display:grid;place-items:center}}
svg{{max-width:96vw;max-height:96vh;width:auto;height:auto}}</style></head>
<body>
{svg}
</body></html>
"""

def extract_page(answer: str, title: str = "") -> Dict[str, Any]:
    """The page inside a model's answer: ``{"html", "kind", "complete"}``.

    ``kind`` is ``html``, ``svg`` (wrapped into a small page) or ``""`` when the
    answer has neither. ``complete`` is False when the document was cut off.
    """
    text = str(answer or "")
    blocks = [(lang.lower(), body) for lang, body in _FENCE_RE.findall(text)]
    pages = [body for lang, body in blocks if re.search(r"<!doctype html|<html[\s>]", body, re.I)]
    svgs = [body for lang, body in blocks if re.search(r"<svg[\s>]", body, re.I)]
    if not pages and not svgs:

        if re.search(r"<!doctype html|<html[\s>]", text, re.I):
            pages = [text]
        elif re.search(r"<svg[\s>]", text, re.I):
            svgs = [text]
    if pages:
        body = max(pages, key=len)
        start = re.search(r"<!doctype html|<html[\s>]", body, re.I).start()
        end = body.lower().rfind("</html>")
        return {"html": (body[start:end + 7] if end > start else body[start:]).strip() + "\n", "kind": "html",
                "complete": end > start}
    if svgs:
        body = max(svgs, key=len)
        start = re.search(r"<svg[\s>]", body, re.I).start()
        end = body.lower().rfind("</svg>")
        svg = body[start:end + 6] if end > start else body[start:]
        safe_title = re.sub(r"[<>&]", "", title)[:120] or "SVG"
        return {"html": _SVG_PAGE.format(title=safe_title, svg=svg.strip()), "kind": "svg", "complete": end > start}
    return {"html": "", "kind": "", "complete": False}

def _folder() -> Path:
    from .config import DATA_DIR
    path = Path(DATA_DIR) / "visual-tests"
    path.mkdir(parents=True, exist_ok=True)
    return path

def _index_path() -> Path:
    return _folder() / "index.json"

class Run:
    def __init__(self, model: str, prompt: Dict[str, Any]) -> None:
        self.id = secrets.token_hex(8)

        self.key = secrets.token_urlsafe(18)
        self.model = model
        self.prompt_id = prompt["id"]
        self.title = prompt.get("title", prompt["id"])
        self.title_en = prompt.get("title_en", self.title)
        self.prompt = prompt["prompt"]
        self.state = "running"
        self.started = time.time()
        self.finished = 0.0
        self.first = 0.0
        self.chars = 0
        self.reasoning_chars = 0
        self.usage: Dict[str, Any] = {}
        self.kind = ""
        self.complete = False
        self.size = 0
        self.error = ""
        self.cancelled = False
        self.retried = False
        self.task: Optional[asyncio.Task] = None

    def public(self) -> Dict[str, Any]:
        now = self.finished or time.time()
        return {"id": self.id, "key": self.key, "model": self.model, "prompt_id": self.prompt_id, "title": self.title,
                "title_en": self.title_en, "state": self.state, "started": int(self.started),
                "seconds": round(now - self.started, 1),
                "ttft": round(self.first - self.started, 1) if self.first else None,
                "chars": self.chars, "reasoning_chars": self.reasoning_chars,
                "tokens": int(self.usage.get("completion_tokens") or 0) if self.usage.get("estimated") is False else 0,
                "kind": self.kind, "complete": self.complete, "size": self.size, "error": self.error,
                "retried": self.retried}

def _load_index() -> List[Dict[str, Any]]:
    from .storage import load_json_with_recovery
    rows = load_json_with_recovery(_index_path(), [], validator=lambda value: isinstance(value, list))
    return [row for row in rows if isinstance(row, dict) and row.get("id")] if isinstance(rows, list) else []

def _remember(run: Run) -> None:
    from .storage import mutate_json

    dropped: List[str] = []

    def change(raw: Any):
        rows = [row for row in (raw if isinstance(raw, list) else []) if isinstance(row, dict) and row.get("id") != run.id]
        rows.insert(0, run.public())
        dropped.extend(str(row["id"]) for row in rows[KEEP_RUNS:])
        return True, rows[:KEEP_RUNS]

    mutate_json(_index_path(), [], change, backup=False, validator=lambda value: isinstance(value, list),
                repair_invalid=True)
    for run_id in dropped:
        _delete_files(run_id)

def _delete_files(run_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        return
    for suffix in (".html", ".txt"):
        try:
            (_folder() / f"{run_id}{suffix}").unlink()
        except OSError:
            pass

def runs() -> List[Dict[str, Any]]:
    """Running tests first, then the saved ones, newest first."""
    with _lock:
        live = [run.public() for run in _runs.values() if run.state == "running"]
    live_ids = {row["id"] for row in live}
    return sorted(live, key=lambda row: -row["started"]) + [row for row in _load_index() if row["id"] not in live_ids]

def get(run_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        run = _runs.get(run_id)
    if run:
        return run.public()
    return next((row for row in _load_index() if row["id"] == run_id), None)

def page(run_id: str, key: str) -> Optional[str]:
    """The saved page, if the key in the link is the one issued for this run."""
    row = get(run_id)
    if not row or not key or not secrets.compare_digest(str(row.get("key") or ""), str(key)):
        return None
    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        return None
    try:
        return (_folder() / f"{run_id}.html").read_text(encoding="utf-8")
    except OSError:
        return None

def answer(run_id: str) -> Optional[str]:
    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        return None
    try:
        return (_folder() / f"{run_id}.txt").read_text(encoding="utf-8")
    except OSError:
        return None

def cancel(run_id: str) -> bool:
    with _lock:
        run = _runs.get(run_id)
    if not run or run.state != "running":
        return False
    run.cancelled = True
    if run.task:
        run.task.get_loop().call_soon_threadsafe(run.task.cancel)
    return True

def delete(run_id: str) -> None:
    from .storage import mutate_json
    cancel(run_id)
    with _lock:
        _runs.pop(run_id, None)

    def change(raw: Any):
        return True, [row for row in (raw if isinstance(raw, list) else []) if row.get("id") != run_id]

    mutate_json(_index_path(), [], change, backup=False, validator=lambda value: isinstance(value, list),
                repair_invalid=True)
    _delete_files(run_id)

async def start(model: str, prompt_id: str = "", custom: str = "") -> Dict[str, Any]:
    model = str(model or "").strip()
    if not model:
        raise ValueError("Выберите модель для теста")
    custom = str(custom or "").strip()
    if custom:
        prompt = {"id": "custom", "title": "Своё задание", "title_en": "Custom prompt", "prompt": custom[:MAX_CUSTOM_PROMPT]}
    else:
        prompt = find_prompt(str(prompt_id or ""))
        if prompt is None:
            raise ValueError("Задание не найдено")
    run = Run(model, prompt)
    with _lock:
        _runs[run.id] = run
        for old in [key for key, item in _runs.items() if item.state != "running" and time.time() - item.started > 3600]:
            _runs.pop(old, None)
    run.task = asyncio.get_running_loop().create_task(_execute(run))
    return run.public()

RETRY_PAUSE = 3.0
_RETRYABLE_RE = re.compile(r"upstream[_ ]timeout|не начала ответ|did not start|timed? ?out|overloaded|temporarily|"
                           r"high demand|try again|повторите запрос|HTTP (?:408|429|5\d\d)\b", re.I)

def _retryable(exc: BaseException, message: str) -> bool:
    try:
        status = int(getattr(exc, "status", 0) or 0)
    except (TypeError, ValueError):
        status = 0
    return status in (408, 429, 500, 502, 503, 504, 529) or bool(_RETRYABLE_RE.search(message))

async def _execute(run: Run) -> None:
    from . import model_call

    text: List[str] = []

    async def stream(params: Dict[str, Any]) -> None:
        async for event in model_call.stream(run.model, [{"role": "user", "content": run.prompt}], params):
            if event["type"] == "usage":
                run.usage = event["usage"]
                continue
            if not run.first:
                run.first = time.time()
            if event["type"] == "text":
                text.append(event["text"])
                run.chars += len(event["text"])
            else:
                run.reasoning_chars += len(event["text"])

    try:
        try:
            await stream({"max_tokens": MAX_OUTPUT_TOKENS})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(getattr(exc, "message", "") or exc)
            if text:
                raise
            if re.search(r"max[_ ]?(output[_ ]?)?tokens|maximum.*tokens|too large", message, re.I):

                await stream({})
            elif _retryable(exc, message):

                run.retried = True
                await asyncio.sleep(RETRY_PAUSE)
                await stream({"max_tokens": MAX_OUTPUT_TOKENS})
            else:
                raise
        body = "".join(text)
        if not body.strip():
            raise RuntimeError("Модель вернула пустой ответ")
        found = extract_page(body, f"{run.title} — {run.model}")
        (_folder() / f"{run.id}.txt").write_text(body, encoding="utf-8")
        if found["html"]:
            (_folder() / f"{run.id}.html").write_text(found["html"], encoding="utf-8")
            run.kind, run.complete, run.size = found["kind"], found["complete"], len(found["html"].encode("utf-8"))
            run.state = "done"
        else:
            run.state = "error"
            run.error = "В ответе нет страницы HTML или SVG — откройте текст ответа"
    except asyncio.CancelledError:
        run.state = "cancelled"
    except Exception as exc:
        run.state = "error"
        run.error = str(getattr(exc, "message", "") or exc)[:500]
    finally:
        run.finished = time.time()
        if run.state != "cancelled":
            try:
                _remember(run)
            except Exception:
                pass
