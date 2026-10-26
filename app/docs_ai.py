"""Round 29: «Кратко от ИИ» / «AI summary» for every documentation page.

Two layers:

* **Shipped summaries** — ``app/docs/summaries/{ru,en}.json`` are generated once
  by the developers with a strong model (``tests/docs_summaries.py``), reviewed
  against the article and shipped with the docs.  Every entry keeps the
  :func:`content_hash` of the article it was written for, so a test
  (``tests/round29_docs_ai_test.py``) fails as soon as an article changes and its
  summary was not regenerated, and the viewer marks such a summary as possibly
  outdated instead of silently showing wrong facts.  Image lines (screenshots)
  do not take part in the hash: adding a picture does not invalidate a summary.
* **On demand** — ``POST /api/pg/docs/ai`` asks the user's current model through
  MemuAI Proxy to retell the page again (``mode=summary``) or to answer a
  question about it (``mode=ask``).  The article text is the only context; the
  answer is never stored on disk.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

try:
    from fastapi import Request
except ImportError:  # pragma: no cover - MemuCode reads the docs without FastAPI
    Request = None  # type: ignore[assignment,misc]

SCHEMA = 1
MAX_POINTS = 6
_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

def summaries_dir() -> Path:
    from .docs import docs_dir
    return docs_dir() / "summaries"

def normalized_text(title: str, body: str) -> str:
    """The article as the summary sees it: title + body without image lines and extra blank lines."""
    lines = [line.rstrip() for line in str(body or "").replace("\r\n", "\n").split("\n")
             if not _IMAGE_LINE.match(line)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return f"# {str(title or '').strip()}\n\n{text}\n"

def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(normalized_text(title, body).encode("utf-8")).hexdigest()[:16]

def load(lang: str) -> Dict[str, Any]:
    """``{"schema", "lang", "model", "pages": {id: {hash, tldr, points, generated}}}`` (cached by mtime)."""
    lang = "en" if lang == "en" else "ru"
    path = summaries_dir() / f"{lang}.json"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {"schema": SCHEMA, "lang": lang, "model": "", "pages": {}}
    with _lock:
        cached = _cache.get(lang)
        if cached and cached[0] == stamp:
            return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("pages"), dict):
        data = {"schema": SCHEMA, "lang": lang, "model": "", "pages": {}}
    with _lock:
        _cache[lang] = (stamp, data)
    return data

def save(lang: str, data: Dict[str, Any]) -> None:
    """Used by the generator script only (the app never writes into its own folder)."""
    lang = "en" if lang == "en" else "ru"
    folder = summaries_dir()
    folder.mkdir(parents=True, exist_ok=True)
    pages = data.get("pages") or {}
    ordered = {"schema": SCHEMA, "lang": lang, "model": data.get("model", ""),
               "note": data.get("note", ""), "pages": {key: pages[key] for key in sorted(pages)}}
    tmp = folder / f".{lang}.json.tmp"
    tmp.write_text(json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(folder / f"{lang}.json")
    with _lock:
        _cache.pop(lang, None)

def clean_points(points: Any) -> List[str]:
    rows = []
    for item in points if isinstance(points, list) else []:
        text = re.sub(r"^(?:[-•]|\*(?!\*))\s*", "", re.sub(r"\s+", " ", str(item or "")).strip()).strip()
        if text:
            rows.append(text[:400])
    return rows[:MAX_POINTS]

def summary_for(lang: str, page_id: str, title: str, body: str) -> Optional[Dict[str, Any]]:
    """The shipped summary of a page, with ``stale`` when the article changed after it was written."""
    data = load(lang)
    entry = (data.get("pages") or {}).get(page_id)
    if not isinstance(entry, dict) or not str(entry.get("tldr") or "").strip():
        return None
    return {"tldr": str(entry.get("tldr") or "").strip()[:600], "points": clean_points(entry.get("points")),
            "generated": str(entry.get("generated") or "")[:20],
            "model": str(entry.get("model") or data.get("model") or "")[:120],
            "stale": entry.get("hash") != content_hash(title, body)}

def summary_messages(lang: str, title: str, body: str) -> List[Dict[str, str]]:
    article = normalized_text(title, body)
    if lang == "en":
        system = ("You write the «AI summary» card shown at the top of a page of the MemuAI desktop app documentation. "
                  "Use only facts stated in the article; never invent features, settings, shortcuts or numbers. "
                  "Keep names of buttons, settings and sections exactly as the article writes them. "
                  "Answer with JSON only: {\"tldr\": \"one or two sentences: what this feature/topic gives the user and when to use it (start with the subject itself, not with 'This page' or 'The article')\", "
                  "\"points\": [\"3 to 5 short key takeaways, each one sentence, practical: what to do / where it is / what to remember\"]}. "
                  "You may use **bold** for one or two key words and `code` for literal values and ++Ctrl+K++ for shortcuts. "
                  "Write in English.")
        user = f"The article:\n\n{article}"
    else:
        system = ("Ты пишешь карточку «Кратко от ИИ» в начале страницы документации настольного приложения MemuAI. "
                  "Используй только факты из статьи; не выдумывай функции, настройки, сочетания клавиш и числа. "
                  "Названия кнопок, настроек и разделов пиши точно как в статье. "
                  "Ответь только JSON: {\"tldr\": \"одно-два предложения: что эта функция/тема даёт пользователю и когда она нужна (начни сразу с сути, без «Эта страница» и «В статье»)\", "
                  "\"points\": [\"3–5 коротких главных мыслей, по одному предложению, практично: что сделать / где это / что запомнить\"]}. "
                  "Можно выделить **жирным** одно-два ключевых слова, `код` — для точных значений, ++Ctrl+K++ — для клавиш. "
                  "Пиши по-русски, на «вы».")
        user = f"Статья:\n\n{article}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def ask_messages(lang: str, title: str, body: str, question: str,
                 history: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    article = normalized_text(title, body)
    if lang == "en":
        system = ("You answer questions about one page of the MemuAI documentation. Rely on the article below; if it "
                  "does not contain the answer, say so in one sentence and suggest searching the documentation; never "
                  "name a menu path, setting, plugin or integration the article does not mention. Be concise (a few sentences or a short list), use Markdown. Answer in English.\n\n"
                  f"The article:\n\n{article}")
    else:
        system = ("Ты отвечаешь на вопросы об одной странице документации MemuAI. Опирайся на статью ниже; если в ней "
                  "нет ответа — скажи об этом одним предложением и предложи поиск по документации; не называй пути "
                  "в меню, настройки, плагины и интеграции, которых нет в статье. "
                  "Отвечай кратко (несколько предложений или короткий список), Markdown, по-русски.\n\n"
                  f"Статья:\n\n{article}")
    messages = [{"role": "system", "content": system}]
    for row in (history or [])[-6:]:
        if isinstance(row, dict) and row.get("role") in ("user", "assistant") and str(row.get("content") or "").strip():
            messages.append({"role": row["role"], "content": str(row["content"])[:4000]})
    messages.append({"role": "user", "content": str(question or "").strip()[:2000]})
    return messages

def literal_problems(summary: Dict[str, Any], title: str, body: str) -> List[str]:
    """Shortcuts (++…++) and `literals` of a summary that the article does not contain — a cheap accuracy guard."""
    article = normalized_text(title, body)
    plain_article = re.sub(r"\s+", " ", article.lower())
    text = " ".join([str(summary.get("tldr") or "")] + [str(p) for p in summary.get("points") or []])
    problems = []
    for item in [str(summary.get("tldr") or "")] + [str(p) for p in summary.get("points") or []]:
        if item.count("`") % 2 or item.count("**") % 2:
            problems.append("unbalanced markup: " + item[:60])
    for keys in re.findall(r"\+\+([^\s+](?:[^\n]*?[^\s+])??)\+\+", text):
        if keys.lower().replace(" ", "") not in article.lower().replace(" ", ""):
            problems.append(f"++{keys}++")
    for code in re.findall(r"`([^`]+)`", text):
        literal = code.strip().lower()
        tokens = [token for token in re.findall(r"[\w-]{3,}", literal)]
        if literal not in plain_article and literal.replace(".", ",") not in plain_article and not (tokens and all(token in plain_article for token in tokens)):
            problems.append(f"`{code}`")
    return problems

def parse_summary(text: str) -> Dict[str, Any]:
    """The model's answer → ``{tldr, points}``; tolerant to code fences and to a plain bullet list."""
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and str(data.get("tldr") or "").strip():
                return {"tldr": re.sub(r"\s+", " ", str(data["tldr"])).strip()[:600],
                        "points": clean_points(data.get("points"))}
        except ValueError:
            pass
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    bullets = [line for line in lines if re.match(r"^([-*•]|\d+[.)])\s+", line)]
    rest = [line for line in lines if line not in bullets]
    tldr = re.sub(r"\s+", " ", " ".join(rest)).strip()[:600]
    points = clean_points([re.sub(r"^([-*•]|\d+[.)])\s+", "", line) for line in bullets])
    if not tldr and points:
        tldr, points = points[0], points[1:]
    if not tldr:
        raise ValueError("empty summary")
    return {"tldr": tldr, "points": points}

Completion = Callable[..., Awaitable[str]]

def register(app, completion: Completion) -> None:
    """``POST /api/pg/docs/ai`` — retell / ask through the user's model (``completion`` = ``proxy_completion``)."""
    import asyncio
    from fastapi.responses import JSONResponse
    from . import docs

    app.state.docs_ai_completion = completion

    @app.post("/api/pg/docs/ai")
    async def docs_ai(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        lang = "en" if body.get("lang") == "en" else "ru"
        say = (lambda ru, en: en) if lang == "en" else (lambda ru, en: ru)
        mode = "ask" if body.get("mode") == "ask" else "summary"
        model = str(body.get("model") or "").strip()[:300]
        if not model:
            return JSONResponse({"error": say("Выберите модель в окне чата — ответит она",
                                              "Pick a model in the chat window — it will answer"), "code": "no_model"}, 400)
        page = await asyncio.to_thread(docs.page, lang, str(body.get("id") or ""))
        if not page:
            return JSONResponse({"error": say("Нет такой страницы документации", "No such documentation page")}, 404)
        if mode == "ask":
            question = str(body.get("question") or "").strip()
            if not question:
                return JSONResponse({"error": say("Напишите вопрос", "Type a question")}, 400)
            history = body.get("history") if isinstance(body.get("history"), list) else []
            messages = ask_messages(page["lang"], page["title"], page["body"], question, history)
        else:
            messages = summary_messages(page["lang"], page["title"], page["body"])
        complete = getattr(app.state, "docs_ai_completion", None) or completion
        try:
            text = await complete(model, messages)
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            offline = any(word in message.lower() for word in ("connect", "refused", "proxy", "connection"))
            return JSONResponse({"error": say("MemuAI Proxy не запущен — запустите его и попробуйте снова",
                                              "MemuAI Proxy is not running — start it and try again") if offline
                                 else say("Модель не ответила: ", "The model did not answer: ") + message[:300],
                                 "code": "offline" if offline else "model"}, 502)
        if mode == "ask":
            return {"mode": "ask", "answer": str(text or "").strip(), "model": model}
        try:
            parsed = parse_summary(text)
        except ValueError:
            return JSONResponse({"error": say("Модель вернула пустой ответ", "The model returned an empty answer"),
                                 "code": "empty"}, 502)
        return {"mode": "summary", "model": model, **parsed}
