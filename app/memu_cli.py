"""Round 35 (protocol): ``memu`` — the MemuAI command line (``memu.exe``; ``memucode`` stays the coding agent).

Talks to what is already running, found through the discovery files of the data folder
(``%APPDATA%\\MemuAI\\memuai\\memuai.json`` = the MemuAI window server, ``proxy.json`` = MemuAI Proxy):

    memu ask "текст" [-m MODEL] [--stream] [--system S]     answer from a model through the Proxy (stdin pipe works)
    memu chat new ["текст"] [-m MODEL] [--send] [--temp]    a new chat in the MemuAI window (via a memuai:// link)
    memu open <memuai://…|route> [key=value …]               open a link in MemuAI (dangerous ones ask in the window)
    memu link <route> [key=value …]                           print a link (nothing is opened)
    memu models | status | inbox [--all]
    memu task list | memu task run <id> [--yes]              without --yes the run is confirmed in the MemuAI window
    memu search-files <query> [--limit N]                    PC file search (MemuAI's file index)
    memu note <file>                                          open a file in MemuNote
    memu completion bash|zsh|powershell                      shell completion script
Global: ``--json`` (machine output), ``--lang ru|en``, ``--token`` (Proxy API key if one is set; else
``MEMUAI_API_KEY`` / ``MEMUAI_TOKEN`` / the Proxy's own config).  Exit codes: 0 ok, 1 error, 2 usage,
3 MemuAI/Proxy not reachable, 4 refused (link rejected, links or memu disabled), 130 interrupted.

Only the standard library is used (a small exe).  Nothing here can bypass the window confirmations:
links go through ``/api/pg/deeplink/dispatch`` like any other source (``source=cli``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_UNREACHABLE, EXIT_REFUSED, EXIT_INTERRUPTED = 0, 1, 2, 3, 4, 130
COMMANDS = ("ask", "chat", "open", "link", "models", "status", "task", "inbox", "search-files", "note", "completion", "help", "version")

class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code

def detect_lang(argv: Sequence[str]) -> str:
    for index, value in enumerate(argv):
        if value == "--lang" and index + 1 < len(argv):
            return "en" if argv[index + 1].lower().startswith("en") else "ru"
        if value.startswith("--lang="):
            return "en" if value.split("=", 1)[1].lower().startswith("en") else "ru"
    env = os.environ.get("MEMU_LANG") or ""
    if env:
        return "en" if env.lower().startswith("en") else "ru"
    try:
        prefs = json.loads((data_dir() / "memuai" / "preferences.json").read_text(encoding="utf-8"))
        if prefs.get("language") in ("ru", "en"):
            return prefs["language"]
    except (OSError, ValueError, AttributeError):
        pass
    loc = os.environ.get("LANG") or os.environ.get("LC_ALL") or ""
    if loc:
        return "ru" if loc.lower().startswith("ru") else "en"
    return "ru"

LANG = "ru"

def T(ru: str, en: str) -> str:
    return en if LANG == "en" else ru

HELP_RU = """memu — командная строка MemuAI

Использование:
  memu ask "вопрос" [-m МОДЕЛЬ] [--stream] [--system ТЕКСТ]
                                  ответ модели через MemuAI Proxy; текст можно подать в конвейер:
                                  git diff | memu ask "Опиши изменения"
  memu chat new ["текст"] [-m МОДЕЛЬ] [--send] [--temp]
                                  новый чат в окне MemuAI (--send спросит подтверждение в окне)
  memu open <ссылка|маршрут> [ключ=значение …]
                                  открыть ссылку memuai:// (опасное подтверждается в окне MemuAI)
  memu link <маршрут> [ключ=значение …]   собрать ссылку memuai://, ничего не открывая
  memu models                     модели MemuAI Proxy
  memu status                     что запущено, адреса, включены ли ссылки
  memu task list                  задачи «Планирования»
  memu task run <id> [--yes]      запустить задачу (без --yes — подтверждение в окне MemuAI)
  memu inbox [--all]              «Входящие»
  memu search-files <запрос> [--limit N]   поиск файлов на ПК (*.pdf, часть имени)
  memu note <файл>                открыть файл в MemuNote
  memu completion bash|zsh|powershell      скрипт автодополнения

Общие ключи: --json (вывод для скриптов), --lang ru|en, --token КЛЮЧ (если у Proxy задан API-ключ).
Коды выхода: 0 — успех, 1 — ошибка, 2 — неверная команда, 3 — MemuAI/Proxy недоступен,
4 — отказано (ссылка отклонена или выключена в настройках), 130 — прервано.
Маршруты ссылок: memu link --list.  memucode — отдельная команда для работы с кодом.
"""

HELP_EN = """memu — the MemuAI command line

Usage:
  memu ask "question" [-m MODEL] [--stream] [--system TEXT]
                                  a model's answer through MemuAI Proxy; text can be piped in:
                                  git diff | memu ask "Describe the changes"
  memu chat new ["text"] [-m MODEL] [--send] [--temp]
                                  a new chat in the MemuAI window (--send asks for confirmation there)
  memu open <link|route> [key=value …]
                                  open a memuai:// link (dangerous ones are confirmed in the MemuAI window)
  memu link <route> [key=value …]  build a memuai:// link without opening it
  memu models                     MemuAI Proxy models
  memu status                     what runs, addresses, whether links are on
  memu task list                  Scheduling tasks
  memu task run <id> [--yes]      run a task (without --yes it is confirmed in the MemuAI window)
  memu inbox [--all]              the Inbox
  memu search-files <query> [--limit N]    PC file search (*.pdf, part of a name)
  memu note <file>                open a file in MemuNote
  memu completion bash|zsh|powershell      shell completion script

Global options: --json (output for scripts), --lang ru|en, --token KEY (when the Proxy has an API key).
Exit codes: 0 ok, 1 error, 2 bad command, 3 MemuAI/Proxy unreachable, 4 refused (link rejected or
turned off in settings), 130 interrupted.
Link routes: memu link --list.  memucode is the separate coding command.
"""

def data_dir() -> Path:
    base = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if not base:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MemuAI")
    return Path(base)

def runtime_url(name: str) -> str:
    """URL from memuai/<name> — only a loopback http address without credentials is trusted."""
    try:
        data = json.loads((data_dir() / "memuai" / name).read_text(encoding="utf-8"))
        url = str(data.get("url") or "").rstrip("/")
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme == "http" and (parts.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")
                and parts.port and not parts.username and not parts.password and parts.path in ("", "/")):
            return url
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ""

def proxy_url() -> str:
    return (os.environ.get("MEMUAI_PROXY_URL") or "").rstrip("/") or runtime_url("proxy.json") or "http://127.0.0.1:8317"

def memuai_url() -> str:
    return (os.environ.get("MEMUAI_APP_URL") or "").rstrip("/") or runtime_url("memuai.json")

def proxy_token(explicit: str = "") -> str:
    if explicit:
        return explicit
    for name in ("MEMUAI_API_KEY", "MEMUAI_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    try:
        cfg = json.loads((data_dir() / "config.json").read_text(encoding="utf-8"))
        keys = cfg.get("api_keys") if isinstance(cfg, dict) else None
        if isinstance(keys, list) and keys and isinstance(keys[0], str):
            return keys[0]
    except (OSError, ValueError):
        pass
    return ""

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def http(method: str, url: str, body: Any = None, *, token: str = "", timeout: float = 30.0,
         stream: bool = False) -> Tuple[int, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "memu-cli"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        response = _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": raw[:300]}
    except (urllib.error.URLError, OSError) as exc:
        raise CliError(T("Не удалось подключиться: ", "Could not connect: ") + url.split("/api")[0].split("/v1")[0]
                       + f" ({getattr(exc, 'reason', exc)})", EXIT_UNREACHABLE) from exc
    if stream:
        return response.status, response
    with response:
        raw = response.read().decode("utf-8", "replace")
    try:
        return response.status, json.loads(raw) if raw else {}
    except ValueError:
        return response.status, {"text": raw}

def online(url: str, path: str, key: str = "") -> bool:
    if not url:
        return False
    try:
        status, data = http("GET", url + path, timeout=1.5)
    except CliError:
        return False
    if status != 200 or not isinstance(data, dict):
        return False
    return data.get("service") == "memuai-proxy" if key == "proxy" else data.get("ok") is True

def need_memuai(start: bool = False, link: str = "") -> str:
    url = memuai_url()
    if url and online(url, "/api/pg/ping"):
        return url
    if start and launch_memuai(link):
        for _ in range(80):
            time.sleep(0.25)
            url = memuai_url()
            if url and online(url, "/api/pg/ping"):
                return url
    raise CliError(T("MemuAI не запущен. Откройте MemuAI и повторите.", "MemuAI is not running. Open MemuAI and try again."), EXIT_UNREACHABLE)

def launch_memuai(link: str = "") -> bool:
    """Start MemuAI (installed layout: ..\\MemuAI\\MemuAI.exe next to this program's folder; or the source checkout)."""
    args = ["--deeplink", link] if link else []
    candidates: List[List[str]] = []
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        for folder in (here, here.parent, here.parent.parent / "MemuAI", here.parent / "MemuAI"):
            exe = folder / "MemuAI.exe"
            if exe.is_file():
                candidates.append([str(exe), *args])
    else:
        entry = Path(__file__).resolve().parent.parent / "memuai.py"
        if entry.is_file():
            candidates.append([sys.executable, str(entry), *args])
    for command in candidates:
        try:
            flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, creationflags=flags, start_new_session=os.name != "nt")
            return True
        except OSError:
            continue
    return False

class Out:
    def __init__(self, as_json: bool) -> None:
        self.json = as_json

    def emit(self, data: Any, text: str = "") -> None:
        if self.json:
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        elif text:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.stdout.flush()

def parse_kv(items: Sequence[str]) -> Dict[str, str]:
    params: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise CliError(T(f"Ожидалось ключ=значение: {item}", f"key=value expected: {item}"), EXIT_USAGE)
        key, value = item.split("=", 1)
        params[key.strip()] = value
    return params

def take_opts(argv: List[str], flags: Sequence[str], valued: Sequence[str]) -> Tuple[Dict[str, Any], List[str]]:
    opts: Dict[str, Any] = {}
    rest: List[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        name, eq, inline = arg.partition("=")
        if arg == "--":
            rest.extend(argv[i + 1:])
            break
        if arg in flags:
            opts[arg.lstrip("-")] = True
        elif name in valued:
            if eq:
                opts[name.lstrip("-")] = inline
            elif i + 1 < len(argv):
                opts[name.lstrip("-")] = argv[i + 1]
                i += 1
            else:
                raise CliError(T(f"После {arg} нужно значение", f"{arg} needs a value"), EXIT_USAGE)
        elif arg.startswith("--") and len(arg) > 2:
            raise CliError(T(f"Неизвестный ключ {arg}", f"Unknown option {arg}"), EXIT_USAGE)
        else:
            rest.append(arg)
        i += 1
    return opts, rest

def dispatch_link(link: str, out: Out, *, start: bool = True) -> int:
    url = memuai_url()
    if not (url and online(url, "/api/pg/ping")):
        if not start:
            raise CliError(T("MemuAI не запущен.", "MemuAI is not running."), EXIT_UNREACHABLE)

        try:
            from app import deeplinks
            deeplinks.split(link)
        except ImportError:
            pass
        except Exception as exc:
            raise CliError(T("Ссылка отклонена: ", "Link rejected: ") + str(getattr(exc, "en" if LANG == "en" else "ru", exc)), EXIT_REFUSED)
        if not launch_memuai(link):
            raise CliError(T("MemuAI не найден — откройте его вручную.", "MemuAI was not found — open it by hand."), EXIT_UNREACHABLE)
        out.emit({"ok": True, "started": True, "link": link}, T("MemuAI запускается и откроет ссылку.", "MemuAI is starting and will open the link."))
        return EXIT_OK
    status, data = http("POST", url + "/api/pg/deeplink/dispatch", {"url": link, "source": "cli", "lang": LANG})
    if not isinstance(data, dict) or not data.get("ok"):
        message = (data or {}).get("message") if isinstance(data, dict) else ""
        out.emit(data if isinstance(data, dict) else {"ok": False}, T("Отказано: ", "Refused: ") + str(message or status))
        return EXIT_REFUSED
    if data.get("danger") == "confirm":
        text = T(f"«{data.get('title')}» — подтвердите в окне MemuAI.", f"\"{data.get('title')}\" — confirm it in the MemuAI window.")
    else:
        text = T(f"Открыто в MemuAI: {data.get('title')}", f"Opened in MemuAI: {data.get('title')}")
    out.emit(data, text)
    return EXIT_OK

def build_link(route: str, params: Dict[str, Any]) -> str:
    try:
        from app import deeplinks
    except ImportError as exc:  # pragma: no cover - the exe always bundles it
        raise CliError("app.deeplinks is missing") from exc
    if deeplinks.get_route(route) is None:
        raise CliError(T(f"Неизвестный маршрут: {route} (список: memu link --list)", f"Unknown route: {route} (list: memu link --list)"), EXIT_USAGE)
    link = deeplinks.build(route, params)
    try:
        deeplinks.parse(link)
    except deeplinks.LinkError as exc:
        raise CliError((exc.en if LANG == "en" else exc.ru), EXIT_REFUSED) from exc
    return link

def cmd_link(argv: List[str], out: Out) -> int:
    opts, rest = take_opts(argv, ("--list",), ())
    if opts.get("list") or not rest:
        from app import deeplinks
        rows = [r.public(LANG) for r in deeplinks.routes() if r.danger != "block"]
        lines = [f"{r['name']:<18} {'⚠ ' if r['danger'] == 'confirm' or r['confirm_sometimes'] else '  '}{r['title']}"
                 + (f"   {r['example']}" if r['example'] else "") for r in rows]
        out.emit({"routes": rows}, "\n".join(lines) + "\n" + T("⚠ — спрашивает подтверждение в окне MemuAI", "⚠ — asks for confirmation in the MemuAI window"))
        return EXIT_OK
    link = build_link(rest[0], parse_kv(rest[1:]))
    out.emit({"ok": True, "link": link}, link)
    return EXIT_OK

def cmd_open(argv: List[str], out: Out) -> int:
    if not argv:
        raise CliError(T("Укажите ссылку memuai:// или маршрут", "Give a memuai:// link or a route"), EXIT_USAGE)
    target = argv[0]
    link = target if target.lower().startswith("memuai:") else build_link(target, parse_kv(argv[1:]))
    return dispatch_link(link, out)

def cmd_chat(argv: List[str], out: Out) -> int:
    if not argv or argv[0] not in ("new",):
        raise CliError(T("Использование: memu chat new [\"текст\"] [-m МОДЕЛЬ] [--send] [--temp]",
                         "Usage: memu chat new [\"text\"] [-m MODEL] [--send] [--temp]"), EXIT_USAGE)
    opts, rest = take_opts(argv[1:], ("--send", "--temp"), ("-m", "--model"))
    text = " ".join(rest).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read(8000).strip()
    params: Dict[str, Any] = {}
    if text:
        params["q"] = text
    model = opts.get("m") or opts.get("model")
    if model:
        params["model"] = model
    if opts.get("send"):
        params["send"] = True
    if opts.get("temp"):
        params["temp"] = True
    return dispatch_link(build_link("chat/new", params), out)

def default_model(token: str) -> str:
    if os.environ.get("MEMU_MODEL"):
        return os.environ["MEMU_MODEL"]
    try:
        prefs = json.loads((data_dir() / "memuai" / "preferences.json").read_text(encoding="utf-8"))
        if isinstance(prefs, dict) and prefs.get("default_model"):
            return str(prefs["default_model"])
    except (OSError, ValueError):
        pass
    models = list_models(token)
    if not models:
        raise CliError(T("В MemuAI Proxy нет моделей — добавьте аккаунт.", "MemuAI Proxy has no models — add an account."))
    return models[0]["id"]

def list_models(token: str) -> List[Dict[str, Any]]:
    status, data = http("GET", proxy_url() + "/v1/models", token=token, timeout=15)
    if status == 401:
        raise CliError(T("Proxy требует API-ключ: --token КЛЮЧ или MEMUAI_API_KEY.", "The Proxy needs an API key: --token KEY or MEMUAI_API_KEY."), EXIT_REFUSED)
    if status != 200 or not isinstance(data, dict):
        raise CliError(T(f"Proxy ответил {status}", f"The Proxy answered {status}"))
    return [m for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]

def cmd_models(argv: List[str], out: Out, token: str) -> int:
    models = list_models(token)
    out.emit({"models": models}, "\n".join(m["id"] for m in models) or T("(нет моделей)", "(no models)"))
    return EXIT_OK

def cmd_ask(argv: List[str], out: Out, token: str) -> int:
    opts, rest = take_opts(argv, ("--stream", "--no-stream"), ("-m", "--model", "--system", "--max-tokens"))
    text = " ".join(rest).strip()
    piped = ""
    if not sys.stdin.isatty():
        piped = sys.stdin.read(2_000_000)
    if piped.strip():
        text = (text + "\n\n" + piped.strip()) if text else piped.strip()
    if not text:
        raise CliError(T("Нет текста запроса: memu ask \"вопрос\" или через конвейер.", "No request text: memu ask \"question\" or pipe it in."), EXIT_USAGE)
    model = opts.get("m") or opts.get("model") or default_model(token)
    messages = []
    if opts.get("system"):
        messages.append({"role": "system", "content": opts["system"]})
    messages.append({"role": "user", "content": text})
    stream = bool(opts.get("stream")) and not out.json
    body: Dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
    if opts.get("max-tokens"):
        try:
            body["max_tokens"] = int(opts["max-tokens"])
        except ValueError:
            raise CliError("--max-tokens: число / a number", EXIT_USAGE)
    url = proxy_url() + "/v1/chat/completions"
    if stream:
        status, response = http("POST", url, body, token=token, timeout=900, stream=True)
        if status != 200 or isinstance(response, dict):
            return _ask_error(status, response, out)
        answer: List[str] = []
        with response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if chunk.get("error"):
                    sys.stdout.write("\n")
                    return _ask_error(500, chunk, out)
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        answer.append(piece)
                        sys.stdout.write(piece)
                        sys.stdout.flush()
        sys.stdout.write("\n")
        return EXIT_OK
    status, data = http("POST", url, body, token=token, timeout=900)
    if status != 200 or not isinstance(data, dict) or not data.get("choices"):
        return _ask_error(status, data, out)
    content = ((data["choices"][0] or {}).get("message") or {}).get("content") or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    out.emit({"ok": True, "model": data.get("model") or model, "content": content, "usage": data.get("usage")}, content)
    return EXIT_OK

def _ask_error(status: int, data: Any, out: Out) -> int:
    err = data.get("error") if isinstance(data, dict) else None
    message = (err.get("message") if isinstance(err, dict) else err) or str(status)
    out.emit({"ok": False, "status": status, "error": message}, "")
    sys.stderr.write(T("Ошибка модели: ", "Model error: ") + str(message)[:500] + "\n")
    return EXIT_REFUSED if status in (401, 403) else EXIT_ERROR

def cmd_status(argv: List[str], out: Out) -> int:
    app_url, px_url = memuai_url(), proxy_url()
    app_on = online(app_url, "/api/pg/ping") if app_url else False
    px_on = online(px_url, "/health", "proxy")
    links: Dict[str, Any] = {}
    if app_on:
        try:
            status, data = http("GET", app_url + "/api/pg/deeplink/settings?lang=" + LANG, timeout=5)
            if status == 200 and isinstance(data, dict):
                links = {"settings": data.get("settings"), "registration": data.get("registration")}
        except CliError:
            pass
    data = {"memuai": {"running": app_on, "url": app_url}, "proxy": {"running": px_on, "url": px_url},
            "data_dir": str(data_dir()), "links": links}
    yes, no = T("запущен", "running"), T("не запущен", "not running")
    text = [f"MemuAI:        {yes if app_on else no}{('  ' + app_url) if app_on else ''}",
            f"MemuAI Proxy:  {yes if px_on else no}{('  ' + px_url) if px_on else ''}",
            T("Данные:        ", "Data:          ") + str(data_dir())]
    if links.get("settings"):
        s = links["settings"]
        text.append(T("Ссылки memuai: ", "memuai links:  ") + (T("включены", "on") if s.get("enabled") else T("выключены", "off"))
                    + (T(", всегда спрашивать", ", always ask") if s.get("always_ask") else ""))
    out.emit(data, "\n".join(text))
    return EXIT_OK if (app_on or px_on) else EXIT_UNREACHABLE

def _tasks_from_file() -> List[Dict[str, Any]]:
    try:
        rows = json.loads((data_dir() / "memuai" / "schedules.json").read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []

def cmd_task(argv: List[str], out: Out) -> int:
    if not argv or argv[0] not in ("list", "run"):
        raise CliError(T("Использование: memu task list | memu task run <id> [--yes]", "Usage: memu task list | memu task run <id> [--yes]"), EXIT_USAGE)
    if argv[0] == "list":
        rows = []
        url = memuai_url()
        if url and online(url, "/api/pg/ping"):
            status, data = http("GET", url + "/api/plan/tasks?lang=" + LANG, timeout=15)
            if status == 200 and isinstance(data, dict):
                rows = data.get("tasks") or []
        if not rows:
            rows = _tasks_from_file()
        slim = [{"id": r.get("id"), "name": r.get("name") or r.get("title") or "", "enabled": r.get("enabled", True) is not False,
                 "next_run": r.get("next_run"), "schedule": r.get("schedule_text") or r.get("summary") or ""} for r in rows]
        lines = [f"{str(r['id']):<14} {'●' if r['enabled'] else '○'} {r['name']}" + (f"  — {r['schedule']}" if r['schedule'] else "") for r in slim]
        out.emit({"tasks": slim}, "\n".join(lines) or T("Задач нет.", "No tasks."))
        return EXIT_OK
    opts, rest = take_opts(argv[1:], ("--yes", "-y"), ())
    if not rest:
        raise CliError(T("Укажите id задачи (memu task list)", "Give a task id (memu task list)"), EXIT_USAGE)
    task_id = rest[0]
    if opts.get("yes") or opts.get("y"):

        url = need_memuai()
        status, data = http("POST", url + "/api/plan/run", {"id": task_id, "lang": LANG}, timeout=20)
        if status == 200 and isinstance(data, dict) and data.get("ok"):
            out.emit({"ok": True, "id": task_id}, T("Задача запущена.", "Task started."))
            return EXIT_OK
        out.emit({"ok": False, "status": status, "data": data}, T("Задача не запущена: ", "Task not started: ") + str((data or {}).get("error") or status))
        return EXIT_ERROR
    return dispatch_link(build_link("task/run", {"id": task_id}), out)

def cmd_inbox(argv: List[str], out: Out) -> int:
    opts, _ = take_opts(argv, ("--all",), ())
    url = need_memuai()
    status, data = http("GET", url + "/api/pg/inbox" + ("?status=all" if opts.get("all") else ""), timeout=15)
    if status != 200 or not isinstance(data, dict):
        raise CliError(T("«Входящие» недоступны", "The Inbox is unavailable"))
    items = data.get("items") or []
    lines = [f"{'•' if not it.get('read') else ' '} [{it.get('source', '')}] {it.get('title', '')}" for it in items[:100]]
    out.emit({"items": items, "counts": data.get("counts")}, "\n".join(lines) or T("Во «Входящих» пусто.", "The Inbox is empty."))
    return EXIT_OK

def cmd_search_files(argv: List[str], out: Out) -> int:
    opts, rest = take_opts(argv, (), ("--limit",))
    query = " ".join(rest).strip()
    if not query:
        raise CliError(T("Укажите запрос: memu search-files \"*.pdf\"", "Give a query: memu search-files \"*.pdf\""), EXIT_USAGE)
    try:
        limit = max(1, min(500, int(opts.get("limit") or 50)))
    except ValueError:
        raise CliError("--limit: число / a number", EXIT_USAGE)
    url = need_memuai()
    status, data = http("GET", url + "/api/fsindex/search?" + urllib.parse.urlencode({"q": query, "limit": limit}), timeout=30)
    if status == 404:
        raise CliError(T("Поиск файлов недоступен в этой версии MemuAI.", "File search is unavailable in this MemuAI version."))
    if not isinstance(data, dict):
        raise CliError(T("Поиск не ответил", "Search did not answer"))
    if data.get("code") == "disabled" or data.get("error") == "disabled":
        out.emit(data, T("Поиск файлов выключен (Быстрый доступ → Поиск файлов).", "File search is off (Quick Access → File search)."))
        return EXIT_REFUSED
    results = data.get("results") or []
    out.emit({"results": results, "query": query}, "\n".join(str(r.get("path")) for r in results) or T("Ничего не найдено.", "Nothing found."))
    return EXIT_OK

def cmd_note(argv: List[str], out: Out) -> int:
    if not argv:
        raise CliError(T("Укажите файл: memu note заметка.md", "Give a file: memu note notes.md"), EXIT_USAGE)
    files = [str(Path(a).expanduser().resolve()) for a in argv]
    for path in files:
        if path.startswith("\\\\"):
            raise CliError(T("Сетевые пути не открываются.", "Network paths are not opened."), EXIT_REFUSED)
    candidates: List[List[str]] = []
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        for folder in (here.parent / "MemuNote", here.parent.parent / "MemuNote", here):
            exe = folder / "MemuNote.exe"
            if exe.is_file():
                candidates.append([str(exe), *files])
    else:
        entry = Path(__file__).resolve().parent.parent / "memunote.py"
        if entry.is_file():
            candidates.append([sys.executable, str(entry), *files])
    for command in candidates:
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, start_new_session=os.name != "nt")
            out.emit({"ok": True, "files": files}, T("MemuNote открывается…", "Opening MemuNote…"))
            return EXIT_OK
        except OSError:
            continue

    url = memuai_url()
    if url and online(url, "/api/pg/ping"):
        status, data = http("POST", url + "/api/pg/memunote/open", {"files": files}, timeout=15)
        if status == 200 and isinstance(data, dict) and data.get("ok"):
            out.emit({"ok": True, "files": files}, T("MemuNote открывается…", "Opening MemuNote…"))
            return EXIT_OK
    raise CliError(T("MemuNote не найден (MemuAI → Настройки → MemuNote).", "MemuNote was not found (MemuAI → Settings → MemuNote)."))

def completion_script(shell: str) -> str:
    words = " ".join(COMMANDS)
    if shell == "bash":
        return f"""# memu bash completion: eval "$(memu completion bash)"
_memu() {{
  local cur prev; cur="${{COMP_WORDS[COMP_CWORD]}}"; prev="${{COMP_WORDS[COMP_CWORD-1]}}"
  case "$prev" in
    memu) COMPREPLY=( $(compgen -W "{words} --json --lang --help" -- "$cur") ); return;;
    task) COMPREPLY=( $(compgen -W "list run" -- "$cur") ); return;;
    chat) COMPREPLY=( $(compgen -W "new" -- "$cur") ); return;;
    completion) COMPREPLY=( $(compgen -W "bash zsh powershell" -- "$cur") ); return;;
    -m|--model) COMPREPLY=( $(compgen -W "$(memu models 2>/dev/null)" -- "$cur") ); return;;
  esac
  COMPREPLY=( $(compgen -W "--json --stream --send --temp --yes --all --limit -m" -- "$cur") )
}}
complete -F _memu memu
"""
    if shell == "zsh":
        return f"""#compdef memu
# memu zsh completion: source <(memu completion zsh)
_memu() {{
  local -a cmds; cmds=({words})
  if (( CURRENT == 2 )); then _describe 'command' cmds; return; fi
  case $words[2] in
    task) _values 'action' list run;;
    chat) _values 'action' new;;
    completion) _values 'shell' bash zsh powershell;;
    note) _files;;
  esac
}}
compdef _memu memu
"""
    if shell in ("powershell", "pwsh"):
        quoted = ", ".join(f"'{w}'" for w in COMMANDS)
        return f"""# memu PowerShell completion: memu completion powershell | Out-String | Invoke-Expression
Register-ArgumentCompleter -Native -CommandName memu, memu.exe -ScriptBlock {{
  param($wordToComplete, $commandAst, $cursorPosition)
  $tokens = $commandAst.CommandElements | ForEach-Object {{ $_.ToString() }}
  $list = @({quoted})
  if ($tokens.Count -ge 2 -and $tokens[1] -eq 'task') {{ $list = @('list', 'run') }}
  elseif ($tokens.Count -ge 2 -and $tokens[1] -eq 'chat') {{ $list = @('new') }}
  elseif ($tokens.Count -ge 2 -and $tokens[1] -eq 'completion') {{ $list = @('bash', 'zsh', 'powershell') }}
  elseif ($tokens.Count -ge 2) {{ $list = @('--json', '--stream', '--send', '--temp', '--yes', '--all', '--limit', '-m', '--lang') }}
  $list | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
    [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
  }}
}}
"""
    raise CliError(T("Оболочки: bash, zsh, powershell", "Shells: bash, zsh, powershell"), EXIT_USAGE)

def _version() -> str:
    try:
        from app.version import APP_VERSION
        return APP_VERSION
    except Exception:  # noqa: BLE001
        return "?"

def main(argv: Optional[Sequence[str]] = None) -> int:
    global LANG
    args = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    LANG = detect_lang(args)
    try:
        opts, rest = take_opts(args, ("--json", "--help", "-h", "--version", "-V"), ("--lang", "--token"))
    except CliError:

        opts, rest = {}, []
        skip = False
        for i, a in enumerate(args):
            if skip:
                skip = False
                continue
            if a in ("--json",):
                opts["json"] = True
            elif a in ("--lang", "--token"):
                opts[a[2:]] = args[i + 1] if i + 1 < len(args) else ""
                skip = True
            elif a.startswith(("--lang=", "--token=")):
                k, v = a[2:].split("=", 1)
                opts[k] = v
            else:
                rest.append(a)
    out = Out(bool(opts.get("json")))
    if opts.get("version") or opts.get("V") or (rest and rest[0] == "version"):
        out.emit({"version": _version()}, "memu " + _version())
        return EXIT_OK
    if not rest or opts.get("help") or opts.get("h") or rest[0] in ("help",):
        sys.stdout.write(HELP_EN if LANG == "en" else HELP_RU)
        return EXIT_OK if (not rest or rest[0] == "help" or opts.get("help") or opts.get("h")) else EXIT_USAGE
    command, tail = rest[0], rest[1:]
    if any(a in ("--help", "-h") for a in tail):
        sys.stdout.write(HELP_EN if LANG == "en" else HELP_RU)
        return EXIT_OK
    token = proxy_token(str(opts.get("token") or ""))
    try:
        if command == "ask":
            return cmd_ask(tail, out, token)
        if command == "models":
            return cmd_models(tail, out, token)
        if command == "chat":
            return cmd_chat(tail, out)
        if command == "open":
            return cmd_open(tail, out)
        if command == "link":
            return cmd_link(tail, out)
        if command == "status":
            return cmd_status(tail, out)
        if command == "task":
            return cmd_task(tail, out)
        if command == "inbox":
            return cmd_inbox(tail, out)
        if command == "search-files":
            return cmd_search_files(tail, out)
        if command == "note":
            return cmd_note(tail, out)
        if command == "completion":
            sys.stdout.write(completion_script(tail[0] if tail else ""))
            return EXIT_OK
        raise CliError(T(f"Неизвестная команда «{command}». Справка: memu --help", f"Unknown command \"{command}\". Help: memu --help"), EXIT_USAGE)
    except CliError as exc:
        if out.json:
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc), "exit": exc.code}, ensure_ascii=False) + "\n")
        sys.stderr.write("memu: " + str(exc) + "\n")
        return exc.code
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        return EXIT_OK
