"""Проверка орфографии через Windows Spell Checking API (ISpellChecker).

MemuAI работает внутри WebView2, а тот показывает подсказки Windows только в
своём собственном контекстном меню. Чтобы орфография работала в интерфейсе
приложения (волнистое подчёркивание в поле ввода и подсказки в нашем меню),
мы обращаемся к COM-API Windows напрямую через ctypes.

Вся работа с COM выполняется в одном выделенном потоке-апартаменте: указатели
на COM-интерфейсы, созданные в одном апартаменте, нельзя использовать из
другого потока. На не-Windows системах модуль остаётся импортируемым и просто
сообщает ``available: False`` — это нужно для тестов и для будущих портов.
"""

from __future__ import annotations

import ctypes
import platform
import queue
import threading
from typing import Any, Callable, Dict, List

IS_WINDOWS = platform.system() == "Windows"

S_OK = 0
S_FALSE = 1
CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 0x2

CLSID_SPELL_CHECKER_FACTORY = "{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}"
IID_SPELL_CHECKER_FACTORY = "{8E018A9D-2415-4677-BF08-794EA61F94BB}"

ACTION_NAMES = {0: "none", 1: "get_suggestions", 2: "replace", 3: "delete"}

DEFAULT_LANGUAGES = ("ru-RU", "en-US")
MAX_TEXT = 20000
MAX_ERRORS = 300
MAX_SUGGESTIONS = 8
CALL_TIMEOUT = 8.0

class SpellcheckUnavailable(RuntimeError):
    """Проверка орфографии недоступна на этой системе.

    ``en`` — та же причина по-английски (Round 27: английское окно показывало «Язык en-GB не поддерживается
    Windows»); технические коды HRESULT одинаковы на обоих языках."""

    def __init__(self, message: str, en: str = "") -> None:
        super().__init__(message)
        self.en = en or message

if IS_WINDOWS:  # pragma: no cover - проверяется только на Windows
    from ctypes import POINTER, byref, c_int, c_long, c_ulong, c_void_p, c_wchar_p

    _ole32 = ctypes.windll.ole32

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def _guid(text: str) -> "GUID":
        value = GUID()
        hr = _ole32.CLSIDFromString(c_wchar_p(text), byref(value))
        if hr != S_OK:
            raise SpellcheckUnavailable("CLSIDFromString: 0x%08X" % (hr & 0xFFFFFFFF))
        return value

    def _call(ptr: int, index: int, argtypes: List[Any], *args: Any) -> int:
        """Вызвать метод COM-интерфейса по индексу в его vtable."""
        vtable = ctypes.cast(c_void_p(ptr), POINTER(POINTER(c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)
        return proto(vtable[index])(c_void_p(ptr), *args)

    def _need(hr: int, what: str) -> int:
        if hr < 0:
            raise SpellcheckUnavailable("%s: 0x%08X" % (what, hr & 0xFFFFFFFF))
        return hr

    def _release(ptr: Any) -> None:
        if not ptr:
            return
        try:
            _call(int(ptr), 2, [])
        except Exception:
            pass

    def _take_string(address: Any) -> str:
        """Скопировать LPWSTR из COM и освободить память через CoTaskMemFree."""
        if not address:
            return ""
        try:
            return ctypes.wstring_at(int(address))
        finally:
            _ole32.CoTaskMemFree(c_void_p(int(address)))

    def _enum_strings(enum_ptr: int, limit: int = 64) -> List[str]:
        out: List[str] = []
        while len(out) < limit:
            item = c_void_p()
            fetched = c_ulong(0)
            hr = _call(
                enum_ptr, 3,
                [c_ulong, POINTER(c_void_p), POINTER(c_ulong)],
                c_ulong(1), byref(item), byref(fetched),
            )
            if hr != S_OK or not fetched.value or not item.value:
                break
            text = _take_string(item.value)
            if text:
                out.append(text)
        return out

class _ComWorker:
    def __init__(self) -> None:
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Any = None
        self._lock = threading.Lock()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, name="memuai-spellcheck", daemon=True,
            )
            self._thread.start()

    def _loop(self) -> None:
        if IS_WINDOWS:  # pragma: no cover

            try:
                _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            except Exception:
                pass
        while True:
            job = self._queue.get()
            if job is None:
                return
            fn, box = job
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - передаём вызывающему
                box["error"] = exc
            finally:
                box["event"].set()

    def submit(self, fn: Callable[[], Any], timeout: float = CALL_TIMEOUT) -> Any:
        self._ensure_thread()
        box: Dict[str, Any] = {"event": threading.Event()}
        self._queue.put((fn, box))
        if not box["event"].wait(timeout):
            raise SpellcheckUnavailable("Проверка орфографии не ответила вовремя", "Spell check did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("value")

_worker = _ComWorker()

_com: Dict[str, Any] = {"factory": None, "checkers": {}, "languages": None}
_cache: Dict[str, Any] = {"key": "", "value": None}
_cache_lock = threading.Lock()

def _normalize_language(language: str = "") -> str:
    text = (language or "").strip().replace("_", "-")
    return text or DEFAULT_LANGUAGES[0]

def _factory() -> int:  # pragma: no cover - Windows only
    if _com["factory"]:
        return int(_com["factory"])
    ptr = c_void_p()
    hr = _ole32.CoCreateInstance(
        byref(_guid(CLSID_SPELL_CHECKER_FACTORY)), None, CLSCTX_INPROC_SERVER,
        byref(_guid(IID_SPELL_CHECKER_FACTORY)), byref(ptr),
    )
    _need(hr, "CoCreateInstance(SpellCheckerFactory)")
    if not ptr.value:
        raise SpellcheckUnavailable("SpellCheckerFactory недоступна", "SpellCheckerFactory is unavailable")
    _com["factory"] = ptr.value
    return int(ptr.value)

def _languages() -> List[str]:  # pragma: no cover - Windows only
    if _com["languages"] is not None:
        return list(_com["languages"])
    enum = c_void_p()
    _need(_call(_factory(), 3, [POINTER(c_void_p)], byref(enum)), "get_SupportedLanguages")
    try:
        found = _enum_strings(int(enum.value or 0), limit=200) if enum.value else []
    finally:
        _release(enum.value)
    _com["languages"] = found
    return list(found)

def _is_supported(language: str) -> bool:  # pragma: no cover - Windows only
    flag = c_int(0)
    hr = _call(_factory(), 4, [c_wchar_p, POINTER(c_int)], c_wchar_p(language), byref(flag))
    if hr < 0:
        return False
    return bool(flag.value)

def _checker(language: str) -> int:  # pragma: no cover - Windows only
    lang = _normalize_language(language)
    cached = _com["checkers"].get(lang)
    if cached:
        return int(cached)
    if not _is_supported(lang):
        raise SpellcheckUnavailable("Язык %s не поддерживается Windows" % lang,
                                    "Windows has no spell check dictionary for %s" % lang)
    ptr = c_void_p()
    _need(
        _call(_factory(), 5, [c_wchar_p, POINTER(c_void_p)], c_wchar_p(lang), byref(ptr)),
        "CreateSpellChecker",
    )
    if not ptr.value:
        raise SpellcheckUnavailable("Не удалось создать проверку для %s" % lang, "Could not create a spell checker for %s" % lang)
    _com["checkers"][lang] = ptr.value
    return int(ptr.value)

def _suggest(checker: int, word: str) -> List[str]:  # pragma: no cover - Windows only
    if not word:
        return []
    enum = c_void_p()
    hr = _call(checker, 5, [c_wchar_p, POINTER(c_void_p)], c_wchar_p(word), byref(enum))
    if hr < 0 or not enum.value:
        return []
    try:
        return _enum_strings(int(enum.value), limit=MAX_SUGGESTIONS)
    finally:
        _release(enum.value)

def _check_text_com(text: str, language: str) -> Dict[str, Any]:  # pragma: no cover
    checker = _checker(language)
    enum = c_void_p()
    _need(
        _call(checker, 4, [c_wchar_p, POINTER(c_void_p)], c_wchar_p(text), byref(enum)),
        "ISpellChecker::Check",
    )
    errors: List[Dict[str, Any]] = []
    utf16 = text.encode("utf-16-le", "surrogatepass")
    try:
        while enum.value and len(errors) < MAX_ERRORS:
            item = c_void_p()
            hr = _call(int(enum.value), 3, [POINTER(c_void_p)], byref(item))
            if hr != S_OK or not item.value:
                break
            error_ptr = int(item.value)
            try:
                start, length, action = c_ulong(0), c_ulong(0), c_int(0)
                _call(error_ptr, 3, [POINTER(c_ulong)], byref(start))
                _call(error_ptr, 4, [POINTER(c_ulong)], byref(length))
                _call(error_ptr, 5, [POINTER(c_int)], byref(action))
                replacement = ""
                if action.value == 2:
                    holder = c_void_p()
                    if _call(error_ptr, 6, [POINTER(c_void_p)], byref(holder)) >= 0:
                        replacement = _take_string(holder.value)

                word = utf16[2 * start.value:2 * (start.value + length.value)].decode(
                    "utf-16-le", "replace")
                suggestions = [] if action.value == 3 else _suggest(checker, word)
                if replacement and replacement not in suggestions:
                    suggestions.insert(0, replacement)
                errors.append({
                    "start": int(start.value),
                    "length": int(length.value),
                    "word": word,
                    "action": ACTION_NAMES.get(int(action.value), "none"),
                    "replacement": replacement,
                    "suggestions": suggestions[:MAX_SUGGESTIONS],
                })
            finally:
                _release(error_ptr)
    finally:
        _release(enum.value)
    return {
        "available": True,
        "language": _normalize_language(language),
        "errors": errors,
    }

def _check_mixed_com(text: str, language: str) -> Dict[str, Any]:  # pragma: no cover
    """Проверка смешанного текста: слово — ошибка, только если его не знает
    ни один из установленных языков по умолчанию. Раньше текст проверялся одним
    русским словарём, и любое английское слово подчёркивалось как опечатка."""
    result = _check_text_com(text, language)
    if not result["errors"]:
        return result
    for other in DEFAULT_LANGUAGES:
        if other.lower() == language.lower() or not _is_supported(other):
            continue
        flagged = {(e["start"], e["length"]) for e in _check_text_com(text, other)["errors"]}
        result["errors"] = [e for e in result["errors"]
                            if e["action"] == "delete" or (e["start"], e["length"]) in flagged]
        if not result["errors"]:
            break
    return result

def _unavailable(reason: str, reason_en: str = "") -> Dict[str, Any]:

    return {"available": False, "reason": reason, "reason_en": reason_en or reason, "languages": [], "errors": []}

def spell_status() -> Dict[str, Any]:
    """Доступна ли проверка орфографии и какие языки установлены в системе."""
    if not IS_WINDOWS:
        return _unavailable("Проверка орфографии доступна только в Windows", "Spell check is available only on Windows")
    try:
        languages = _worker.submit(_languages)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(str(exc), getattr(exc, "en", ""))
    preferred = [lang for lang in DEFAULT_LANGUAGES if lang in languages]
    return {
        "available": True,
        "reason": "",
        "languages": languages,
        "preferred": preferred or list(languages[:2]),
        "errors": [],
    }

def check_text(text: str, language: str = "") -> Dict[str, Any]:
    """Найти орфографические ошибки в тексте."""
    body = (text or "")[:MAX_TEXT]
    lang = _normalize_language(language)
    if not IS_WINDOWS:
        return _unavailable("Проверка орфографии доступна только в Windows", "Spell check is available only on Windows")
    if not body.strip():
        return {"available": True, "language": lang, "errors": []}
    key = lang + "\u0000" + body
    with _cache_lock:
        if _cache["key"] == key and _cache["value"] is not None:
            return dict(_cache["value"])
    try:
        result = _worker.submit(lambda: _check_mixed_com(body, lang))
    except Exception as exc:  # noqa: BLE001
        return _unavailable(str(exc), getattr(exc, "en", ""))
    with _cache_lock:
        _cache["key"], _cache["value"] = key, result
    return dict(result)

def _word_action(index: int, word: str, language: str, what: str) -> Dict[str, Any]:
    clean = (word or "").strip()[:80]
    if not IS_WINDOWS:
        return _unavailable("Проверка орфографии доступна только в Windows", "Spell check is available only on Windows")
    if not clean:
        return {"available": True, "ok": False, "reason": "Пустое слово", "reason_en": "Empty word"}

    def run() -> Dict[str, Any]:  # pragma: no cover - Windows only
        checker = _checker(language)
        _need(_call(checker, index, [c_wchar_p], c_wchar_p(clean)), what)
        return {"available": True, "ok": True, "word": clean}

    try:
        result = _worker.submit(run)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(str(exc), getattr(exc, "en", ""))
    with _cache_lock:
        _cache["key"], _cache["value"] = "", None
    return result

def add_word(word: str, language: str = "") -> Dict[str, Any]:
    """Добавить слово в пользовательский словарь Windows."""
    return _word_action(6, word, _normalize_language(language), "ISpellChecker::Add")

def ignore_word(word: str, language: str = "") -> Dict[str, Any]:
    """Игнорировать слово до перезапуска приложения."""
    return _word_action(7, word, _normalize_language(language), "ISpellChecker::Ignore")
