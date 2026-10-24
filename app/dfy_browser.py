"""The DFY browser: a persistent Microsoft Edge profile driven over CDP.

Unlike Work's Browser (``automation_runtime.EdgeAutomation``), which is a
throw-away, network-locked profile for *reading* sites, DFY acts for the user:
cookies, logins and site storage persist in ``DATA_DIR/dfy_browser`` between
tasks, so "log in once, then book" works.  The profile is used by one task at a
time (``DFYManager`` serializes tasks), can run visibly or in the background,
and "Clear sessions" deletes it.

Safety that still applies here:
* only http(s) navigation; private network addresses are refused
  (``automation_runtime._safe_navigation_url``);
* downloads are denied; ``window.open``/``target=_blank`` stay in the same tab;
* with a DFY proxy (Round 30, ``app/dfy_proxy.py``) every request of the browser goes through
  it (``extra_args``), and navigation does not resolve host names locally (no DNS leak);
* fields filled from the secret vault get ``data-memu-secret`` and are drawn as
  discs (``-webkit-text-security``) before every screenshot, and their value is
  removed from the page state sent to the model.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR

MAX_ELEMENTS = 300
#: Round 34: inputs with a picker that ignore typed characters, and the value format the browser expects.
_PICKER_INPUTS = ("time", "date", "datetime-local", "month", "week", "color", "range")
_PICKER_FORMATS = {"time": "ЧЧ:ММ (19:30)", "date": "ГГГГ-ММ-ДД (2026-12-31)", "datetime-local": "ГГГГ-ММ-ДДTЧЧ:ММ",
                   "month": "ГГГГ-ММ", "week": "ГГГГ-W05", "color": "#rrggbb", "range": "число"}

_PICKER_FORMATS_EN = {"ЧЧ:ММ (19:30)": "HH:MM (19:30)", "ГГГГ-ММ-ДД (2026-12-31)": "YYYY-MM-DD (2026-12-31)",
                      "ГГГГ-ММ-ДДTЧЧ:ММ": "YYYY-MM-DDTHH:MM", "ГГГГ-ММ": "YYYY-MM", "ГГГГ-W05": "YYYY-W05", "число": "a number"}
#: Round 34 (r34-quick-agentic-2): the browser's messages reach the task card / the step list — in an English UI they
#: were Russian. The browser raises them in Russian (the model and the tests read those); ``in_language`` gives the
#: text the user reads.
_ERRORS_EN = (
    (r"Некорректный адрес", "Invalid address"),
    (r"DFY открывает только HTTP\(S\)-адреса", "DFY opens only HTTP(S) addresses"),
    (r"DFY не открывает локальные адреса \(localhost\)", "DFY does not open local addresses (localhost)"),
    (r"DFY не открывает локальные и приватные сетевые адреса", "DFY does not open local or private network addresses"),
    (r"Учётные данные нельзя помещать в адрес сайта", "Credentials cannot be put into a site address"),
    (r"Браузер DFY не запустился\. Если профиль DFY открыт в другом окне Edge — закройте его или очистите сессии DFY в настройках",
     "The DFY browser did not start. If the DFY profile is open in another Edge window, close it or clear the DFY sessions in Settings"),
    (r"Не удалось подключиться к браузеру DFY", "Could not connect to the DFY browser"),
    (r"Соединение с браузером DFY закрыто", "The connection to the DFY browser closed"),
    (r"Браузер DFY не запущен", "The DFY browser is not running"),
    (r"Ошибка скрипта на странице", "A script error on the page"),
    (r"Не удалось удалить профиль DFY: файлы заняты другим процессом Edge", "Could not delete the DFY profile: another Edge process holds its files"),
    (r"Сайт не открылся: (.*)", "The site did not open: \\1"),
    (r"Элемент страницы изменился; нужен новый снимок", "The page element changed; a new screenshot is needed"),
    (r"В этой точке ничего нет", "There is nothing at this point"),
    (r"Точка вне видимой части страницы", "The point is outside the visible part of the page"),
    (r"Нет активного поля: сначала нажмите на поле \(click или click_xy\)", "No focused field: click the field first (click or click_xy)"),
    (r"Неподдерживаемая клавиша", "Unsupported key"),
    (r"Это не выпадающий список или он изменился", "This is not a drop-down list, or it changed"),
    (r"В списке нет такого варианта", "The list has no such option"),
    (r"Вокруг этого элемента нет прокручиваемой области", "There is no scrollable area around this element"),
    # the manager's answers to the card's buttons (dfy.py → dfy_api.py), shown as a toast
    (r"Задача DFY не найдена|Задача не найдена", "The DFY task was not found"),
    (r"Задача сейчас не ждёт ответа", "The task is not waiting for an answer now"),
    (r"Этот вопрос уже неактуален — посмотрите новый", "This question is out of date — see the new one"),
    (r"Похоже на номер карты\. Его нельзя отправлять подсказкой", "This looks like a card number. It cannot be sent as a hint"),
    (r"Нажмите «Перехватить управление» или «Пропустить»", "Press «Take control» or «Skip»"),
    (r"Нужно подтвердить или отклонить", "Approve or decline"),
    (r"Введите код", "Enter the code"),
    (r"Секрет не найден в хранилище", "The secret is not in the vault"),
    (r"Заполните все поля формы", "Fill in all the fields of the form"),
    (r"Нужно заполнить форму или выбрать сохранённые данные", "Fill in the form or pick saved data"),
    (r"Пустой ответ", "The answer is empty"),
    (r"Похоже на номер карты\. Его нельзя отправлять обычным ответом — DFY запросит карту через защищённую форму",
     "This looks like a card number. It cannot be sent as a plain answer — DFY will ask for the card in a secure form"),
    (r"Не указана задача DFY", "The DFY task is empty"),
    (r"Нельзя очищать сессии во время задачи DFY", "Sessions cannot be cleared while a DFY task runs"),
)


def in_language(message: str, lang: str) -> str:
    """A browser message in the UI language (``lang`` = "en" translates the known Russian ones; others pass as is)."""
    text = str(message or "")
    if lang != "en":
        return text
    m = re.match(r"^Поле типа «(.+?)» не приняло «(.*)»: нужен формат (.+)$", text, re.S)
    if m:
        return f"The «{m.group(1)}» field did not accept «{m.group(2)}»: the format is {_PICKER_FORMATS_EN.get(m.group(3), m.group(3))}"
    for pattern, english in _ERRORS_EN:
        hit = re.match("^" + pattern + "$", text, re.S)
        if hit:
            return hit.expand(english)
    return text


def picker_value(kind: str, text: str) -> str:
    """What a person writes («7:30 pm», «31.12.2026», «19.30») → the value an ``<input type=kind>`` accepts."""
    raw = str(text or "").strip()
    if kind == "time":
        m = re.match(r"^(\d{1,2})[:.h ]?(\d{2})?(?::(\d{2}))?\s*([ap]\.?m\.?)?$", raw, re.I)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            ampm = (m.group(4) or "").lower().replace(".", "")
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if hour < 24 and minute < 60:
                return f"{hour:02d}:{minute:02d}" + (f":{m.group(3)}" if m.group(3) else "")
        return raw
    if kind in ("date", "datetime-local"):
        m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:[ T,]+(\d{1,2})[:.](\d{2}))?$", raw)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
            date = f"{year}-{month:02d}-{day:02d}"
            if kind == "date":
                return date
            return f"{date}T{int(m.group(4) or 0):02d}:{m.group(5) or '00'}"
        if kind == "datetime-local":
            return raw.replace(" ", "T", 1)
        return raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", raw) else raw
    if kind == "color" and re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        return "#" + raw
    return raw


def url_blocked_reason(url: str) -> str:
    """Cheap (no DNS) check of a URL the DFY page ended up on: ``""`` when allowed.

    Refuses non-http(s) schemes (file:, chrome:, edge:, javascript:, data: …) and loopback /
    private / link-local literal hosts, including ``localhost``. ``about:blank`` is allowed."""
    import ipaddress
    import urllib.parse
    raw = str(url or "").strip()
    if not raw or raw.lower() in ("about:blank", "about:srcdoc"):
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return "Некорректный адрес"
    if parsed.scheme.lower() not in ("http", "https"):
        return "DFY открывает только HTTP(S)-адреса"
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return "Некорректный адрес"
    if host == "localhost" or host.endswith(".localhost"):
        return "DFY не открывает локальные адреса (localhost)"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if not ip.is_global:
        return "DFY не открывает локальные и приватные сетевые адреса"
    return ""

_INIT_SCRIPT = r"""
(() => {
  try {
    const go = (u) => { try { if (u) location.href = new URL(String(u), location.href).href; } catch (e) {} return null; };
    Object.defineProperty(window, 'open', { value: go, writable: false, configurable: false });
    document.addEventListener('click', (e) => {
      const a = e.target && e.target.closest && e.target.closest('a[target]');
      if (a) a.target = '_self';
    }, true);
    document.addEventListener('submit', (e) => { if (e.target && e.target.target) e.target.target = '_self'; }, true);
  } catch (e) {}
  const style = () => {
    if (document.getElementById('memu-dfy-mask')) return;
    const s = document.createElement('style'); s.id = 'memu-dfy-mask';
    s.textContent = '[data-memu-secret]{-webkit-text-security:disc!important;color:transparent!important;text-shadow:0 0 0 #555!important}' +
      '[data-memu-secret-frame]{filter:blur(8px)!important}';
    (document.head || document.documentElement).appendChild(s);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', style); else style();
})();
"""

# Finds an element marked by the last snapshot, also inside same-origin frames, and maps its
# box to the top-level viewport. Cross-origin frames (payment widgets) are not readable from the
# page: the model clicks them by screenshot coordinates (click_xy) and types into the focused field.
_FIND_JS = r"""
const __dfyFind = (id) => {
  const walk = (doc, ox, oy) => {
    const e = doc.querySelector('[data-memu-dfy-id="' + id + '"]');
    if (e) return {e, ox, oy};
    for (const f of doc.querySelectorAll('iframe,frame')) {
      let inner = null; try { inner = f.contentDocument; } catch (_) {}
      if (!inner) continue;
      const r = f.getBoundingClientRect();
      const found = walk(inner, ox + r.left + (f.clientLeft || 0), oy + r.top + (f.clientTop || 0));
      if (found) return found;
    }
    return null;
  };
  return walk(document, 0, 0);
};
const __dfyFocused = () => {
  let e = document.activeElement, frame = null;
  while (e && (e.tagName === 'IFRAME' || e.tagName === 'FRAME')) {
    let inner = null; try { inner = e.contentDocument; } catch (_) {}
    if (!inner) { frame = e; break; }
    e = inner.activeElement;
  }
  return {e: frame ? null : e, frame};
};
"""

_SNAPSHOT_SCRIPT = r"""(() => {
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=checkbox],[role=radio],[role=option],[role=tab],[role=menuitem],[role=switch],[role=combobox],[contenteditable=true],[contenteditable=""],label[for],summary';
  const W = innerWidth, H = innerHeight;
  const elements = [], frames = []; let n = 0;
  const labelOf = el => {
    if (el.labels && el.labels.length) return [...el.labels].map(l => l.innerText).join(' ');
    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.title || el.name || '';
  };
  const scan = (doc, ox, oy, depth) => {
    const win = doc.defaultView || window;
    doc.querySelectorAll('[data-memu-dfy-id]').forEach(el => el.removeAttribute('data-memu-dfy-id'));
    const visible = el => { const r = el.getBoundingClientRect(), s = win.getComputedStyle(el);
      return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' &&
        r.bottom + oy > -40 && r.top + oy < H + 400; };
    for (const el of doc.querySelectorAll(sel)) {
      if (n >= %MAX%) break;
      if (!visible(el)) continue;
      n += 1; el.setAttribute('data-memu-dfy-id', String(n));
      const r = el.getBoundingClientRect();
      const tag = el.tagName.toLowerCase();
      const secret = el.hasAttribute('data-memu-secret');
      const sensitive = /password|cc-|card|cvc|cvv|one-time-code|otp/i.test((el.type||'') + ' ' + (el.autocomplete||'') + ' ' + (el.name||''));
      const item = { id: n, tag, type: el.type || el.getAttribute('role') || '',
        text: (tag === 'input' || tag === 'textarea' ? '' : (el.innerText || '')).trim().replace(/\s+/g, ' ').slice(0, 160),
        label: String(labelOf(el)).trim().replace(/\s+/g, ' ').slice(0, 120),
        box: [Math.round(r.x + ox), Math.round(r.y + oy), Math.round(r.width), Math.round(r.height)] };
      if (depth) item.frame = depth;
      if (el.href) item.href = String(el.href).slice(0, 300);
      if (el.disabled) item.disabled = true;
      if (el.checked) item.checked = true;
      if (el.required) item.required = true;
      if ('value' in el && tag !== 'button') item.value = (secret || sensitive || el.type === 'password') ? (el.value ? '••••' : '') : String(el.value || '').slice(0, 120);
      if (secret) item.filled_from_vault = true;
      if (tag === 'select') item.options = [...el.options].slice(0, 40).map(o => ({ value: o.value.slice(0, 60), text: o.text.trim().slice(0, 60), selected: o.selected }));
      if (el.autocomplete && el.autocomplete !== 'off') item.autocomplete = el.autocomplete;
      if (el.maxLength > 0 && el.maxLength < 64) item.maxlength = el.maxLength;
      elements.push(item);
    }
    if (depth >= 3) return;
    for (const f of doc.querySelectorAll('iframe,frame')) {
      const r = f.getBoundingClientRect();
      if (r.width < 20 || r.height < 12) continue;
      const box = [Math.round(r.x + ox), Math.round(r.y + oy), Math.round(r.width), Math.round(r.height)];
      let inner = null; try { inner = f.contentDocument; } catch (_) {}
      if (inner && inner.body) { scan(inner, ox + r.left + (f.clientLeft || 0), oy + r.top + (f.clientTop || 0), depth + 1); continue; }
      let host = ''; try { host = new URL(f.src, location.href).host; } catch (_) {}
      if (frames.length < 12) frames.push({ box, host, title: (f.title || f.name || '').slice(0, 80), cross_origin: true,
        filled_from_vault: f.hasAttribute('data-memu-secret-frame') || undefined });
    }
  };
  scan(document, 0, 0, 0);
  const state = { url: location.href, title: document.title,
    text: (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 9000),
    elements, scroll: { y: Math.round(scrollY), maxY: Math.max(0, document.documentElement.scrollHeight - H) },
    viewport: { width: W, height: H, dpr: devicePixelRatio || 1 } };
  if (frames.length) state.frames = frames;
  return JSON.stringify(state);
})()""".replace("%MAX%", str(MAX_ELEMENTS))


def jpeg_size(data_b64: str) -> Optional[tuple]:
    """``(width, height)`` from a base64 JPEG's SOF marker (``None`` if it cannot be read)."""
    import base64
    try:
        data = base64.b64decode(data_b64[:200_000] + "=" * (-len(data_b64[:200_000]) % 4))
    except (ValueError, TypeError):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return (width, height) if width and height else None
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        index += 2 + int.from_bytes(data[index + 2:index + 4], "big")
    return None


def proxied_navigation_url(value: str) -> str:
    """``_safe_navigation_url`` without the DNS lookup, for a browser behind the DFY proxy.

    Resolving the host locally would leak every visited site to the local DNS server; behind a
    proxy the name is resolved by the proxy, so a private address can only be the proxy's own
    network, not the user's. Literal private hosts are still refused by ``url_blocked_reason``."""
    import urllib.parse
    raw = str(value or "").strip()[:4000]
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("DFY открывает только HTTP(S)-адреса")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Учётные данные нельзя помещать в адрес сайта")
    return urllib.parse.urlunsplit(parsed)


def end_profile_orphans(profile: Path) -> int:
    """End browser processes left over from an earlier MemuAI run that still use ``profile`` (``--user-data-dir``).

    Only processes started with exactly this MemuAI-owned profile and not by this MemuAI process are touched.
    Returns how many were ended; 0 without the optional ``psutil``."""
    try:
        import psutil  # type: ignore
    except Exception:
        return 0
    wanted = "--user-data-dir=" + str(profile)
    try:
        mine = {proc.pid for proc in psutil.Process().children(recursive=True)} | {os.getpid()}
    except Exception:
        mine = {os.getpid()}
    victims = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = proc.info.get("cmdline") or []
            if proc.info["pid"] in mine or wanted not in cmd:
                continue
            victims.append(proc)
        except Exception:
            continue
    for proc in victims:
        try:
            proc.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(victims, timeout=4) if victims else ([], [])
    for proc in alive:
        try:
            proc.kill()
        except Exception:
            pass
    if victims:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"):
            try:
                (Path(profile) / name).unlink()
            except OSError:
                pass
    return len(victims)


def profile_dir() -> Path:
    return Path(DATA_DIR) / "dfy_browser"


class DFYBrowser:
    """Minimal CDP driver over one page target of a persistent Edge profile."""

    def __init__(self, *, visible: bool = False, profile: Optional[Path] = None,
                 extra_args: Optional[List[str]] = None, proxied: bool = False) -> None:
        self.visible = bool(visible)
        self.profile = Path(profile) if profile else profile_dir()
        # Round 30: launch flags of the DFY proxy; with a proxy the host name is resolved by the
        # proxy only — navigate() must not look it up through the local resolver.
        self.extra_args = [str(arg) for arg in extra_args or [] if str(arg).startswith("--")]
        self.proxied = bool(proxied)
        self._listeners: Dict[str, List[Any]] = {}   # CDP event → callbacks (screencast of the takeover)
        self.process: Optional[subprocess.Popen] = None
        self.socket: Any = None
        self._receiver: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._counter = 0
        self._lock = asyncio.Lock()
        self.port = 0
        self.target_id = ""
        self._shot_scale = 1.0   # screenshot pixels per CSS pixel (DPI scaling of a visible window)
        self.dialogs: List[Dict[str, str]] = []   # JS dialogs answered since the last snapshot

    # ----------------------------------------------------------- lifecycle --
    @property
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None and self.socket is not None)

    async def start(self, _retry: bool = False) -> None:
        if self.running:
            return
        await self.close()
        from .automation_runtime import _edge_executable
        self.profile.mkdir(parents=True, exist_ok=True)
        port_file = self.profile / "DevToolsActivePort"
        try:
            port_file.unlink()
        except OSError:
            pass
        command = [_edge_executable(), "--remote-debugging-port=0",
                   f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                   "--disable-sync", "--disable-features=msEdgeSidebarV2,msUndersideButton",
                   "--window-size=1280,860", *self.extra_args,
                   *(arg for arg in os.environ.get("MEMUAI_BROWSER_ARGS", "").split() if arg.startswith("--")),
                   "about:blank"]
        if not self.visible:
            command.insert(1, "--headless=new")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" and not self.visible else 0
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=flags)
        port = 0
        for _ in range(100):
            if self.process.poll() is not None:
                break
            try:
                port = int(port_file.read_text("ascii").splitlines()[0])
                break
            except (OSError, ValueError, IndexError):
                await asyncio.sleep(0.1)
        if not port:
            await self.close()
            # Round 34: after MemuAI crashed or was killed, its headless Edge lives on and holds the profile — every
            # later DFY task failed here («the DFY profile is open in another Edge window») until a reboot; «Clear
            # sessions» could not delete the locked profile either. Such a leftover is ended and the start retried.
            if not _retry and await asyncio.to_thread(end_profile_orphans, self.profile):
                await self.start(_retry=True)
                return
            raise RuntimeError("Браузер DFY не запустился. Если профиль DFY открыт в другом окне Edge — "
                               "закройте его или очистите сессии DFY в настройках")
        await self._attach(port)

    async def _attach(self, port: int) -> None:
        """Connect to the DevTools endpoint on ``127.0.0.1:port`` and prepare the page.

        ``port`` is the browser's own port or, for a browser on a remote machine, the local end of
        the agent's CDP tunnel (app/dfy_remote_browser.py) — the WebSocket URL is rebuilt on it."""
        from .automation_runtime import _connect_cdp, _read_json_url
        target = None
        for _ in range(60):
            try:
                targets = await asyncio.to_thread(_read_json_url, f"http://127.0.0.1:{port}/json/list")
                target = next((t for t in targets or [] if t.get("type") == "page"
                               and t.get("webSocketDebuggerUrl")), None)
                if target:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        if not target:
            await self.close()
            raise RuntimeError("Не удалось подключиться к браузеру DFY")
        self.port, self.target_id = port, str(target.get("id") or "")
        path = "/" + str(target["webSocketDebuggerUrl"]).split("://", 1)[-1].split("/", 1)[-1]
        self.socket = await _connect_cdp(f"ws://127.0.0.1:{port}{path}", max_size=32 * 1024 * 1024)
        self._receiver = asyncio.create_task(self._receive(self.socket))
        await self.command("Page.enable")
        await self.command("Runtime.enable")
        await self.command("Page.addScriptToEvaluateOnNewDocument", {"source": _INIT_SCRIPT})
        try:
            await self.command("Browser.setDownloadBehavior", {"behavior": "deny"})
        except RuntimeError:
            try:
                await self.command("Page.setDownloadBehavior", {"behavior": "deny"})
            except RuntimeError:
                pass
        if not self.visible:
            await self.command("Emulation.setDeviceMetricsOverride", {
                "width": 1280, "height": 820, "deviceScaleFactor": 1, "mobile": False})

    async def _receive(self, socket_value: Any) -> None:
        try:
            async for raw in socket_value:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if message.get("method") == "Page.javascriptDialogOpening":
                    self._on_dialog(message.get("params") or {})
                    continue
                if message.get("method") and "id" not in message:
                    for callback in list(self._listeners.get(message["method"], ())):
                        try:
                            callback(message.get("params") or {})
                        except Exception:
                            pass
                    continue
                future = self._pending.get(message.get("id"))
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(RuntimeError("Соединение с браузером DFY закрыто"))

    def on(self, method: str, callback: Any) -> Any:
        """Subscribe to a CDP event of the page (``Page.screencastFrame``…); returns the unsubscribe function."""
        self._listeners.setdefault(method, []).append(callback)

        def off() -> None:
            try:
                self._listeners.get(method, []).remove(callback)
            except ValueError:
                pass
        return off

    def _on_dialog(self, params: Dict[str, Any]) -> None:
        """alert/confirm/prompt/beforeunload block every script call (snapshot, click) until answered.

        Nothing irreversible is ever accepted on the model's behalf: confirm() and prompt() are
        dismissed (the safe answer), alert() and «leave this page?» are accepted. The text is shown
        to the model in the next snapshot so it can react (e.g. ask the user)."""
        kind = str(params.get("type") or "alert")
        accept = kind in ("alert", "beforeunload")
        self.dialogs.append({"type": kind, "message": str(params.get("message") or "")[:300],
                             "answered": "accepted" if accept else "dismissed"})
        del self.dialogs[:-5]
        task = asyncio.ensure_future(self.command("Page.handleJavaScriptDialog", {"accept": accept}, timeout=5))
        task.add_done_callback(lambda done: done.cancelled() or done.exception())

    async def command(self, method: str, params: Optional[Dict[str, Any]] = None,
                      timeout: float = 30) -> Dict[str, Any]:
        if self.socket is None:
            raise RuntimeError("Браузер DFY не запущен")
        async with self._lock:
            self._counter += 1
            command_id = self._counter
            future = asyncio.get_running_loop().create_future()
            self._pending[command_id] = future
            await self.socket.send(json.dumps({"id": command_id, "method": method, "params": params or {}}))
        try:
            message = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(command_id, None)
        if message.get("error"):
            raise RuntimeError(str(message["error"].get("message") or message["error"]))
        return message.get("result") or {}

    async def evaluate(self, expression: str) -> Any:
        result = await self.command("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                          "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise RuntimeError("Ошибка скрипта на странице")
        return (result.get("result") or {}).get("value")

    async def close(self) -> None:
        socket_value, self.socket = self.socket, None
        receiver, self._receiver = self._receiver, None
        process, self.process = self.process, None
        if receiver and not receiver.done():
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
        if socket_value is not None:
            try:
                await socket_value.close()
            except Exception:
                pass
        if process and process.poll() is None:
            try:
                process.terminate()
                await asyncio.to_thread(process.wait, 5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    async def clear_profile(self) -> None:
        """Forget every cookie/session: close the browser and delete the profile."""
        await self.close()
        for _attempt in range(8):
            await asyncio.to_thread(shutil.rmtree, self.profile, True)
            if not self.profile.exists():
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("Не удалось удалить профиль DFY: файлы заняты другим процессом Edge")

    # -------------------------------------------------------------- state ---
    async def snapshot(self) -> Dict[str, Any]:
        await self.start()
        state: Dict[str, Any] = {}
        for attempt in range(2):
            try:
                state = json.loads(await self.evaluate(_SNAPSHOT_SCRIPT) or "{}")
                break
            except Exception:
                # Usually the page is between two documents (navigation in progress).
                state = {"url": await self.current_url(), "title": "", "text": "", "elements": [],
                         "note": "the page is still loading or could not be read"}
                if attempt == 0:
                    await self.wait_ready(4)
        try:
            shot = await self.command("Page.captureScreenshot", {"format": "jpeg", "quality": 70})
            state["screenshot"] = shot.get("data") or ""
        except Exception:
            state["screenshot"] = ""
        if self.dialogs:
            state["dialogs"], self.dialogs = self.dialogs, []
        size = jpeg_size(state["screenshot"]) if state["screenshot"] else None
        width = (state.get("viewport") or {}).get("width") or 0
        if size and width:
            self._shot_scale = max(0.25, min(4.0, size[0] / width))
            state["screenshot_size"] = {"width": size[0], "height": size[1],
                                        "pixels_per_css_px": round(self._shot_scale, 3)}
        return state

    async def current_url(self) -> str:
        try:
            return str(await self.evaluate("location.href") or "")
        except Exception:
            return ""

    async def wait_ready(self, timeout: float = 12.0) -> None:
        deadline = time.monotonic() + timeout
        await asyncio.sleep(0.4)
        while time.monotonic() < deadline:
            try:
                if await self.evaluate("document.readyState") == "complete":
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        await asyncio.sleep(0.5)

    # ------------------------------------------------------------ actions ---
    async def navigate(self, url: str) -> str:
        if self.proxied:
            safe = proxied_navigation_url(url)
        else:
            from .automation_runtime import _safe_navigation_url
            safe = await asyncio.to_thread(_safe_navigation_url, url)
        # Work's helper allows localhost (for Work projects); DFY acts on the open web only —
        # localhost would expose MemuAI's own API (chats, settings) to a prompt-injected page.
        blocked = url_blocked_reason(safe)
        if blocked:
            raise ValueError(blocked)
        await self.start()
        result = await self.command("Page.navigate", {"url": safe})
        error = str((result or {}).get("errorText") or "")
        if error and error != "net::ERR_ABORTED":
            # DNS/connection/TLS failure: the tab shows chrome-error://, but the step used to be
            # reported as «Открыт …» and the model kept re-opening the same address.
            raise RuntimeError(f"Сайт не открылся: {error}")
        await self.wait_ready()
        return safe

    async def back(self) -> None:
        await self.evaluate("history.back()")
        await self.wait_ready(6)

    async def element_info(self, element: int) -> Dict[str, Any]:
        value = await self.evaluate(
            "(() => {" + _FIND_JS + "const f = __dfyFind(%d); if (!f) return null; const e = f.e;"
            " return { tag: e.tagName.toLowerCase(), type: e.type || '', text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 160),"
            " form: !!e.form, submit: e.type === 'submit' }; })()" % int(element))
        if not value:
            raise ValueError("Элемент страницы изменился; нужен новый снимок")
        return value

    async def point_info(self, x: float, y: float) -> Dict[str, Any]:
        """What a click at screenshot pixel (x, y) would hit (text ``""`` inside a cross-origin frame)."""
        cx, cy = x / self._shot_scale, y / self._shot_scale
        value = await self.evaluate(
            "(() => { let x = %f, y = %f, doc = document, e = doc.elementFromPoint(x, y);"
            " while (e && (e.tagName === 'IFRAME' || e.tagName === 'FRAME')) { let inner = null; try { inner = e.contentDocument; } catch (_) {}"
            "  if (!inner) return { text: '', frame: true }; const r = e.getBoundingClientRect(); x -= r.left; y -= r.top; e = inner.elementFromPoint(x, y); }"
            " if (!e) return null; const b = e.closest('button,a,[role=button],input[type=submit],input[type=button]') || e;"
            " return { text: (b.innerText || b.value || b.getAttribute('aria-label') || '').trim().slice(0, 160) }; })()" % (cx, cy))
        if value is None:
            raise ValueError("В этой точке ничего нет")
        return value

    async def submit_info(self, element: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The submit button that Enter in this field (or the focused one) would press; ``None`` outside a form."""
        target = "(__dfyFind(%d) || {}).e" % int(element) if element else "__dfyFocused().e"
        return await self.evaluate(
            "(() => {" + _FIND_JS + "const e = " + target + "; const form = e && (e.form || (e.closest && e.closest('form')));"
            " if (!form) return null; const b = form.querySelector('button[type=submit],input[type=submit],button:not([type])');"
            " return { text: b ? (b.innerText || b.value || b.getAttribute('aria-label') || '').trim().slice(0, 160) : '' }; })()")

    async def _mouse_click(self, x: float, y: float) -> None:
        # A real mouse click (not el.click()) so sites that check isTrusted work.
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.command("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                                             "button": "left", "clickCount": 1})
        await self.wait_ready(8)
        await self.adopt_popups()

    async def click(self, element: int) -> str:
        value = await self.evaluate(
            "(() => {" + _FIND_JS + "const f = __dfyFind(%d); if (!f) return 'missing';"
            " f.e.scrollIntoView({block: 'center', inline: 'center'}); const g = __dfyFind(%d) || f; const r = g.e.getBoundingClientRect();"
            " return JSON.stringify({x: g.ox + r.x + r.width / 2, y: g.oy + r.y + r.height / 2}); })()" % (int(element), int(element)))
        if value == "missing" or not value:
            raise ValueError("Элемент страницы изменился; нужен новый снимок")
        point = json.loads(value)
        await self._mouse_click(point["x"], point["y"])
        return "clicked"

    async def click_xy(self, x: float, y: float) -> str:
        """Click a point of the last screenshot (converted to CSS pixels for DPI-scaled windows)."""
        cx, cy = x / self._shot_scale, y / self._shot_scale
        size = await self.evaluate("[innerWidth, innerHeight]") or [0, 0]
        if not (0 <= cx < (size[0] or 1e9) and 0 <= cy < (size[1] or 1e9)):
            raise ValueError("Точка вне видимой части страницы")
        await self._mouse_click(cx, cy)
        return f"clicked at {x:g},{y:g}"

    async def type(self, element: Optional[int], text: str, *, secret: bool = False, submit: bool = False) -> str:
        mark = "e.setAttribute('data-memu-secret', '1');" if secret else ""
        find = ("const f = __dfyFind(%d); if (!f) return 'missing'; const e = f.e;"
                " e.scrollIntoView({block: 'center'}); e.focus();" % int(element)) if element else \
            ("const g = __dfyFocused(); if (g.frame) { %s return 'frame'; } const e = g.e;"
             " if (!e || e === document.body || e === document.documentElement) return 'nofocus';"
             % ("g.frame.setAttribute('data-memu-secret-frame', '1');" if secret else ""))
        value = await self.evaluate(
            "(() => {" + _FIND_JS + find + mark +
            " if ('value' in e) { const p = Object.getPrototypeOf(e); const s = Object.getOwnPropertyDescriptor(p, 'value');"
            " if (s && s.set) s.set.call(e, ''); else e.value = ''; e.dispatchEvent(new Event('input', {bubbles: true})); }"
            " else if (e.isContentEditable) { e.textContent = ''; }"
            " return 'ok:' + (e.tagName === 'INPUT' ? String(e.type || '').toLowerCase() : ''); })()")
        if value == "nofocus":
            raise ValueError("Нет активного поля: сначала нажмите на поле (click или click_xy)")
        if value != "frame" and not str(value or "").startswith("ok:"):
            raise ValueError("Элемент страницы изменился; нужен новый снимок")
        found = ("const e = (__dfyFind(%d) || {}).e;" % int(element) if element else "const e = __dfyFocused().e;")
        kind = str(value)[3:] if value != "frame" else ""
        if kind in _PICKER_INPUTS and not secret:
            # Round 34: <input type=time|date|…> ignores typed characters (Input.insertText) — the field stayed empty,
            # the step still said «typed» and the model typed «19:30» again until the stuck pause. Such fields take
            # their value in the browser's own format, set directly.
            wanted = picker_value(kind, text)
            got = await self.evaluate(
                "(() => {" + _FIND_JS + found + " if (!e) return null; const s = Object.getOwnPropertyDescriptor("
                "HTMLInputElement.prototype, 'value'); s.set.call(e, %s); e.dispatchEvent(new Event('input', {bubbles: true}));"
                " e.dispatchEvent(new Event('change', {bubbles: true})); return e.value; })()" % json.dumps(wanted))
            if not got:
                raise ValueError(f"Поле типа «{kind}» не приняло «{text[:40]}»: нужен формат {_PICKER_FORMATS[kind]}")
            if submit:
                await self.key("Enter")
            return f"set the {kind} field to {got}"
        await self.command("Input.insertText", {"text": text})
        stayed_empty = False
        if value != "frame":
            stayed_empty = bool(await self.evaluate(
                "(() => {" + _FIND_JS + found + " if (!e) return false; e.dispatchEvent(new Event('change', {bubbles: true}));"
                " return ('value' in e) ? e.value === '' : (e.isContentEditable ? !e.textContent : false); })()")) and bool(text)
        if submit:
            await self.key("Enter")
        if stayed_empty:
            # the page refused the characters (a mask, a read-only widget): say so instead of «typed», or the model
            # repeats the same typing on an unchanged page
            return "typed, but the field is still EMPTY — the page did not accept the text; click the field or use another way"
        return "typed into a frame field" if value == "frame" else "typed"

    _KEYS = {"Enter": (13, "\r"), "Tab": (9, ""), "Escape": (27, ""), "Space": (32, " "), "ArrowDown": (40, ""),
             "ArrowUp": (38, ""), "ArrowLeft": (37, ""), "ArrowRight": (39, ""), "PageDown": (34, ""),
             "PageUp": (33, ""), "Home": (36, ""), "End": (35, ""), "Backspace": (8, ""), "Delete": (46, "")}

    async def key(self, name: str) -> str:
        if name not in self._KEYS:
            raise ValueError("Неподдерживаемая клавиша")
        code, text = self._KEYS[name]
        params = {"key": " " if name == "Space" else name, "code": name, "windowsVirtualKeyCode": code,
                  "nativeVirtualKeyCode": code}
        await self.command("Input.dispatchKeyEvent", {"type": "keyDown", **params, **({"text": text} if text else {})})
        await self.command("Input.dispatchKeyEvent", {"type": "keyUp", **params})
        await self.wait_ready(6)
        await self.adopt_popups()
        return f"pressed {name}"

    async def select(self, element: int, value: str) -> str:
        payload = json.dumps(str(value), ensure_ascii=False)
        result = await self.evaluate(
            "(() => {" + _FIND_JS + "const f = __dfyFind(%d); const e = f && f.e; if (!e || e.tagName !== 'SELECT') return 'missing';"
            " const v = %s; const o = [...e.options].find(o => o.value === v || o.text.trim() === v)"
            " || [...e.options].find(o => o.text.trim().toLowerCase() === String(v).trim().toLowerCase());"
            " if (!o) return 'no-option'; e.value = o.value; e.dispatchEvent(new Event('input', {bubbles: true}));"
            " e.dispatchEvent(new Event('change', {bubbles: true})); return 'selected'; })()" % (int(element), payload))
        if result == "missing":
            raise ValueError("Это не выпадающий список или он изменился")
        if result == "no-option":
            raise ValueError("В списке нет такого варианта")
        await asyncio.sleep(0.4)
        return "selected"

    async def scroll(self, dy: int, element: Optional[int] = None) -> str:
        dy = max(-4000, min(4000, int(dy or 600)))
        if element:
            # Scroll the nearest scrollable box around the element (lists, dialogs, side panels).
            done = await self.evaluate(
                "(() => {" + _FIND_JS + "const f = __dfyFind(%d); if (!f) return false; let e = f.e;"
                " while (e && e !== e.ownerDocument.documentElement) { const s = getComputedStyle(e);"
                "  if (/(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 4) { e.scrollBy({top: %d, behavior: 'instant'}); return true; }"
                "  e = e.parentElement; } return false; })()" % (int(element), dy))
            if not done:
                raise ValueError("Вокруг этого элемента нет прокручиваемой области")
        else:
            await self.evaluate("window.scrollBy({top: %d, behavior: 'instant'})" % dy)
        await asyncio.sleep(0.3)
        return f"scrolled {dy}px"

    async def adopt_popups(self) -> None:
        """A tab the page opened anyway (bypassing the window.open override) is moved into ours."""
        if not self.port or not self.target_id:
            return
        from .automation_runtime import _read_json_url
        try:
            targets = await asyncio.to_thread(_read_json_url, f"http://127.0.0.1:{self.port}/json/list")
        except Exception:
            return
        others = [t for t in targets or [] if t.get("type") == "page" and t.get("id") != self.target_id]
        if not others:
            return
        url = next((str(t.get("url") or "") for t in others if str(t.get("url") or "") not in ("", "about:blank")), "")
        for target in others:
            try:
                await asyncio.to_thread(_close_target, self.port, str(target.get("id") or ""))
            except Exception:
                pass
        if url and not url_blocked_reason(url):
            await self.command("Page.navigate", {"url": url})
            await self.wait_ready()


def _close_target(port: int, target_id: str) -> None:
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=2) as response:
        response.read()
