"""Round 35 (protocol): the built-in ``memuai://`` routes.  Registry and rules: :mod:`app.deeplinks`.

Navigation routes are ``safe`` (they only open something or prefill text — nothing is sent, saved or
started).  Everything that changes data, starts work or may cost money is ``confirm``: the MemuAI window
shows the exact action, the link and its source first.  ``block`` routes exist so that obvious attempts
(``memuai://exec?…``) get a clear refusal instead of «unknown action».
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from .deeplinks import Ctx, LinkError, P, register_route


def _connect_browser_extension(params, ctx):
    from .browser_extension_connect import approve
    try:
        return approve(params["challenge"], params["extension_id"])
    except ValueError as exc:
        raise LinkError("bad_browser_request", str(exc)) from exc


register_route(
    "browser-extension/connect", _connect_browser_extension, "confirm", owner="browser_extension",
    params={"challenge": P("id", required=True, max=32), "extension_id": P("id", required=True, max=32)},
    title=("Подключить расширение MemuAI", "Connect the MemuAI extension"),
    describe=lambda p, lang: (["Allow this Chrome profile to connect to MemuAI. Extension ID: " + p["extension_id"],
                               "Page reading, browser control and scheduled actions follow your integration settings."]
                              if lang == "en" else ["Разрешить этому профилю Chrome подключиться к MemuAI. ID расширения: " + p["extension_id"],
                                                    "Чтение страниц, управление браузером и расписания подчиняются настройкам интеграции."]),
    example="memuai://browser-extension/connect?challenge=0123456789abcdef0123456789abcdef&extension_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

log = logging.getLogger("memuai.deeplinks")


def _en(lang: str) -> bool:
    return lang == "en"


def _quote(text: Any, limit: int = 280) -> str:
    text = str(text or "").strip()
    return "«" + (text if len(text) <= limit else text[:limit - 1] + "…") + "»"


# ---------------------------------------------------------------- chats

def _describe_chat_new(p: Dict[str, Any], lang: str) -> List[str]:
    en = _en(lang)
    lines = ["A new chat opens." if en else "Откроется новый чат."]
    if p.get("q"):
        lines.append(("Request: " if en else "Запрос: ") + _quote(p["q"]))
    if p.get("model"):
        lines.append(("Model: " if en else "Модель: ") + str(p["model"]))
    if p.get("temp"):
        lines.append("A temporary chat (not saved to history)." if en else "Временный чат (не сохранится в истории).")
    if p.get("send") and p.get("q"):
        lines.append("The request is SENT to the model right away — this uses your limits or money."
                     if en else "Запрос будет СРАЗУ отправлен модели — это тратит лимиты или деньги.")
    elif p.get("q"):
        lines.append("The text is only put into the input box — you send it yourself."
                     if en else "Текст только вставится в поле ввода — отправите вы сами.")
    return lines


register_route(
    "chat/new", None, "safe", ui="chat.new", owner="protocol",
    params={"q": P("text", max=8000), "model": P("model"), "send": P("bool", default=False), "temp": P("bool", default=False)},
    title=("Новый чат", "New chat"), describe=_describe_chat_new,
    confirm_if=lambda p: bool(p.get("send") and p.get("q")),
    example="memuai://chat/new?q=Привет&model=gpt-6.1-sol", aliases=("new", "ask"))

register_route(
    "chat", None, "safe", ui="chat.open", owner="protocol", positional=("id",),
    params={"id": P("id", required=True)}, title=("Открыть чат", "Open a chat"),
    describe=lambda p, lang: [("Opens the chat " if _en(lang) else "Откроется чат ") + str(p.get("id"))],
    example="memuai://chat/chat-1a2b3c4d5e6f")
register_route(
    "chat/open", None, "safe", ui="chat.open", owner="protocol",
    params={"id": P("id", required=True)}, title=("Открыть чат", "Open a chat"),
    describe=lambda p, lang: [("Opens the chat " if _en(lang) else "Откроется чат ") + str(p.get("id"))],
    example="memuai://chat/open?id=chat-1a2b3c4d5e6f")


# ---------------------------------------------------------------- places inside MemuAI

TABS = ("chat", "work", "code", "plan", "inbox", "skills", "catalog", "modules", "panels", "memory", "search",
        "settings", "docs", "home")

register_route(
    "open", None, "safe", ui="tab.open", owner="protocol", positional=("tab",),
    params={"tab": P("enum", required=True, choices=TABS)}, title=("Открыть раздел", "Open a section"),
    describe=lambda p, lang: [("Opens the section: " if _en(lang) else "Откроется раздел: ") + str(p.get("tab"))],
    example="memuai://open/plan", aliases=("tab",))

register_route(
    "settings", None, "safe", ui="settings.open", owner="protocol", positional=("section",),
    params={"section": P("slug", default="general")}, title=("Настройки", "Settings"),
    describe=lambda p, lang: [("Opens Settings → " if _en(lang) else "Откроются Настройки → ") + str(p.get("section") or "general")],
    example="memuai://settings/links")

register_route(
    "inbox", None, "safe", ui="inbox.open", owner="protocol", positional=("id",),
    params={"id": P("id")}, title=("Входящие", "Inbox"),
    describe=lambda p, lang: ["Opens the Inbox" + (f" on item {p['id']}" if p.get("id") else "") + "." if _en(lang)
                              else "Откроются «Входящие»" + (f" на записи {p['id']}" if p.get("id") else "") + "."],
    example="memuai://inbox")

register_route(
    "module", None, "safe", ui="module.open", owner="protocol", positional=("id",),
    params={"id": P("slug", required=True), "q": P("line", max=500)}, title=("Открыть модуль", "Open a module"),
    # A module decides itself what to do with a passed text (it may start work): then ask first.
    confirm_if=lambda p: bool(p.get("q")),
    describe=lambda p, lang: [("Opens the module " if _en(lang) else "Откроется модуль ") + str(p.get("id"))]
    + ([("Text for it: " if _en(lang) else "Текст для него: ") + _quote(p["q"])] if p.get("q") else []),
    example="memuai://module/studio")

register_route(
    "search", None, "safe", ui="search.open", owner="protocol",
    params={"q": P("line", max=300)}, title=("Поиск по MemuAI", "Search MemuAI"),
    describe=lambda p, lang: [("Opens search (Ctrl+K)" if _en(lang) else "Откроется поиск (Ctrl+K)")
                              + (f": {_quote(p['q'])}" if p.get("q") else "")],
    example="memuai://search?q=отпуск")

register_route(
    "help", None, "safe", ui="settings.open", owner="protocol",
    params={"section": P("slug", default="links")}, title=("Справка по ссылкам", "Links help"),
    describe=lambda p, lang: ["Opens the guide to memuai:// links." if _en(lang) else "Откроется справка по ссылкам memuai://."],
    example="memuai://help")


# ---------------------------------------------------------------- Quick Access

async def _quick(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    quick = getattr(getattr(ctx.app, "state", None), "quick_ask", None) if ctx.app is not None else None
    text = str(p.get("q") or "")
    if quick is not None and getattr(quick, "native_ok", False):
        import asyncio
        shown = await asyncio.to_thread(quick.show, "link")
        if shown:
            if text:
                await asyncio.sleep(0.35)
                push = getattr(quick, "_push", None)
                if callable(push):
                    await asyncio.to_thread(push, "window.MemuQuick && MemuQuick.setInput(%s)" % json.dumps(text, ensure_ascii=False))
            return {"ok": True, "message": "Быстрый доступ открыт" if ctx.lang != "en" else "Quick Access opened"}
    # No native window (browser playground, Quick Access off): the main window handles it.
    return {"ok": True, "ui": {"do": "quick.open", "q": text}}


register_route(
    "quick", _quick, "safe", owner="protocol",
    params={"q": P("line", max=2000)}, title=("Быстрый доступ", "Quick Access"),
    describe=lambda p, lang: [("Opens Quick Access" if _en(lang) else "Откроется Быстрый доступ")
                              + (f" with {_quote(p['q'])} in the box (not sent)" if _en(lang) and p.get("q")
                                 else f" с текстом {_quote(p['q'])} в поле (не отправляется)" if p.get("q") else "")],
    example="memuai://quick?q=Переведи")


# ---------------------------------------------------------------- Proxy pages

PROXY_PAGES = ("home", "accounts", "quota", "models", "composite", "local", "public", "chat", "detector", "settings", "logs")


async def _proxy(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    proxy_json = ctx.services.get("proxy_json")
    page = str(p.get("page") or "home")
    if proxy_json is not None:
        try:
            status, data = await proxy_json("POST", "/api/admin/show_window", json.dumps({"tab": page}).encode())
            if status == 200 and isinstance(data, dict) and data.get("ok"):
                return {"ok": True, "message": "MemuAI Proxy открыт" if ctx.lang != "en" else "MemuAI Proxy opened"}
        except Exception:  # noqa: BLE001 - not running: started below
            pass
    launcher = ctx.services.get("launch_proxy")
    if launcher is not None:
        await launcher()
        if proxy_json is not None:
            try:
                await proxy_json("POST", "/api/admin/show_window", json.dumps({"tab": page}).encode())
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "message": "MemuAI Proxy запускается…" if ctx.lang != "en" else "Starting MemuAI Proxy…"}
    return {"ok": False, "error": "proxy_unavailable",
            "message": "MemuAI Proxy недоступен" if ctx.lang != "en" else "MemuAI Proxy is unavailable"}


register_route(
    "proxy", _proxy, "safe", owner="protocol", positional=("page",),
    params={"page": P("enum", choices=PROXY_PAGES, default="home")}, title=("Страница MemuAI Proxy", "MemuAI Proxy page"),
    describe=lambda p, lang: [("Opens MemuAI Proxy → " if _en(lang) else "Откроется MemuAI Proxy → ") + str(p.get("page") or "home")],
    example="memuai://proxy/composite")


# ---------------------------------------------------------------- files (MemuNote, MemuFiles)

def _describe_note(p: Dict[str, Any], lang: str) -> List[str]:
    en = _en(lang)
    return [("MemuNote opens the file:" if en else "MemuNote откроет файл:"), str(p.get("path")),
            ("The file is only opened for viewing and editing; nothing is run." if en
             else "Файл только откроется для просмотра и правки; ничего не запускается.")]


async def _note(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    path = Path(str(p["path"]))
    if not path.is_file():
        raise LinkError("not_found", "Файл не найден: " + str(path), "File not found: " + str(path))
    from . import memunote_manager
    try:
        memunote_manager.open_app([str(path)])
    except memunote_manager.ManagerError as exc:  # type: ignore[attr-defined]
        if getattr(exc, "code", "") == "not_installed":
            raise LinkError("not_installed", "MemuNote не установлен (Настройки → MemuNote).",
                            "MemuNote is not installed (Settings → MemuNote).") from exc
        raise
    return {"ok": True, "message": "MemuNote открывается…" if ctx.lang != "en" else "Opening MemuNote…"}


register_route(
    "note", _note, "confirm", owner="protocol", params={"path": P("path", required=True)},
    title=("Открыть файл в MemuNote", "Open a file in MemuNote"), describe=_describe_note,
    example="memuai://note?path=C:%5CUsers%5CИмя%5Cnotes.md", aliases=("memunote", "note/open"))


def _open_memufiles(path: str) -> bool:
    try:
        from .fsindex_api import open_in_memufiles
        if open_in_memufiles(path):
            return True
    except Exception:  # noqa: BLE001 - fsindex is optional
        pass
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        for exe in (here / "MemuFiles.exe", here.parent / "MemuFiles" / "MemuFiles.exe"):
            if exe.is_file():
                subprocess.Popen([str(exe), path], close_fds=True)  # noqa: S603 - our own exe, a checked local path
                return True
        return False
    entry = Path(__file__).resolve().parent.parent / "memufiles.py"
    if entry.is_file():
        subprocess.Popen([sys.executable, str(entry), path], close_fds=True,  # noqa: S603
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return False


async def _files(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    import asyncio
    path = str(p["path"])
    if not os.path.exists(path):
        raise LinkError("not_found", "Путь не найден: " + path, "Path not found: " + path)
    if not await asyncio.to_thread(_open_memufiles, path):
        raise LinkError("not_installed", "MemuFiles недоступен.", "MemuFiles is unavailable.")
    return {"ok": True, "message": "MemuFiles открывается…" if ctx.lang != "en" else "Opening MemuFiles…"}


register_route(
    "files", _files, "safe", owner="protocol", params={"path": P("path", required=True)},
    title=("Открыть в MemuFiles", "Open in MemuFiles"),
    describe=lambda p, lang: [("MemuFiles shows: " if _en(lang) else "MemuFiles покажет: ") + str(p.get("path"))],
    example="memuai://files?path=C:%5CUsers%5CИмя%5CDownloads", aliases=("files/open", "memufiles"))


# ---------------------------------------------------------------- scheduled tasks

def _task(task_id: str) -> Dict[str, Any]:
    try:
        from .pgdata import load_schedules
        for row in load_schedules():
            if str(row.get("id")) == task_id:
                return row
    except Exception:  # noqa: BLE001
        pass
    return {}


def _describe_task_run(p: Dict[str, Any], lang: str) -> List[str]:
    en = _en(lang)
    row = _task(str(p.get("id")))
    if not row:
        return ["Task not found: " + str(p.get("id")) if en else "Задача не найдена: " + str(p.get("id"))]
    lines = [("The scheduled task runs NOW: " if en else "Задача по расписанию запустится СЕЙЧАС: ") + _quote(row.get("name") or row.get("id"), 120)]
    prompt = row.get("prompt") or row.get("text") or ""
    if prompt:
        lines.append(("It asks the model: " if en else "Она попросит модель: ") + _quote(prompt, 240))
    lines.append("It may use the model, the internet, integrations and your limits — like a run by the schedule."
                 if en else "Она может использовать модель, интернет, интеграции и ваши лимиты — как запуск по расписанию.")
    return lines


async def _task_run(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    task_id = str(p["id"])
    if not _task(task_id):
        raise LinkError("not_found", "Задача не найдена.", "Task not found.")
    self_request = ctx.services.get("self_request")
    if self_request is None:
        raise LinkError("unavailable", "Планирование недоступно.", "Scheduling is unavailable.")
    status, data = await self_request("POST", "/api/plan/run", {"id": task_id, "lang": ctx.lang})
    if status != 200 or not (isinstance(data, dict) and data.get("ok")):
        raise LinkError("failed", "Не удалось запустить задачу.", "Could not start the task.")
    return {"ok": True, "message": "Задача запущена" if ctx.lang != "en" else "Task started", "ui": {"do": "plan.open", "id": task_id}}


register_route(
    "task/run", _task_run, "confirm", owner="protocol", params={"id": P("id", required=True)},
    title=("Запустить задачу сейчас", "Run a task now"), describe=_describe_task_run,
    example="memuai://task/run?id=1a2b3c4d5e6f", aliases=("plan/run",))   # Раунд 36: «plan/run» читалась как task/<id=run>
register_route(
    "task", None, "safe", ui="plan.open", owner="protocol", positional=("id",), params={"id": P("id")},
    title=("Открыть задачу", "Open a task"),
    describe=lambda p, lang: [("Opens Scheduling" if _en(lang) else "Откроется «Планирование»")
                              + (f" → {_quote(_task(str(p['id'])).get('name') or p['id'], 80)}" if p.get("id") else "")],
    example="memuai://task/1a2b3c4d5e6f", aliases=("task/open", "plan"))


# ---------------------------------------------------------------- Do For You

register_route(
    "dfy/start", None, "confirm", ui="dfy.start", owner="protocol", params={"task": P("text", required=True, max=2000)},
    title=("Сделай за меня: новая задача", "Do For You: a new task"),
    describe=lambda p, lang: ([
        "Do For You STARTS a task in its own browser:", _quote(p.get("task"), 400),
        "The AI acts on websites for you; payments and secrets still ask you separately."] if _en(lang) else [
        "«Сделай за меня» ЗАПУСТИТ задачу в своём браузере:", _quote(p.get("task"), 400),
        "ИИ будет действовать на сайтах за вас; оплаты и секреты по-прежнему спрашиваются отдельно."]),
    example="memuai://dfy/start?task=Найди+билеты", aliases=("dfy",))


# ---------------------------------------------------------------- skills from a URL

_SKILL_HOSTS = ("github.com", "raw.githubusercontent.com")


async def _skill_preview(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    import asyncio
    from . import skills_catalog
    try:
        found = await asyncio.to_thread(skills_catalog.resolve_url, p["url"])
    except Exception as exc:  # noqa: BLE001 - CatalogError and network errors
        raise LinkError("preview_failed", "Не удалось прочитать скилл: " + str(exc)[:200],
                        "Could not read the skill: " + str(exc)[:200]) from exc
    repo = found.get("repo") or ""
    items = []
    for row in (found.get("skills") or [])[:5]:
        try:
            detail = await asyncio.to_thread(skills_catalog.skill_detail, repo or row.get("repo", ""), row.get("path", ""))
        except Exception:  # noqa: BLE001
            detail = {}
        skill = detail.get("skill") if isinstance(detail.get("skill"), dict) else detail
        body = str(skill.get("prompt") or skill.get("body") or "")
        items.append({"name": str(skill.get("name") or row.get("name") or "")[:120],
                      "desc": str(skill.get("description") or skill.get("desc") or row.get("description") or "")[:600],
                      "when": str(skill.get("when") or "")[:400], "category": str(skill.get("category") or "")[:60],
                      "prompt": body[:200000], "license": str(skill.get("license") or row.get("license") or "")[:80]})
    items = [x for x in items if x["name"] and x["prompt"]]
    if not items:
        raise LinkError("no_skill", "По ссылке нет скилла (SKILL.md).", "No skill (SKILL.md) at the link.")
    ctx.cache["skills"] = items
    ctx.cache["label"] = repo
    en = ctx.lang == "en"
    lines = [(f"Repository: {repo}" if en else f"Репозиторий: {repo}")]
    for item in items:
        lines.append(f"{item['name']} — {item['desc'][:160]}")
    lines.append("Skills are instructions for the AI. Scripts are not installed or run."
                 if en else "Скилл — это инструкции для ИИ. Скрипты не устанавливаются и не запускаются.")
    code = "\n\n".join(f"# {x['name']}\n{x['prompt'][:1500]}" for x in items)
    return {"lines": lines, "code": code[:6000]}


async def _skill_install(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    items = ctx.cache.get("skills")
    if not items:
        raise LinkError("no_preview", "Сначала посмотрите, что будет установлено.", "Look at what will be installed first.")
    return {"ok": True, "ui": {"do": "skills.add", "items": items, "label": ctx.cache.get("label") or ""}}


register_route(
    "skill/install", _skill_install, "confirm", owner="protocol",
    params={"url": P("url", required=True, hosts=_SKILL_HOSTS)}, title=("Установить скилл по ссылке", "Install a skill from a link"),
    describe=lambda p, lang: [("Adds AI skills from: " if _en(lang) else "Добавит скиллы ИИ из: ") + str(p.get("url"))],
    preview=_skill_preview, example="memuai://skill/install?url=https://github.com/owner/repo/tree/main/skills/x")


# ---------------------------------------------------------------- custom provider (no keys in links)

def _describe_provider(p: Dict[str, Any], lang: str) -> List[str]:
    en = _en(lang)
    lines = [("Adds a custom OpenAI-compatible connection to MemuAI Proxy:" if en
              else "Добавит своё OpenAI-совместимое подключение в MemuAI Proxy:"),
             ("Name (model prefix): " if en else "Имя (префикс моделей): ") + str(p.get("name")),
             "Base URL: " + str(p.get("base_url"))]
    if p.get("models"):
        lines.append(("Models: " if en else "Модели: ") + str(p.get("models"))[:300])
    lines.append("Your requests to these models will go to this address. The link never contains a key — "
                 "type it below only if you trust the service." if en else
                 "Ваши запросы к этим моделям пойдут на этот адрес. Ключа в ссылке нет — "
                 "введите его ниже, только если доверяете сервису.")
    return lines


async def _provider_add(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    proxy_json = ctx.services.get("proxy_json")
    if proxy_json is None:
        raise LinkError("unavailable", "MemuAI Proxy недоступен.", "MemuAI Proxy is unavailable.")
    fields = {"name": p["name"], "base_url": p["base_url"]}
    if p.get("models"):
        fields["models"] = p["models"]
    key = str(ctx.inputs.get("api_key") or "").strip()
    if key:
        fields["api_key"] = key[:4000]
    body = {"provider": "custom", "fields": fields, "label": str(p.get("label") or "")[:120]}
    status, data = await proxy_json("POST", "/api/admin/accounts", json.dumps(body).encode("utf-8"))
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        message = (data or {}).get("error") if isinstance(data, dict) else ""
        raise LinkError("failed", "Proxy не добавил подключение: " + str(message or status)[:200],
                        "The Proxy did not add the connection: " + str(message or status)[:200])
    return {"ok": True, "message": ("Подключение добавлено" if ctx.lang != "en" else "Connection added")
            + ("" if key else (" — ключ можно добавить в Proxy → Аккаунты" if ctx.lang != "en" else " — add a key in Proxy → Accounts"))}


register_route(
    "provider/add", _provider_add, "confirm", owner="protocol",
    params={"name": P("slug", required=True, max=40), "base_url": P("url", required=True),
            "models": P("line", max=600), "label": P("line", max=120)},
    title=("Добавить своё подключение (Proxy)", "Add a custom connection (Proxy)"), describe=_describe_provider,
    inputs=({"id": "api_key", "type": "password", "optional": True, "label_ru": "API-ключ (необязательно, вводите сами)",
             "label_en": "API key (optional, type it yourself)",
             "hint_ru": "Ключ не берётся из ссылки и не показывается в журнале.",
             "hint_en": "The key never comes from the link and is not written to the log."},),
    example="memuai://provider/add?name=myapi&base_url=https://api.example.com/v1&models=model-a")


# ---------------------------------------------------------------- composite model import

def _composite_models(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("models"), list):
        rows = data["models"]
    elif isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = [data]
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)][:20]


async def _composite_preview(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    en = ctx.lang == "en"
    models = _composite_models(p.get("data"))
    if not models:
        raise LinkError("empty", "В ссылке нет составных моделей.", "The link has no composite models.")
    proxy_json = ctx.services.get("proxy_json")
    lines = [("Imports composite models into MemuAI Proxy (merged with yours):" if en
              else "Импортирует составные модели в MemuAI Proxy (добавятся к вашим):")]
    for model in models:
        cands = [str(c.get("model") if isinstance(c, dict) else c) for c in (model.get("candidates") or [])][:8]
        line = f"{model.get('id') or '?'}" + (f" «{model.get('name')}»" if model.get("name") else "")
        line += (" → " + ", ".join(cands)) if cands else ""
        if model.get("rules"):
            line += f" · {'rules' if en else 'правил'}: {len(model.get('rules') or [])}"
        lines.append(line[:300])
        if proxy_json is not None:
            try:
                status, data = await proxy_json("POST", "/api/admin/composite/validate", json.dumps({"model": model}).encode())
                issues = (data or {}).get("issues") if isinstance(data, dict) else None
                for issue in (issues or [])[:4]:
                    text = issue.get("message_en" if en else "message") or issue.get("message") or ""
                    lines.append(("⚠ " if issue.get("level") != "warning" else "· ") + str(text)[:200])
            except Exception:  # noqa: BLE001 - Proxy down: preview without checks
                pass
    lines.append("Existing composites with the same id are replaced." if en else "Составные модели с тем же id будут заменены.")
    ctx.cache["data"] = p.get("data")
    return {"lines": lines, "code": json.dumps(p.get("data"), ensure_ascii=False, indent=2)[:12000]}


async def _composite_import(p: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    proxy_json = ctx.services.get("proxy_json")
    if proxy_json is None:
        raise LinkError("unavailable", "MemuAI Proxy недоступен.", "MemuAI Proxy is unavailable.")
    status, data = await proxy_json("POST", "/api/admin/composite/import",
                                    json.dumps({"data": p.get("data"), "mode": "merge"}, ensure_ascii=False).encode("utf-8"))
    if status != 200:
        message = (data or {}).get("message_en" if ctx.lang == "en" else "error") if isinstance(data, dict) else ""
        raise LinkError("failed", "Импорт не удался: " + str(message or status)[:200], "Import failed: " + str(message or status)[:200])
    count = len((data or {}).get("imported") or []) if isinstance(data, dict) else 0
    return {"ok": True, "message": (f"Импортировано: {count}" if ctx.lang != "en" else f"Imported: {count}")}


register_route(
    "composite/import", _composite_import, "confirm", owner="protocol",
    params={"data": P("b64json", required=True, max=8000)},
    title=("Импорт составной модели", "Import a composite model"),
    describe=lambda p, lang: [("Composite models in the link: " if _en(lang) else "Составных моделей в ссылке: ")
                              + str(len(_composite_models(p.get("data"))))],
    preview=_composite_preview, example="memuai://composite/import?data=<base64url JSON>")


# ---------------------------------------------------------------- never from a link

for _name in ("exec", "run", "shell", "cmd", "powershell", "eval", "script", "file", "delete", "uninstall",
              "install", "download", "settings/reset", "data/delete", "data/export", "key", "token"):
    register_route(_name, None, "block", owner="protocol", title=("Запрещено", "Forbidden"))
