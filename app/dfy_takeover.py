"""DFY takeover (Round 30): the user takes the wheel of the DFY browser, then hands it back.

«Перехватить управление» pauses the agent (``DFYManager`` loop waits at its gate, the running
browser action finishes first), streams the page to a big window in the UI (CDP screencast) and
replays the user's mouse, wheel, keyboard (any layout: the character the user's keyboard produced
is sent), IME/paste text and navigation into the browser. Every action is written to a journal:
«click on “Sign in” (button)», «typed into “Email”: ivan@…», «opened https://…/cart», «scrolled
down ~1200 px». Password, card, CVC and one-time-code fields are never read: their input is
«•••». «Вернуть управление ИИ» ends the session; the agent gets the journal (text, English) and up
to three key screenshots (start, after navigations, end) on its next turn and continues.

Transport. ``Control`` is everything a session needs from a browser. ``CDPControl`` implements it
for any object with ``command(method, params)`` + ``on(event, callback)`` (``DFYBrowser`` locally;
a remote machine that forwards CDP gets it for free). A browser may instead return its own
implementation from ``takeover_control()`` (r30 remote DFY).

The UI talks over one WebSocket ``/api/dfy/tasks/{id}/takeover/ws``: binary messages are JPEG
frames, text messages are JSON (``meta`` frame size, ``state``, ``clipboard``); the client sends
JSON input events with coordinates normalised to the frame (0…1).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
import uuid
from typing import Any, Callable, Dict, List, Optional

IDLE_TIMEOUT = 10 * 60
MAX_SESSION = 60 * 60
MAX_EVENTS = 300
MAX_SHOTS = 6
FRAME_INTERVAL = 1 / 15
HEADLESS_SIZE = (1280, 820)
REASONS = ("captcha", "login", "2fa", "verification", "other")

_VK = {"Enter": 13, "Tab": 9, "Escape": 27, "Backspace": 8, "Delete": 46, "ArrowLeft": 37, "ArrowUp": 38,
       "ArrowRight": 39, "ArrowDown": 40, "Home": 36, "End": 35, "PageUp": 33, "PageDown": 34, "Insert": 45,
       "F1": 112, "F2": 113, "F3": 114, "F4": 115, "F5": 116, "F6": 117, "F7": 118, "F8": 119, "F9": 120,
       "F10": 121, "F11": 122, "F12": 123, "Shift": 16, "Control": 17, "Alt": 18, "Meta": 91, " ": 32}

_COMMANDS = {"a": "selectAll", "z": "undo", "y": "redo", "x": "cut", "c": "copy", "v": "paste"}
_MOD_ALT, _MOD_CTRL, _MOD_META, _MOD_SHIFT = 1, 2, 4, 8

_SENSITIVE_JS = r"""
const __memuSensitive = (e) => {
  if (!e) return false;
  const t = String(e.type || '').toLowerCase();
  if (t === 'password') return true;
  const ac = String(e.autocomplete || e.getAttribute && e.getAttribute('autocomplete') || '').toLowerCase();
  if (/cc-|one-time-code|password/.test(ac)) return true;
  const hint = [e.name, e.id, e.getAttribute && e.getAttribute('aria-label'), e.placeholder].join(' ');
  return /pass|парол|card.?num|cardnumber|номер.?карт|cvc|cvv|cvn|csc|otp|one.?time|sms.?code|код.?(из|подтвержд|смс)|pin\b|пин.?код|secret|ssn|снилс/i.test(hint) || !!(e.closest && e.closest('[data-memu-secret]'));
};
const __memuDescribe = (e) => {
  if (!e || e === document.body || e === document.documentElement) return null;
  const tag = e.tagName.toLowerCase();
  const role = e.getAttribute('role') || '';
  const labelOf = (x) => {
    if (x.labels && x.labels.length) return [...x.labels].map(l => l.innerText).join(' ');
    const by = x.getAttribute('aria-labelledby');
    if (by) { const l = document.getElementById(by.split(' ')[0]); if (l) return l.innerText; }
    return x.getAttribute('aria-label') || x.getAttribute('placeholder') || x.title || x.getAttribute('alt') || x.name || '';
  };
  const editable = /^(input|textarea|select)$/.test(tag) || e.isContentEditable;
  const clickable = e.closest('a[href],button,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=radio],label,summary,input[type=submit],input[type=button],input[type=checkbox],input[type=radio]');
  const target = editable ? e : (clickable || e);
  const ttag = target.tagName.toLowerCase();
  const sens = __memuSensitive(target);
  let text = '';
  if (!/^(input|textarea|select)$/.test(ttag)) text = (target.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  else if (/^(submit|button)$/.test(String(target.type))) text = String(target.value || '').slice(0, 80);
  return { tag: ttag, role: target.getAttribute('role') || role, type: String(target.type || '').toLowerCase(),
    label: String(labelOf(target)).trim().replace(/\s+/g, ' ').slice(0, 80), text,
    href: target.href ? String(target.href).slice(0, 300) : '', editable: !!(/^(input|textarea|select)$/.test(ttag) || target.isContentEditable),
    sensitive: sens, checked: target.checked === true ? true : undefined };
};
const __memuAt = (x, y) => {
  let doc = document, e = doc.elementFromPoint(x, y);
  while (e) {
    if (e.shadowRoot) { const inner = e.shadowRoot.elementFromPoint(x, y); if (inner && inner !== e) { e = inner; continue; } }
    if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
      let d = null; try { d = e.contentDocument; } catch (_) {}
      if (!d) return { e, frame: true };
      const r = e.getBoundingClientRect(); x -= r.left; y -= r.top; doc = d; e = d.elementFromPoint(x, y); continue;
    }
    break;
  }
  return { e, frame: false };
};
const __memuFocused = () => {
  let e = document.activeElement;
  while (e && (e.tagName === 'IFRAME' || e.tagName === 'FRAME')) {
    let d = null; try { d = e.contentDocument; } catch (_) {}
    if (!d) return { e, frame: true };
    e = d.activeElement;
  }
  while (e && e.shadowRoot && e.shadowRoot.activeElement) e = e.shadowRoot.activeElement;
  return { e, frame: false };
};
"""

class Control:
    """What a takeover session needs from a browser (see the module docstring)."""

    async def start_screencast(self, on_frame: Callable[[str, Dict[str, Any]], None], *, max_width: int,
                               max_height: int) -> None: ...
    async def stop_screencast(self) -> None: ...
    async def mouse(self, params: Dict[str, Any]) -> None: ...
    async def key(self, params: Dict[str, Any]) -> None: ...
    async def insert_text(self, text: str) -> None: ...
    async def navigate(self, url: str) -> str: ...
    async def history(self, delta: int) -> None: ...
    async def reload(self) -> None: ...
    async def probe(self, x: float, y: float) -> Optional[Dict[str, Any]]: ...
    async def focused(self) -> Optional[Dict[str, Any]]: ...
    async def field_value(self, marker: str) -> Optional[Dict[str, Any]]: ...
    async def screenshot(self) -> str: ...
    async def selection(self) -> str: ...
    async def viewport(self) -> Dict[str, Any]: ...
    async def fit(self, width: int, height: int) -> None: ...
    async def restore(self) -> None: ...
    def on_navigation(self, callback: Callable[[str], None]) -> Callable[[], None]: ...

class CDPControl(Control):
    def __init__(self, browser: Any) -> None:
        self.browser = browser
        self._unsubscribe: List[Callable[[], None]] = []
        self._frame_cb: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._last_ack = 0.0
        self._fitted = False
        self.main_frame = ""

    async def _eval(self, expression: str) -> Any:
        result = await self.browser.command("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                                 "awaitPromise": True}, timeout=8)
        if result.get("exceptionDetails"):
            return None
        return (result.get("result") or {}).get("value")

    async def start_screencast(self, on_frame, *, max_width: int = 1920, max_height: int = 1200) -> None:
        self._frame_cb = on_frame
        if not self._unsubscribe:
            self._unsubscribe.append(self.browser.on("Page.screencastFrame", self._on_frame))
        await self.browser.command("Page.startScreencast", {"format": "jpeg", "quality": 70, "maxWidth": int(max_width),
                                                            "maxHeight": int(max_height), "everyNthFrame": 1})

    def _on_frame(self, params: Dict[str, Any]) -> None:
        session = params.get("sessionId")
        if self._frame_cb is not None and params.get("data"):
            try:
                self._frame_cb(params["data"], params.get("metadata") or {})
            except Exception:
                pass

        delay = max(0.0, self._last_ack + FRAME_INTERVAL - time.monotonic())
        self._last_ack = time.monotonic() + delay

        def ack() -> None:
            task = asyncio.ensure_future(self.browser.command("Page.screencastFrameAck", {"sessionId": session}, timeout=5))
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
        asyncio.get_running_loop().call_later(delay, ack)

    async def stop_screencast(self) -> None:
        self._frame_cb = None
        for off in self._unsubscribe:
            off()
        self._unsubscribe = []
        try:
            await self.browser.command("Page.stopScreencast", timeout=5)
        except Exception:
            pass

    def on_navigation(self, callback: Callable[[str], None]) -> Callable[[], None]:
        def navigated(params: Dict[str, Any]) -> None:
            frame = params.get("frame") or {}
            if not frame.get("parentId"):
                callback(str(frame.get("url") or "") + str(frame.get("urlFragment") or ""))

        def within(params: Dict[str, Any]) -> None:
            if not self.main_frame or params.get("frameId") == self.main_frame:
                callback(str(params.get("url") or ""))
        offs = [self.browser.on("Page.frameNavigated", navigated), self.browser.on("Page.navigatedWithinDocument", within)]

        async def main_frame() -> None:
            try:
                tree = await self.browser.command("Page.getFrameTree", timeout=5)
                self.main_frame = str(((tree.get("frameTree") or {}).get("frame") or {}).get("id") or "")
            except Exception:
                pass
        asyncio.ensure_future(main_frame())

        def off() -> None:
            for item in offs:
                item()
        return off

    async def mouse(self, params: Dict[str, Any]) -> None:
        await self.browser.command("Input.dispatchMouseEvent", params, timeout=10)

    async def key(self, params: Dict[str, Any]) -> None:
        await self.browser.command("Input.dispatchKeyEvent", params, timeout=10)

    async def insert_text(self, text: str) -> None:
        await self.browser.command("Input.insertText", {"text": text}, timeout=10)

    async def navigate(self, url: str) -> str:
        if hasattr(self.browser, "navigate"):
            return await self.browser.navigate(url)
        await self.browser.command("Page.navigate", {"url": url})
        return url

    async def history(self, delta: int) -> None:
        data = await self.browser.command("Page.getNavigationHistory", timeout=5)
        index = int(data.get("currentIndex") or 0) + int(delta)
        entries = data.get("entries") or []
        if 0 <= index < len(entries):
            from .dfy_browser import url_blocked_reason
            if url_blocked_reason(str(entries[index].get("url") or "")):
                raise ValueError("blocked")
            await self.browser.command("Page.navigateToHistoryEntry", {"entryId": entries[index]["id"]}, timeout=5)

    async def reload(self) -> None:
        await self.browser.command("Page.reload", {}, timeout=10)

    async def probe(self, x: float, y: float) -> Optional[Dict[str, Any]]:
        return await self._eval("(() => {" + _SENSITIVE_JS + "const f = __memuAt(%f, %f); if (f.frame) return {tag: 'iframe', frame: true,"
                                " label: (f.e.title || f.e.name || '').slice(0, 80), sensitive: false}; return __memuDescribe(f.e); })()"
                                % (float(x), float(y)))

    async def focused(self) -> Optional[Dict[str, Any]]:
        marker = uuid.uuid4().hex[:8]
        value = await self._eval("(() => {" + _SENSITIVE_JS + "const f = __memuFocused(); if (!f.e) return null;"
                                 " if (f.frame) return {tag: 'iframe', frame: true, label: (f.e.title || '').slice(0, 80), sensitive: true, editable: true};"
                                 " const d = __memuDescribe(f.e); if (!d || !d.editable) return d;"
                                 " f.e.setAttribute('data-memu-takeover', '%s'); d.marker = '%s'; return d; })()" % (marker, marker))
        return value

    async def field_value(self, marker: str) -> Optional[Dict[str, Any]]:
        """The current value of a field the user typed into (never for a sensitive one)."""
        return await self._eval("(() => {" + _SENSITIVE_JS + "const find = (doc) => { const e = doc.querySelector('[data-memu-takeover=\"%s\"]');"
                                " if (e) return e; for (const f of doc.querySelectorAll('iframe,frame')) { let d = null; try { d = f.contentDocument; } catch (_) {}"
                                " if (d) { const x = find(d); if (x) return x; } } return null; };"
                                " const e = find(document); if (!e) return null; e.removeAttribute('data-memu-takeover');"
                                " if (__memuSensitive(e)) return {sensitive: true};"
                                " const v = e.isContentEditable ? e.innerText : (e.tagName === 'SELECT' ? (e.selectedOptions[0] || {}).text : e.value);"
                                " return {value: String(v || '').slice(0, 300)}; })()" % re.sub(r"[^0-9a-f]", "", marker))

    async def screenshot(self) -> str:

        mask = "(() => {" + _SENSITIVE_JS + "const walk = (doc) => { doc.querySelectorAll('input,textarea').forEach(e => {" \
            " if (!e.hasAttribute('data-memu-secret') && __memuSensitive(e)) e.setAttribute('data-memu-secret', 'tmp'); });" \
            " for (const f of doc.querySelectorAll('iframe,frame')) { let d = null; try { d = f.contentDocument; } catch (_) {} if (d) walk(d); } }; walk(document); })()"
        unmask = "(() => { const walk = (doc) => { doc.querySelectorAll('[data-memu-secret=\"tmp\"]').forEach(e => e.removeAttribute('data-memu-secret'));" \
            " for (const f of doc.querySelectorAll('iframe,frame')) { let d = null; try { d = f.contentDocument; } catch (_) {} if (d) walk(d); } }; walk(document); })()"
        try:
            await self._eval(mask)
            shot = await self.browser.command("Page.captureScreenshot", {"format": "jpeg", "quality": 70}, timeout=15)
            return str(shot.get("data") or "")
        except Exception:
            return ""
        finally:
            try:
                await self._eval(unmask)
            except Exception:
                pass

    async def selection(self) -> str:
        value = await self._eval("(() => {" + _SENSITIVE_JS + "const f = __memuFocused(); const e = f.e;"
                                 " if (e && !f.frame && 'selectionStart' in e && typeof e.value === 'string' && e.selectionEnd > e.selectionStart)"
                                 "  return __memuSensitive(e) ? '' : e.value.slice(e.selectionStart, e.selectionEnd);"
                                 " return String(getSelection() || ''); })()")
        return str(value or "")[:100000]

    async def viewport(self) -> Dict[str, Any]:
        value = await self._eval("({width: innerWidth, height: innerHeight, url: location.href, title: document.title})") or {}
        return value if isinstance(value, dict) else {}

    async def fit(self, width: int, height: int) -> None:
        """A background (headless) browser gets the size of the user's window, so the page is not a small picture."""
        if getattr(self.browser, "visible", False):
            return
        width, height = max(800, min(2560, int(width))), max(480, min(1600, int(height)))
        await self._size(width, height)
        self._fitted = True

    async def _size(self, width: int, height: int) -> None:

        try:
            window = await self.browser.command("Browser.getWindowForTarget", timeout=5)
            await self.browser.command("Browser.setWindowBounds", {"windowId": window["windowId"],
                                                                   "bounds": {"width": width, "height": height}}, timeout=5)
        except Exception:
            pass
        await self.browser.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                                           "deviceScaleFactor": 1, "mobile": False}, timeout=5)

    async def restore(self) -> None:
        if not self._fitted:
            return
        self._fitted = False
        try:
            await self._size(*HEADLESS_SIZE)
        except Exception:
            pass

def control_for(browser: Any) -> Optional[Control]:
    if browser is None:
        return None
    if hasattr(browser, "takeover_control"):
        return browser.takeover_control()
    if hasattr(browser, "command") and hasattr(browser, "on"):
        return CDPControl(browser)
    return None

_SECRET_PARAM = re.compile(r"(?i)token|code|key|secret|session|sid|auth|pass|pw|pin|sig|otp|ticket|state|nonce|cvc|cvv|card")

def safe_url(url: str) -> str:
    """URL for the journal: query values that look like tokens become ``•••``."""
    raw = str(url or "")[:600]
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw[:200]
    if not parts.query:
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:300]
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = "&".join(f"{k}={'•••' if _SECRET_PARAM.search(k) else v[:60]}" for k, v in pairs[:12])
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))[:300]

def _target(info: Optional[Dict[str, Any]], lang: str) -> str:
    if not info:
        return "the page" if lang == "en" else "страницу"
    name = str(info.get("text") or info.get("label") or "").strip()
    tag, kind, role = info.get("tag") or "", info.get("type") or "", info.get("role") or ""
    if info.get("frame"):
        what = "an embedded frame" if lang == "en" else "встроенный фрейм"
        return what + (f" «{name}»" if name else "")
    if tag == "a" or role == "link":
        noun = ("link", "ссылку")
    elif tag == "button" or role == "button" or kind in ("submit", "button"):
        noun = ("button", "кнопку")
    elif kind == "checkbox" or role == "checkbox":
        noun = ("checkbox", "флажок")
    elif kind == "radio" or role == "radio":
        noun = ("option", "вариант")
    elif tag == "select":
        noun = ("list", "список")
    elif info.get("editable"):
        noun = ("field", "поле")
    else:
        noun = ("element", "элемент")
    word = noun[0] if lang == "en" else noun[1]
    return f"{word} «{name}»" if name else word

def describe(event: Dict[str, Any], lang: str = "ru") -> str:
    """One journal line in the given language."""
    en = lang == "en"
    kind = event.get("kind")
    target = event.get("target")
    if kind in ("click", "dblclick", "rclick"):
        verb = {"click": ("Clicked", "Клик:"), "dblclick": ("Double-clicked", "Двойной клик:"),
                "rclick": ("Right-clicked", "Правый клик:")}[kind]
        text = f"{verb[0] if en else verb[1]} {_target(target, lang)}"
        href = (target or {}).get("href")
        if href:
            text += f" → {safe_url(href)}"
        return text
    if kind == "drag":
        return ("Dragged on " if en else "Перетаскивание: ") + _target(target, lang)
    if kind == "type":
        value = event.get("text") or ""
        field = _target(target, lang)
        if event.get("sensitive"):
            return (f"Typed into {field}: ••• (hidden)" if en else f"Ввод в {field}: ••• (скрыто)")
        return (f"Typed into {field}: «{value}»" if en else f"Ввод в {field}: «{value}»")
    if kind == "key":
        count = int(event.get("count") or 1)
        return ("Pressed " if en else "Клавиша ") + str(event.get("key")) + (f" ×{count}" if count > 1 else "")
    if kind == "scroll":
        dy = int(event.get("dy") or 0)
        dx = int(event.get("dx") or 0)
        if abs(dx) > abs(dy):
            way = ("right", "вправо") if dx > 0 else ("left", "влево")
            amount = abs(dx)
        else:
            way = ("down", "вниз") if dy > 0 else ("up", "вверх")
            amount = abs(dy)
        return f"Scrolled {way[0]} ~{amount} px" if en else f"Прокрутка {way[1]} ~{amount} px"
    if kind == "url":
        return ("Opened " if en else "Открыта страница ") + safe_url(event.get("url") or "")
    if kind == "navigate":
        return ("Typed the address " if en else "Введён адрес ") + safe_url(event.get("url") or "")
    if kind == "back":
        return "Went back" if en else "Назад"
    if kind == "forward":
        return "Went forward" if en else "Вперёд"
    if kind == "reload":
        return "Reloaded the page" if en else "Страница перезагружена"
    if kind == "paste":
        return ("Pasted text into " if en else "Вставка текста в ") + _target(target, lang)
    if kind == "copy":
        return "Copied the selection" if en else "Скопировано выделение"
    return str(kind)

class TakeoverSession:
    """One takeover: state, the input replay and the journal. Owned by ``DFYManager``."""

    def __init__(self, task: Any, control: Control, *, by: str = "user", reason: str = "",
                 redact: Callable[[Any], str] = str, idle_timeout: float = IDLE_TIMEOUT) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.task = task
        self.control = control
        self.by = by if by in ("user", "agent") else "user"
        self.reason = reason
        self._redact = redact
        self._typed_secrets: List[str] = []
        self.idle_timeout = float(idle_timeout)
        self.started = time.time()
        self.last_input = time.time()
        self.ended = 0.0
        self.end_reason = ""
        self.events: List[Dict[str, Any]] = []
        self.shots: List[Dict[str, str]] = []
        self.url = ""
        self.frame: Optional[bytes] = None
        self.frame_meta: Dict[str, Any] = {}
        self.frame_seq = 0
        self._frame_changed = asyncio.Event()
        self._state_changed = asyncio.Event()
        self._typing: Optional[Dict[str, Any]] = None
        self._press: Optional[Dict[str, Any]] = None
        self._input_lock = asyncio.Lock()
        self._nav_off: Optional[Callable[[], None]] = None
        self._nav_shot: Optional[asyncio.TimerHandle] = None
        self._viewers = 0
        self._streaming = False
        self._stream_lock = asyncio.Lock()
        self.done = asyncio.Event()

    def redact(self, text: Any) -> str:
        """The vault redactor + every value the user typed into a sensitive field in this session
        (a GET form puts the password into the next URL: «…?login=ivan&pw=…»)."""
        out = self._redact(text)
        for value in self._typed_secrets:
            out = out.replace(value, "•••")
            quoted = urllib.parse.quote_plus(value)
            if quoted != value:
                out = out.replace(quoted, "•••")
        return out

    def typed_secrets(self) -> List[str]:
        return list(self._typed_secrets)

    @property
    def active(self) -> bool:
        return not self.ended

    def deadline(self) -> float:
        return min(self.last_input + self.idle_timeout, self.started + MAX_SESSION)

    def public(self, lang: str = "ru") -> Dict[str, Any]:
        return {"id": self.id, "active": self.active, "by": self.by, "reason": self.reason, "since": self.started,
                "deadline": self.deadline(), "idle_timeout": self.idle_timeout, "events": len(self.events),
                "url": self.redact(safe_url(self.url)) if self.url else "", "ended": self.ended or None,
                "end_reason": self.end_reason or None}

    def journal(self, lang: str = "ru", limit: int = MAX_EVENTS) -> List[str]:
        return [self.redact(describe(event, lang))[:300] for event in self.events[-limit:]]

    def touch(self) -> None:
        self.last_input = time.time()
        self._state_changed.set()

    def _on_frame(self, data_b64: str, meta: Dict[str, Any]) -> None:
        import base64
        try:
            self.frame = base64.b64decode(data_b64)
        except ValueError:
            return
        self.frame_meta = {"width": float(meta.get("deviceWidth") or 0), "height": float(meta.get("deviceHeight") or 0),
                           "offset_top": float(meta.get("offsetTop") or 0)}
        self.frame_seq += 1
        self._frame_changed.set()

    async def attach_viewer(self, max_width: int = 1920, max_height: int = 1200) -> None:
        async with self._stream_lock:
            self._viewers += 1
            if self._streaming:
                return
            self._streaming = True

            view = await self.control.viewport()
            if view.get("width") and view.get("height"):
                await self.control.fit(int(view["width"]), int(view["height"]))
            shot = await _plain_shot(self.control)
            if shot:
                self._on_frame(shot, {"deviceWidth": view.get("width") or 0, "deviceHeight": view.get("height") or 0})
            await self.control.start_screencast(self._on_frame, max_width=max_width, max_height=max_height)

    async def detach_viewer(self) -> None:
        async with self._stream_lock:
            self._viewers = max(0, self._viewers - 1)
            if self._viewers or not self._streaming:
                return
            self._streaming = False
            await self.control.stop_screencast()

    async def next_frame(self, seq: int, timeout: float) -> bool:
        if self.frame_seq != seq:
            return True
        self._frame_changed.clear()
        try:
            await asyncio.wait_for(self._frame_changed.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def next_state(self, timeout: float) -> bool:
        self._state_changed.clear()
        try:
            await asyncio.wait_for(self._state_changed.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def begin(self) -> None:
        view = await self.control.viewport()
        self.url = str(view.get("url") or "")
        self._nav_off = self.control.on_navigation(self._on_navigation)
        await self._shot("start")

    def _on_navigation(self, url: str) -> None:
        if not url or url == self.url or url.startswith("about:"):
            self.url = url or self.url
            return
        self.url = url
        self._flush_typing_later()
        self._add({"kind": "url", "url": url})
        if self._nav_shot is not None:
            self._nav_shot.cancel()

        loop = asyncio.get_running_loop()
        self._nav_shot = loop.call_later(1.5, lambda: asyncio.ensure_future(self._shot("page")))

    async def _shot(self, label: str) -> None:
        if not self.active and label != "end":
            return
        data = await self.control.screenshot()
        if not data:
            return
        if len(self.shots) >= MAX_SHOTS:
            del self.shots[1]
        self.shots.append({"label": label, "data": data, "url": safe_url(self.url)})

    def _add(self, event: Dict[str, Any]) -> None:
        last = self.events[-1] if self.events else None
        if last and event["kind"] in ("key", "scroll") and last["kind"] == event["kind"] and \
                (event["kind"] == "scroll" or last.get("key") == event.get("key")):
            if event["kind"] == "scroll":
                last["dy"] = int(last.get("dy") or 0) + int(event.get("dy") or 0)
                last["dx"] = int(last.get("dx") or 0) + int(event.get("dx") or 0)
            else:
                last["count"] = int(last.get("count") or 1) + 1
            self._state_changed.set()
            return
        if last and event["kind"] == "url" and last["kind"] == "url" and last.get("url") == event.get("url"):
            return
        event["t"] = round(time.time() - self.started, 1)
        self.events.append(event)
        del self.events[:-MAX_EVENTS]
        self._state_changed.set()

    async def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Replay one client event. Returns a reply for the client (``clipboard``) or ``None``."""
        if not self.active:
            return None
        kind = str(message.get("t") or "")
        if kind != "move":
            self.touch()
        else:
            self.last_input = time.time()
        async with self._input_lock:
            if kind in ("down", "up", "move", "wheel"):
                await self._mouse(kind, message)
            elif kind == "key":
                await self._key(message)
            elif kind == "text":
                await self._text(str(message.get("text") or ""), paste=bool(message.get("paste")))
            elif kind == "nav":
                await self._flush_typing()
                url = await self.control.navigate(str(message.get("url") or ""))
                self._add({"kind": "navigate", "url": url})
            elif kind == "history":
                await self._flush_typing()
                delta = -1 if int(message.get("delta") or -1) < 0 else 1
                await self.control.history(delta)
                self._add({"kind": "back" if delta < 0 else "forward"})
            elif kind == "reload":
                await self.control.reload()
                self._add({"kind": "reload"})
            elif kind == "copy":
                text = self.redact(await self.control.selection())
                if text:
                    self._add({"kind": "copy"})
                return {"type": "clipboard", "text": text}
            elif kind == "fit":
                await self.control.fit(int(message.get("w") or 0), int(message.get("h") or 0))
        return None

    def _point(self, message: Dict[str, Any]) -> tuple:
        width = self.frame_meta.get("width") or HEADLESS_SIZE[0]
        height = self.frame_meta.get("height") or HEADLESS_SIZE[1]
        try:
            x, y = float(message.get("x")), float(message.get("y"))
        except (TypeError, ValueError):
            raise ValueError("bad point") from None
        return max(0.0, min(1.0, x)) * width, max(0.0, min(1.0, y)) * height

    @staticmethod
    def _mods(message: Dict[str, Any]) -> int:
        try:
            return int(message.get("mods") or 0) & 15
        except (TypeError, ValueError):
            return 0

    async def _mouse(self, kind: str, message: Dict[str, Any]) -> None:
        x, y = self._point(message)
        button = str(message.get("button") or "left")
        button = button if button in ("left", "middle", "right", "none") else "left"
        base = {"x": x, "y": y, "modifiers": self._mods(message)}
        if kind == "wheel":
            dx = max(-3000.0, min(3000.0, float(message.get("dx") or 0)))
            dy = max(-3000.0, min(3000.0, float(message.get("dy") or 0)))
            await self.control.mouse({"type": "mouseWheel", **base, "deltaX": dx, "deltaY": dy})
            self._add({"kind": "scroll", "dx": int(dx), "dy": int(dy)})
            return
        buttons = int(message.get("buttons") or 0) & 7
        if kind == "move":
            await self.control.mouse({"type": "mouseMoved", **base, "button": button if buttons else "none",
                                      "buttons": buttons})
            return
        clicks = max(1, min(3, int(message.get("clicks") or 1)))
        if kind == "down":
            await self._flush_typing()
            info = None
            try:
                info = await self.control.probe(x, y)
            except Exception:
                pass
            self._press = {"x": x, "y": y, "info": info, "button": button, "clicks": clicks}
            await self.control.mouse({"type": "mousePressed", **base, "button": button, "buttons": buttons or 1,
                                      "clickCount": clicks})
            return
        await self.control.mouse({"type": "mouseReleased", **base, "button": button, "buttons": buttons,
                                  "clickCount": clicks})
        press, self._press = self._press, None
        if not press:
            return
        if abs(press["x"] - x) > 8 or abs(press["y"] - y) > 8:
            self._add({"kind": "drag", "target": press["info"]})
            return
        if press["button"] == "right":
            self._add({"kind": "rclick", "target": press["info"]})
        elif clicks >= 2 and self.events and self.events[-1]["kind"] == "click":
            self.events[-1]["kind"] = "dblclick"
            self._state_changed.set()
        elif press["button"] == "left":
            self._add({"kind": "click", "target": press["info"]})

    async def _key(self, message: Dict[str, Any]) -> None:
        key = str(message.get("key") or "")[:32]
        code = str(message.get("code") or "")[:32]
        down = message.get("type") != "up"
        mods = self._mods(message)
        try:
            vk = int(message.get("keyCode") or 0) or _VK.get(key, 0)
        except (TypeError, ValueError):
            vk = _VK.get(key, 0)
        printable = len(key) == 1 and not (mods & (_MOD_CTRL | _MOD_META)) and not (mods & _MOD_ALT and not mods & _MOD_CTRL)
        if key == "Enter" and down:
            printable = False
        params: Dict[str, Any] = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
                                  "modifiers": mods}
        if code.startswith("Numpad"):
            params["location"] = 3
        if not down:
            await self.control.key({"type": "keyUp", **params})
            return
        if printable:
            await self._typing_target()
            await self.control.key({"type": "keyDown", **params, "text": key, "unmodifiedText": key})
            if self._typing is not None:
                self._typing["buffer"] += key
            return
        if key == "Enter":
            await self.control.key({"type": "keyDown", **params, "text": "\r", "unmodifiedText": "\r"})
        else:
            extra: Dict[str, Any] = {}
            if mods & (_MOD_CTRL | _MOD_META) and key.lower() in _COMMANDS:
                extra["commands"] = [_COMMANDS[key.lower()]]
            await self.control.key({"type": "rawKeyDown", **params, **extra})
        if key in ("Shift", "Control", "Alt", "Meta", "CapsLock"):
            return
        if key == "Backspace" and self._typing is not None and not mods:
            self._typing["buffer"] = self._typing["buffer"][:-1]
            return
        if key in ("ArrowLeft", "ArrowRight", "Delete", "Home", "End") and self._typing is not None and not mods & _MOD_CTRL:
            return
        if mods & (_MOD_CTRL | _MOD_META) and key.lower() in ("a", "z", "y", "x"):
            return
        await self._flush_typing()
        names = {" ": "Space"}
        label = "+".join([n for bit, n in ((_MOD_CTRL, "Ctrl"), (_MOD_ALT, "Alt"), (_MOD_META, "Meta"), (_MOD_SHIFT, "Shift"))
                          if mods & bit and (len(key) == 1 or bit != _MOD_SHIFT or key in _VK)] + [names.get(key, key.upper() if len(key) == 1 else key)])
        self._add({"kind": "key", "key": label})

    async def _text(self, text: str, *, paste: bool) -> None:
        text = text[:20000]
        if not text:
            return
        target = await self._typing_target()
        await self.control.insert_text(text)
        if paste:
            self._add({"kind": "paste", "target": target})
        if self._typing is not None:
            self._typing["buffer"] += text

    async def _typing_target(self) -> Optional[Dict[str, Any]]:
        if self._typing is None:
            info = None
            try:
                info = await self.control.focused()
            except Exception:
                pass
            self._typing = {"info": info, "buffer": ""}
        return self._typing["info"]

    def _flush_typing_later(self) -> None:
        if self._typing is not None:
            asyncio.ensure_future(self._flush_typing())

    async def _flush_typing(self) -> None:
        typing, self._typing = self._typing, None
        if not typing or (not typing["buffer"] and not (typing["info"] or {}).get("marker")):
            return
        info = typing["info"] or {}
        sensitive = bool(info.get("sensitive"))
        value = typing["buffer"]
        if info.get("marker") and not sensitive:
            try:
                current = await self.control.field_value(info["marker"])
            except Exception:
                current = None
            if isinstance(current, dict):
                if current.get("sensitive"):
                    sensitive = True
                elif current.get("value") is not None:
                    value = str(current["value"])
        if not value and not typing["buffer"]:
            return
        from .dfy_vault import looks_like_card
        if looks_like_card(value):
            sensitive = True
        if sensitive:
            for secret in {typing["buffer"], value}:
                if len(secret) >= 3 and secret not in self._typed_secrets:
                    self._typed_secrets.append(secret)
        target = {k: v for k, v in info.items() if k != "marker"}
        self._add({"kind": "type", "target": target, "sensitive": sensitive,
                   "text": "" if sensitive else self.redact(value)[:200]})

    async def finish(self, reason: str) -> None:
        if self.ended:
            return
        await self._flush_typing()
        if self._nav_shot is not None:
            self._nav_shot.cancel()
        self.ended = time.time()
        self.end_reason = reason
        if self._nav_off:
            self._nav_off()
        try:
            await self._shot("end")
        except Exception:
            pass
        async with self._stream_lock:
            if self._streaming:
                self._streaming = False
                await self.control.stop_screencast()
        try:
            await self.control.restore()
        except Exception:
            pass
        self.done.set()
        self._state_changed.set()
        self._frame_changed.set()

    def model_report(self) -> Dict[str, Any]:
        """What the agent gets: English journal text + up to three key screenshots."""
        lines = self.journal("en", 80)
        minutes = max(1, int(round(((self.ended or time.time()) - self.started) / 60)))
        why = {"user": "the user pressed «Return control to AI»", "timeout": "the user was idle, control returned automatically",
               "cancel": "the task was cancelled", "agent": "the user finished"}.get(self.end_reason, self.end_reason or "returned")
        head = (f"The user took control of the browser for ~{minutes} min ({len(self.events)} actions; {why}). "
                f"Now on: {self.redact(safe_url(self.url))}. Passwords and codes typed by the user are hidden as •••.")
        shots = []
        if self.shots:
            picks = [self.shots[-1]] + [s for s in self.shots[:-1]][-2:]
            shots = [{"label": s["label"], "url": self.redact(s.get("url") or ""), "data": s["data"]} for s in reversed(picks)]
        return {"text": head + ("\n" + "\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1)) if lines else
                                "\nThe user did nothing in the browser."), "lines": lines, "shots": shots}

async def _plain_shot(control: Control) -> str:
    """The first frame: a screenshot without the sensitive-field mask flicker when the control allows it."""
    if isinstance(control, CDPControl):
        try:
            shot = await control.browser.command("Page.captureScreenshot", {"format": "jpeg", "quality": 70}, timeout=15)
            return str(shot.get("data") or "")
        except Exception:
            return ""
    return await control.screenshot()

def local_websocket(websocket: Any) -> bool:
    """The same boundary as the HTTP middleware of the desktop API: a local Host and our own Origin."""
    host_header = websocket.headers.get("host", "")
    try:
        host_name = urllib.parse.urlsplit("//" + host_header).hostname or ""
    except ValueError:
        return False
    if host_name.lower() not in ("127.0.0.1", "localhost", "::1"):
        return False
    origin = websocket.headers.get("origin", "")
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    return parsed.netloc.lower() == host_header.lower() and (parsed.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")

async def serve_websocket(websocket: Any, session: TakeoverSession, lang: Callable[[], str]) -> None:
    """Frames out (binary JPEG + ``meta``), input in, ``state`` on every journal/control change."""
    from starlette.websockets import WebSocketDisconnect
    await websocket.accept()
    try:
        await session.attach_viewer()
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "error": str(exc)[:300]}))
        await websocket.close()
        return
    sent_meta: Dict[str, Any] = {}
    closed = asyncio.Event()

    async def send_state() -> None:
        await websocket.send_text(json.dumps({"type": "state", **session.public(lang()),
                                              "journal": session.journal(lang(), 60)}, ensure_ascii=False))

    async def frames() -> None:
        seq = -1
        while not closed.is_set() and session.active:
            if not await session.next_frame(seq, 10):
                continue
            if session.frame is None or session.frame_seq == seq:
                continue
            seq = session.frame_seq
            if session.frame_meta != sent_meta:
                sent_meta.clear()
                sent_meta.update(session.frame_meta)
                await websocket.send_text(json.dumps({"type": "meta", **session.frame_meta}))
            await websocket.send_bytes(session.frame)

    async def states() -> None:
        await send_state()
        while not closed.is_set():
            changed = await session.next_state(5)
            if changed:
                await asyncio.sleep(0.25)
            await send_state()
            if not session.active:
                closed.set()
                return

    async def inputs() -> None:
        while not closed.is_set():
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            try:
                reply = await session.handle(message)
            except (ValueError, RuntimeError, asyncio.TimeoutError) as exc:
                reply = {"type": "error", "error": str(exc)[:300]}
            if reply:
                await websocket.send_text(json.dumps(reply, ensure_ascii=False))

    jobs = [asyncio.ensure_future(job()) for job in (frames, states, inputs)]
    try:
        await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        closed.set()
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        try:
            await session.detach_viewer()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
