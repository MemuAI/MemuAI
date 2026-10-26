"""«Быстрый доступ» × браузер: the page the user was looking at (Round 22).

The user opens a page in any browser, presses the Quick Ask hotkey and asks
"summarize this page". No screenshots are involved:

1. **Before the popup shows**, the native side remembers the foreground
   window (HWND), its process and its title (:func:`foreground_info`). That
   is instant and never blocks opening the window.
2. **On a worker thread** (with a timeout) Windows UI Automation reads the
   active tab's URL from the browser's address bar (:class:`UiaReader` —
   the managed UIA client ``System.Windows.Automation`` through pythonnet,
   which MemuAI already ships for WinForms/WebView2). Chromium-family
   browsers (Chrome, Edge, Brave, Vivaldi, Opera, Yandex, Arc…) and Firefox
   expose the address bar as an ``Edit`` control with a ``ValuePattern``.
   If UIA fails, the window title still gives the page title.
3. **The tools** ``current_page`` / ``read_page`` (``quick_ask_api``) fetch
   the URL through the shared web pipeline (``websearch.fetch_page``:
   redirects checked, readable text extracted). When the fetched page
   clearly is not what the user sees — a login wall, a paywall, a personal
   page, a blocked fetch, a different title (:func:`page_mismatch`) — the
   visible text of the tab is read through UI Automation instead (the
   document's ``TextPattern``, else its text elements), with size and time
   limits.

Everything here degrades to "no page" — never an exception into the caller.
The pure helpers are unit-tested with a fake UIA backend
(``tests/quick_ask_test.py``).
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

log = logging.getLogger("memuai.quick.browser")

BROWSERS: Dict[str, tuple] = {
    "chrome.exe": ("chromium", "Google Chrome"),
    "msedge.exe": ("chromium", "Microsoft Edge"),
    "brave.exe": ("chromium", "Brave"),
    "vivaldi.exe": ("chromium", "Vivaldi"),
    "opera.exe": ("chromium", "Opera"),
    "opera_gx.exe": ("chromium", "Opera GX"),
    "browser.exe": ("chromium", "Яндекс Браузер"),
    "yandex.exe": ("chromium", "Яндекс Браузер"),
    "chromium.exe": ("chromium", "Chromium"),
    "arc.exe": ("chromium", "Arc"),
    "thorium.exe": ("chromium", "Thorium"),
    "firefox.exe": ("gecko", "Firefox"),
    "librewolf.exe": ("gecko", "LibreWolf"),
    "waterfox.exe": ("gecko", "Waterfox"),
    "floorp.exe": ("gecko", "Floorp"),
    "zen.exe": ("gecko", "Zen"),
}

_TITLE_SUFFIX = re.compile(
    r"\s+[-—–]\s+(?:(?:[^-—–]{1,40}\s+[-—–]\s+)?(?:Google Chrome|Chromium|Microsoft\u200b?\s?Edge|Brave|Vivaldi|"
    r"Opera(?: GX)?|Яндекс(?:\.| )Браузер|Yandex(?: Browser)?|Mozilla Firefox(?: Private Browsing)?|Firefox|"
    r"LibreWolf|Waterfox|Floorp|Zen Browser|Zen|Arc|Thorium))\s*$", re.I)
_EDGE_MORE = re.compile(r"\s+(?:and \d+ more pages?|и ещё \d+ (?:страниц[аы]?|вклад\w+))\s*$", re.I)
_ADDRESS_NAMES = re.compile(
    r"(?i)(address|адрес|url|search or enter|search with|enter address|поиск или адрес|введите (?:запрос|адрес)|"
    r"location|omnibox|умная строка|адресная строка|поиск в яндексе или адрес)")
_LOGIN_MARKERS = re.compile(
    r"(?i)(sign in to (?:continue|view)|log in to (?:continue|view)|please (?:sign|log) in|войдите,? чтобы|"
    r"войдите в (?:аккаунт|систему)|авторизуйтесь|вход в (?:аккаунт|систему)|access denied|доступ запрещ|"
    r"are you a robot|captcha|подтвердите, что вы не робот|я не робот|enable javascript|включите javascript|"
    r"checking your browser|проверка браузера|just a moment|"

    r"а не робот|not a robot|verify (?:you are|you're) (?:a )?human|unusual traffic|необычный трафик|"
    r"attention required|ddos-guard|доступ ограничен|access (?:restricted|blocked))")
_PAYWALL_MARKERS = re.compile(
    r"(?i)(subscribe to (?:continue|read)|subscribers only|for subscribers|already a subscriber|"
    r"оформите подписку|только (?:для )?подписчик|доступно по подписке|читать полностью по подписке)")

MAX_PAGE_TEXT = 60_000
MIN_READABLE = 400

def browser_for_process(name: Any) -> Optional[Dict[str, str]]:
    """``{"exe", "family", "name"}`` for a known browser executable, else ``None``."""
    exe = str(name or "").strip().lower().replace("/", "\\").split("\\")[-1]
    if exe in BROWSERS:
        family, title = BROWSERS[exe]
        return {"exe": exe, "family": family, "name": title}
    return None

def clean_title(title: Any) -> str:
    """Window title → page title: drops " - Google Chrome", Edge's "and 3 more pages", a profile name."""
    text = " ".join(str(title or "").split())
    for _ in range(2):
        text = _TITLE_SUFFIX.sub("", text)
        text = _EDGE_MORE.sub("", text)
    return text.strip()[:300]

def is_internal_url(url: str) -> bool:
    return bool(re.match(r"(?i)^(?:chrome|edge|brave|vivaldi|opera|browser|about|chrome-extension|moz-extension|"
                         r"view-source|devtools|file):", str(url or "")))

def normalize_url(value: Any) -> str:
    """Address-bar text → absolute http(s) URL, or ``""`` (search text, internal pages, garbage).

    Chromium hides ``https://`` (and ``www.``) in the omnibox, so a bare
    ``github.com/user/repo`` is completed with ``https://``; ``localhost`` and
    IP addresses get ``http://``.
    """
    text = str(value or "").strip()
    if not text or len(text) > 4000 or any(ch.isspace() for ch in text):
        return ""
    if re.match(r"(?i)^https?://", text):
        return text
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*:", text) and not re.match(r"(?i)^(?:localhost|[\w.-]+):\d+", text):
        return ""
    host = re.match(r"^(localhost|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-f:]+\]|(?:[\w-]+\.)+[a-z\u0430-\u044f\u0451-]{2,})(?::\d+)?(?:[/?#]|$)",
                    text, re.I)
    if not host:
        return ""
    local = host.group(1).lower() == "localhost" or re.match(r"^\d", host.group(1))
    return ("http://" if local else "https://") + text

def pick_address(candidates: Iterable[Dict[str, Any]]) -> str:
    """The address bar among UIA ``Edit`` controls: by its accessible name, else the first URL-looking value."""
    rows = [row for row in candidates if isinstance(row, dict)]
    for row in rows:
        name = str(row.get("name") or "") + " " + str(row.get("automation_id") or "")
        url = normalize_url(row.get("value"))
        if url and _ADDRESS_NAMES.search(name):
            return url
    for row in rows:
        url = normalize_url(row.get("value"))
        if url:
            return url
    return ""

def _tokens(text: str) -> set:
    return {word for word in re.findall(r"[\w\u0400-\u04ff]{3,}", str(text or "").lower())}

def page_mismatch(visible_title: str, fetched: Dict[str, Any]) -> str:
    """Why the fetched page is probably *not* what the user sees ("" = it is fine).

    ``fetch_failed`` (blocked, 4xx/5xx, private network), ``empty`` (a JS app
    without server-rendered text), ``login_wall``, ``paywall``,
    ``title_mismatch`` (personalised page: the tab title shares almost no
    words with the fetched one).
    """
    if not isinstance(fetched, dict) or fetched.get("error"):
        return "fetch_failed"
    text = str(fetched.get("text") or "")
    head = (str(fetched.get("title") or "") + "\n" + text[:3000])
    if _LOGIN_MARKERS.search(head) and len(text) < 6000:
        return "login_wall"
    if _PAYWALL_MARKERS.search(head):
        return "paywall"
    if len(text.strip()) < MIN_READABLE:
        return "empty"
    visible = _tokens(clean_title(visible_title))
    other = _tokens(fetched.get("title") or "")
    if len(visible) >= 2 and len(other) >= 2:
        overlap = len(visible & other) / float(min(len(visible), len(other)))
        if overlap < 0.34 and not (visible & _tokens(text[:4000])):
            return "title_mismatch"
    return ""

def clip(text: Any, limit: int = MAX_PAGE_TEXT) -> Dict[str, Any]:
    value = re.sub(r"[ \t]{2,}", " ", str(text or "")).strip()
    value = re.sub(r"\n{3,}", "\n\n", value)
    return {"text": value[:limit], "truncated": len(value) > limit, "chars": len(value)}

def foreground_info(hwnd: int) -> Dict[str, Any]:
    """``{"hwnd", "title", "exe", "pid"}`` of a top-level window (``{}`` off Windows / on failure)."""
    if sys.platform != "win32" or not hwnd:
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        GetWindowTextLengthW.restype = ctypes.c_int
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        GetWindowTextW.restype = ctypes.c_int
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        GetWindowThreadProcessId.restype = wintypes.DWORD
        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = ctypes.c_void_p
        QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
        QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_wchar_p,
                                               ctypes.POINTER(wintypes.DWORD)]
        QueryFullProcessImageNameW.restype = wintypes.BOOL
        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [ctypes.c_void_p]
        CloseHandle.restype = wintypes.BOOL

        length = max(0, min(int(GetWindowTextLengthW(hwnd)), 2048))
        buffer = ctypes.create_unicode_buffer(length + 2)
        GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD(0)
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        handle = OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                path = ctypes.create_unicode_buffer(1024)
                if QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    exe = path.value.replace("/", "\\").split("\\")[-1].lower()
            finally:
                CloseHandle(handle)
        return {"hwnd": int(hwnd), "title": buffer.value, "exe": exe, "pid": int(pid.value)}
    except Exception as exc:
        log.debug("foreground_info failed: %s", exc)
        return {}

def pick_top_browser(windows: Iterable[Dict[str, Any]], exclude: Iterable[int] = ()) -> int:
    """Round 26: the browser window the user is looking at now — the first (topmost in z-order) visible,
    not minimized, not cloaked window of a known browser. ``windows`` are ``{"hwnd", "exe", "visible",
    "iconic", "cloaked", "title"}`` rows in z-order, top first; ``0`` when there is none."""
    skip = {int(h) for h in exclude if h}
    for row in windows:
        if not isinstance(row, dict) or int(row.get("hwnd") or 0) in skip:
            continue
        if not row.get("visible") or row.get("iconic") or row.get("cloaked") or not str(row.get("title") or "").strip():
            continue
        if browser_for_process(row.get("exe")):
            return int(row["hwnd"])
    return 0

def top_level_windows() -> List[Dict[str, Any]]:  # pragma: no cover - Windows only
    """Visible top-level windows in z-order (top first) for :func:`pick_top_browser` (``[]`` off Windows)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwmapi = ctypes.WinDLL("dwmapi")
        handles: List[int] = []
        proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [proc_type, wintypes.LPARAM]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

        def collect(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                handles.append(int(hwnd or 0))
            return len(handles) < 400
        user32.EnumWindows(proc_type(collect), 0)
        rows = []
        for hwnd in handles:
            cloaked = wintypes.DWORD(0)
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            info = foreground_info(hwnd)
            rows.append({"hwnd": hwnd, "exe": info.get("exe", ""), "title": info.get("title", ""), "visible": True,
                         "iconic": bool(user32.IsIconic(hwnd)), "cloaked": bool(cloaked.value)})
            if browser_for_process(info.get("exe")) and not rows[-1]["iconic"] and not rows[-1]["cloaked"]:
                break
        return rows
    except Exception as exc:
        log.debug("top_level_windows failed: %s", exc)
        return []

class UiaReader:
    """Address bar and visible text of a browser window through UI Automation.

    Uses the managed UIA client (``UIAutomationClient.dll`` of the .NET
    Framework, present on every Windows 10/11) via pythonnet. Every call runs
    on its own daemon thread with a hard timeout, so a hung browser can never
    stall Quick Ask. ``backend`` can be injected (tests): an object with
    ``edits(hwnd) -> [{"name", "value", "automation_id"}]`` and
    ``document_text(hwnd, limit) -> str``.
    """

    _shared: Optional["UiaReader"] = None

    def __init__(self, backend: Any = None) -> None:
        self._backend = backend
        self._lock = threading.Lock()

    @classmethod
    def shared(cls) -> "UiaReader":
        """One reader per process: the .NET assemblies are loaded once."""
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def _get_backend(self):
        if self._backend is None:
            with self._lock:
                if self._backend is None:
                    self._backend = _ManagedUia()
        return self._backend

    @staticmethod
    def _bounded(fn: Callable[[], Any], timeout: float, default: Any) -> Any:
        box: Dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = fn()
            except Exception as exc:      # pragma: no cover - Windows only
                box["error"] = exc
        thread = threading.Thread(target=run, name="memuai-uia", daemon=True)
        thread.start()
        thread.join(timeout)
        if "error" in box:
            log.debug("uia call failed: %s", box["error"])
        return box.get("value", default)

    def address(self, hwnd: int, timeout: float = 1.6) -> str:
        if not hwnd:
            return ""
        try:
            backend = self._get_backend()
        except Exception as exc:
            log.debug("uia unavailable: %s", exc)
            return ""
        return pick_address(self._bounded(lambda: list(backend.edits(int(hwnd)) or []), timeout, []))

    def page_text(self, hwnd: int, limit: int = MAX_PAGE_TEXT, timeout: float = 4.0) -> str:
        if not hwnd:
            return ""
        try:
            backend = self._get_backend()
        except Exception as exc:
            log.debug("uia unavailable: %s", exc)
            return ""
        text = self._bounded(lambda: backend.document_text(int(hwnd), limit), timeout, "") or ""
        if len(text.strip()) < 40:

            time.sleep(0.35)
            text = self._bounded(lambda: backend.document_text(int(hwnd), limit), timeout, "") or text
        return str(text)[:limit]

class _ManagedUia:  # pragma: no cover - needs Windows + .NET
    """System.Windows.Automation through pythonnet (loaded lazily)."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("UI Automation is Windows-only")
        import clr  # type: ignore
        clr.AddReference("UIAutomationClient")
        clr.AddReference("UIAutomationTypes")
        from System import IntPtr  # type: ignore
        from System.Windows.Automation import (AutomationElement, ControlType, PropertyCondition,  # type: ignore
                                               TextPattern, TreeScope, ValuePattern)
        self.IntPtr, self.AE, self.ControlType = IntPtr, AutomationElement, ControlType
        self.PropertyCondition, self.TextPattern, self.TreeScope, self.ValuePattern = (
            PropertyCondition, TextPattern, TreeScope, ValuePattern)

    def _root(self, hwnd: int):
        return self.AE.FromHandle(self.IntPtr(int(hwnd)))

    def edits(self, hwnd: int) -> List[Dict[str, Any]]:
        root = self._root(hwnd)
        condition = self.PropertyCondition(self.AE.ControlTypeProperty, self.ControlType.Edit)
        out: List[Dict[str, Any]] = []

        def describe(element) -> Dict[str, Any]:
            try:
                value = element.GetCurrentPropertyValue(self.ValuePattern.ValueProperty)
            except Exception:
                value = ""
            try:
                name, auto_id = element.Current.Name, element.Current.AutomationId
            except Exception:
                name, auto_id = "", ""
            return {"name": str(name or ""), "value": str(value or ""), "automation_id": str(auto_id or "")}

        first = root.FindFirst(self.TreeScope.Descendants, condition)
        if first is not None:
            out.append(describe(first))
            if pick_address(out):
                return out

        try:
            elements = root.FindAll(self.TreeScope.Descendants, condition)
            for index in range(min(int(elements.Count), 12)):
                out.append(describe(elements[index]))
        except Exception:
            pass
        return out

    def document_text(self, hwnd: int, limit: int) -> str:
        root = self._root(hwnd)
        condition = self.PropertyCondition(self.AE.ControlTypeProperty, self.ControlType.Document)
        document = root.FindFirst(self.TreeScope.Descendants, condition)
        if document is None:
            return ""
        try:
            pattern = document.GetCurrentPattern(self.TextPattern.Pattern)
            text = pattern.DocumentRange.GetText(int(limit))
            if text and len(str(text).strip()) >= 40:
                return str(text)
        except Exception:
            pass

        text_condition = self.PropertyCondition(self.AE.ControlTypeProperty, self.ControlType.Text)
        parts: List[str] = []
        size = 0
        started = time.monotonic()
        try:
            elements = document.FindAll(self.TreeScope.Descendants, text_condition)
            for index in range(min(int(elements.Count), 6000)):
                if size >= limit or time.monotonic() - started > 3.0:
                    break
                try:
                    name = str(elements[index].Current.Name or "").strip()
                except Exception:
                    continue
                if name and (not parts or parts[-1] != name):
                    parts.append(name)
                    size += len(name) + 1
        except Exception:
            pass
        return "\n".join(parts)[:limit]

def detect(hwnd: int, *, info: Optional[Dict[str, Any]] = None, reader: Optional[UiaReader] = None,
           timeout: float = 1.6) -> Optional[Dict[str, Any]]:
    """The page context for the window the user came from, or ``None`` if it is not a browser.

    ``{"hwnd", "browser", "family", "exe", "title", "url", "source": "uia"|"title"}``.
    """
    info = info if info is not None else foreground_info(hwnd)
    browser = browser_for_process((info or {}).get("exe"))
    if not browser:
        return None
    page = {"hwnd": int(hwnd), "browser": browser["name"], "family": browser["family"], "exe": browser["exe"],
            "title": clean_title(info.get("title")), "url": "", "source": "title", "t": time.time()}
    if str(browser.get("exe") or "").lower() == "chrome.exe":
        from .browser_extension import bridge
        extension = bridge.page_hint(page["title"])
        if extension:
            return {**page, **extension}
    reader = reader or UiaReader()
    url = reader.address(hwnd, timeout=timeout)
    if url:
        page["url"], page["source"] = url, "uia"
    return page
