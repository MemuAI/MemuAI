"""Round 33 (r33-quick): the AI command router of Quick Ask — «команды через ИИ, но максимально просто».

Order of a short request typed into Quick Ask (``POST /api/assistant/run {text}``):

1. the instant local matcher (``assistant_intents.match``) — unambiguous phrases run with no model at all;
2. when it found nothing, or found «закрой X» / «переключись на X» about something that is not running, the
   router makes ONE tiny model call: a short system prompt with the compact list of enabled actions and the live
   context (running apps — visible windows and tray apps — and the user's own command names), ``temperature 0``,
   no reasoning, ``max_tokens`` ≈ 100, streamed and cut as soon as the JSON object closes;
3. the answer ``{"a": "<action id>", "p": {…}}`` goes through the SAME safe action layer (``Engine.run`` with
   ``source="router"``: validation, disabled actions, rate limit of model calls, confirmation of confirm/dangerous
   actions — the user's «trusted» list does not apply to an AI guess); ``{"a": "chat"}``, a timeout, an error or
   anything unparsable → the text goes to the normal chat answer as before (nothing is lost, only the wait).

Modes (``settings.ai_router``): ``auto`` (default) — only for phrases that look like a command (an imperative verb
at the start, a short phrase naming a running app…); ``always`` — every short one-line request; ``off``.
The model is ``settings.router_model`` or, when empty, the Quick Ask model (the user's fast model).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from . import assistant_catalog as catalog
from . import assistant_intents as intents

log = logging.getLogger("memuai.assistant.router")

MODES = ("auto", "always", "off")
DEFAULT_TIMEOUT = 4.0
MAX_TOKENS = 120
MAX_TOKENS_ECO = 100  # economy: the answer is a short JSON blob, 100 tokens is plenty
CTX_CACHE_TTL = 10.0  # economy: live context (running apps, windows) is re-read at most this often
MAX_TEXT = 200

SKIP = {"assistant.help", "assistant.undo", "shell.run", "layout.capture", "layout.restore", "reminder.cancel"}

_VERBS_RU = (r"открой|откр|закрой|закрыть|закрывай|запусти|запустить|включи|включить|вруби|выключи|выключить|выруби|"
             r"заверши|завершить|убей|убери|останови|остановить|перезапусти|перезагрузи|сверни|свернуть|разверни|"
             r"развернуть|переключи|переключись|перейди|покажи|спрячь|скрой|поставь|постави|сделай|сделать|прибавь|"
             r"убавь|увеличь|уменьши|повысь|понизь|громче|тише|сохрани|удали|очисти|создай|напомни|засеки|заведи|"
             r"заблокируй|блокируй|сфоткай|заскринь|скопируй|вставь|напечатай|набери|нажми|перемести|перенеси|"
             r"прикрепи|найди|поищи|загугли|включай|выключай|запускай|открывай|продолжи|пауза|отключи|подключи|"
             r"отмени|верни|запиши|нарисуй|переведи|посчитай|сгенерируй|придумай пароль|выйди|глуши|приглуши|"
             r"врубай|вырубай|стопни|стоп|play|пусти|поменяй|смени|измени|установи|настрой")
_VERBS_EN = (r"open|close|quit|exit|kill|end|launch|start|run|stop|pause|resume|play|mute|unmute|turn|set|switch|"
             r"show|hide|minimi[sz]e|maximi[sz]e|restore|lock|take|make|increase|decrease|raise|lower|find|search|"
             r"google|remind|snap|move|copy|paste|type|press|empty|shut|restart|reboot|sleep|focus|bring|activate|"
             r"enable|disable|connect|disconnect|toggle|change|put|create|cancel|translate|draw|generate|save")
_HEAD = re.compile(rf"^(?:(?:{_VERBS_RU})\w*|(?:{_VERBS_EN}))\b", re.I)
_WORDS = re.compile(r"\b(?:громкост\w*|звук\w*|яркост\w*|окн\w*|таймер\w*|будильник\w*|трек\w*|музык\w*|скрин\w*|"
                    r"wi-?fi|вайфай|блютуз|bluetooth|volume|brightness|window|timer|alarm|track|screenshot|"
                    r"на минимум|на максимум|на полную|потише|погромче)\b", re.I)

def looks_like_command(text: str) -> bool:
    """A cheap guess that a phrase is a command and not a question/chat — keeps the router off ordinary chat."""
    clean = intents.normalize(text)
    if not clean or len(clean) > MAX_TEXT or "\n" in clean:
        return False
    words = clean.split()
    if len(words) > 14:
        return False
    if clean.endswith("?") or intents._QUESTION.match(clean):
        return False

    show = re.match(r"(?:покажи|show)(?:\s+(?:мне|me))?\s+(.+)$", clean, re.I)
    if show and not _WORDS.search(clean) and not intents._known_app(show.group(1)):
        return False
    return bool(_HEAD.match(clean)) or (len(words) <= 6 and bool(_WORDS.search(clean)))

def action_lines(settings: Dict[str, Any], economy: bool = False) -> List[str]:
    """``id(param, param?=a|b)`` per enabled action — the router's whole vocabulary (no descriptions: ~2 KB).

    In economy mode only the id and the required parameters survive: enums and optional
    parameters are dropped, because the router's job is classification (which action,
    which names), not parameter validation — the safe layer checks every parameter
    the model sends.
    """
    disabled = set(settings.get("disabled_actions") or [])
    lines = []
    for row in catalog.ACTIONS:
        aid = row["id"]
        if aid in disabled or aid in SKIP or aid.startswith("_") or (row.get("advanced") and not settings.get("advanced_shell")):
            continue
        if economy:
            required = [key for key in row["params"] if key in row["required"]]
            lines.append(f"{aid}({', '.join(required)})")
            continue
        parts = []
        for key, spec in row["params"].items():
            label = key + ("" if key in row["required"] else "?")
            values = [v for v in (spec.get("enum") or []) if v]
            if values:
                label += "=" + "|".join(values[:8]) + ("|…" if len(values) > 8 else "")
            parts.append(label)
        lines.append(f"{aid}({', '.join(parts)})")
    return lines

SYSTEM = (
    "You turn a user's short request to their Windows PC into ONE action. Answer with one JSON object only, "
    "no prose: {\"a\":\"<action id>\",\"p\":{<params>}}. "
    "If it is a question, small talk, needs knowledge or writing, or needs several actions, answer {\"a\":\"chat\"}. "
    "App names: users write them in any spelling, Cyrillic transliteration or with typos (\"инси\" = INCY, "
    "\"телега\" = Telegram); when the user means a running app, put its name EXACTLY as listed in Running. "
    "Closing/quitting an app = app.close; bringing it to front = app.switch; starting it = app.open. "
    "Numbers are numbers; durations in seconds. "
    "Only this PC is controlled: lights, a router, a TV, other people, games' characters or a figure of speech are "
    "not actions — {\"a\":\"chat\"}; never pick a similar-sounding action as a guess.\nActions:\n")

def build_messages(text: str, settings: Dict[str, Any], context: Dict[str, Any],
                   economy: bool = False) -> List[Dict[str, str]]:
    """Router messages. Economy: short action lines, at most 12 running apps and
    10 command names in the user message — the router classifies one short request,
    it does not need the whole desktop inventory."""
    system = SYSTEM + "\n".join(action_lines(settings, economy=economy))
    user = []
    running = context.get("running") or []
    if running:
        user.append("Running: " + "; ".join(running[:12] if economy else running))
    commands = context.get("commands") or []
    if commands:
        user.append("User commands (command.run name): " + "; ".join(commands[:10] if economy else commands))
    user.append("Request: " + text)
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(user)}]

def running_apps(windows: List[Dict[str, Any]], procs: List[Dict[str, Any]], limit: int = 24,
                 titles: bool = True) -> List[str]:
    """«Telegram (Telegram)», «INCY (tray)» — de-duplicated, trimmed, MemuAI's own windows left out.

    ``titles=False`` (economy mode) drops the window titles: the app name alone is
    what the router needs to match a request to a running app, and titles are the
    bulky part (up to 40 characters each).
    """
    from .assistant_procs_win import app_label, user_apps
    out: Dict[str, str] = {}
    for w in windows or []:
        if w.get("own"):
            continue
        name = str(w.get("app") or "").strip()
        title = re.sub(r"\s+", " ", str(w.get("title") or "")).strip()
        if not name:
            continue
        key = name.lower()
        if key not in out:
            out[key] = name + (f" — {title[:40]}" if titles and title and title.lower() != key else "")
    try:
        bg = user_apps(procs or [])
    except Exception:
        bg = []
    for row in bg:
        name = app_label(row.get("name") or row.get("exe", ""), row.get("path", ""))
        key = name.lower()
        if name and key not in out:
            out[key] = name + " (tray/background)"
    return list(out.values())[:limit]

def parse_answer(raw: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """``("app.close", {"name": "INCY"})`` / ``("chat", {})`` / ``None`` (unparsable)."""
    text = intents_strip_reasoning(raw)
    start = text.find("{")
    if start < 0:
        return None
    depth, end = 0, -1
    in_str, esc = False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("a") or data.get("action") or "").strip()
    params = data.get("p") if isinstance(data.get("p"), dict) else data.get("params")
    if not action:
        return None
    return action, (params if isinstance(params, dict) else {})

def intents_strip_reasoning(raw: str) -> str:
    text = str(raw or "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    return text.strip()

def json_closed(text: str) -> bool:
    return parse_answer(text) is not None

async def _call_proxy(engine, model: str, messages: List[Dict[str, str]], timeout: float,
                      max_tokens: int = MAX_TOKENS) -> str:
    if engine.proxy_sse is None:
        raise RuntimeError("no proxy")
    payload = json.dumps({"model": model, "messages": messages, "web_search": False, "thinking": False,
                          "effort": "minimal", "temperature": 0, "max_tokens": max_tokens, "prompt_caching": True},
                         ensure_ascii=False).encode("utf-8")
    chunks: List[str] = []

    async def run() -> None:
        gen = engine.proxy_sse("/api/admin/playground/chat", payload)
        buf = ""
        try:
            async for piece in gen:
                buf += piece.decode("utf-8", "replace") if isinstance(piece, (bytes, bytearray)) else str(piece)
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        item = json.loads(data)
                    except ValueError:
                        continue
                    if item.get("error"):
                        err = item["error"]
                        raise RuntimeError(str(err.get("message", err)) if isinstance(err, dict) else str(err))
                    delta = ((item.get("choices") or [{}])[0]).get("delta") or {}
                    if delta.get("content"):
                        chunks.append(str(delta["content"]))
                        if json_closed("".join(chunks)):
                            return
        finally:
            try:
                await gen.aclose()
            except Exception:
                pass

    await asyncio.wait_for(run(), timeout)
    return "".join(chunks)

async def _call_endpoint(prefs: Dict[str, Any], model: str, messages: List[Dict[str, str]], timeout: float, lang: str,
                         max_tokens: int = MAX_TOKENS) -> str:
    from . import quick_ask_endpoint as endpoint
    return await asyncio.wait_for(endpoint.complete(endpoint.normalize_url(prefs.get("quick_ask_endpoint_url")),
                                                    endpoint.get_key(), model, messages, max_tokens=max_tokens, lang=lang),
                                  timeout)

def pick_model(settings: Dict[str, Any], prefs: Dict[str, Any], economy: bool = False) -> Tuple[str, bool]:
    """(model, via_custom_endpoint). ``router_model`` wins; else the Quick Ask model; else the app's default.

    In economy mode ``router_model_eco`` wins over all of them when set: routing is a
    classification task (one action id from a short list), so a smaller, cheaper
    model is safe here — it never writes text the user reads.
    """
    from . import quick_ask_endpoint as endpoint
    custom = endpoint.active(prefs)
    if economy:
        eco = str(settings.get("router_model_eco") or "").strip()
        if eco:
            return eco, custom
    chosen = str(settings.get("router_model") or "").strip()
    if chosen:
        return chosen, custom
    if custom:
        return str(prefs.get("quick_ask_endpoint_model") or ""), True
    return str(prefs.get("quick_ask_model") or prefs.get("default_model") or ""), False

def should_route(settings: Dict[str, Any], text: str, *, soft_miss: bool = False) -> bool:
    mode = settings.get("ai_router", "auto")
    if mode == "off" or not settings.get("enabled", True):
        return False
    clean = intents.normalize(text)
    if not clean or len(clean) > MAX_TEXT or "\n" in str(text or "").strip():
        return False
    if soft_miss:
        return True
    if mode == "always":
        return not (clean.endswith("?") or intents._QUESTION.match(clean))
    return looks_like_command(clean)

async def route(engine, text: str, lang: str = "ru", *, soft_miss: bool = False,
                prefs: Optional[Dict[str, Any]] = None, call=None) -> Optional[Dict[str, Any]]:
    """One routing decision: ``{"action", "params", "model", "ms"}``, ``{"action": "chat", …}`` or ``None``
    (router off / not a command / no model / failure — the caller answers as before)."""
    settings = engine.settings()
    call = call or getattr(engine, "router_call", None)
    if not should_route(settings, text, soft_miss=soft_miss):
        return None
    if prefs is None:
        try:
            from .pgdata import load_preferences
            prefs = load_preferences()
        except Exception:
            prefs = {}
    economy = settings.get("mode") == "economy"
    model, custom = pick_model(settings, prefs, economy=economy)
    if not model:
        return None
    started = time.monotonic()
    if economy:
        # Live context (running apps, windows) is re-read at most once per 10 s:
        # the router classifies one short request, and the desktop rarely changes
        # between two such requests a few seconds apart.
        cached = getattr(engine, "_router_ctx_cache", None)
        if cached and time.monotonic() - cached[0] < CTX_CACHE_TTL:
            context = cached[1]
        else:
            context = await asyncio.to_thread(live_context, engine, True)
            engine._router_ctx_cache = (time.monotonic(), context)
    else:
        context = await asyncio.to_thread(live_context, engine)
    messages = build_messages(intents.normalize(text)[:MAX_TEXT], settings, context, economy=economy)
    timeout = float(settings.get("router_timeout") or DEFAULT_TIMEOUT)
    max_tokens = MAX_TOKENS_ECO if economy else MAX_TOKENS
    try:
        if call is not None:
            raw = await asyncio.wait_for(call(model, messages), timeout)
        elif custom:
            raw = await _call_endpoint(prefs, model, messages, timeout, lang, max_tokens=max_tokens)
        else:
            raw = await _call_proxy(engine, model, messages, timeout, max_tokens=max_tokens)
    except asyncio.TimeoutError:
        log.info("router: %s did not answer in %.1fs", model, timeout)
        return {"action": "chat", "params": {}, "model": model, "ms": int((time.monotonic() - started) * 1000),
                "reason": "timeout"}
    except Exception as exc:
        log.info("router: %s failed: %s", model, type(exc).__name__)
        return {"action": "chat", "params": {}, "model": model, "ms": int((time.monotonic() - started) * 1000),
                "reason": "error"}
    ms = int((time.monotonic() - started) * 1000)
    parsed = parse_answer(raw)
    if not parsed:
        return {"action": "chat", "params": {}, "model": model, "ms": ms, "reason": "unparsable"}
    action, params = parsed
    row = catalog.get(action)
    if action == "chat" or not row or action in SKIP or action in set(settings.get("disabled_actions") or []) \
            or (row.get("advanced") and not settings.get("advanced_shell")):
        return {"action": "chat", "params": {}, "model": model, "ms": ms, "reason": "chat" if action == "chat" else "unknown"}
    return {"action": action, "params": params, "model": model, "ms": ms}

def live_context(engine, economy: bool = False) -> Dict[str, Any]:
    """Running apps (windows + tray) and the user's command names — best effort, never raises.

    Economy: at most 12 apps, no window titles, at most 10 command names (build_messages
    caps the same lists again, so a stale cached context can never leak a long list in).
    """
    windows: List[Dict[str, Any]] = []
    procs: List[Dict[str, Any]] = []
    try:
        windows = engine.backend.windows()
    except Exception:
        pass
    try:
        procs = engine.backend.procs()
    except Exception:
        pass
    try:
        commands = [c["name"] for c in engine.store.load()["commands"] if c.get("enabled", True)][:10 if economy else 20]
    except Exception:
        commands = []
    return {"running": running_apps(windows, procs, limit=12 if economy else 24, titles=not economy),
            "commands": commands}
