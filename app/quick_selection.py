"""Round 27: «Контекстные действия по выделению» — Quick Ask acts on the text selected in ANY program.

The user selects text in any Windows program and presses the Quick Ask hotkey: the window opens with the
selection as a compact chip and a row of actions (translate, explain, fix, summarize, reply, ask + the user's
own actions with a ``{text}`` prompt). The answer streams like any Quick Ask answer and is saved in the chat;
«Заменить выделение» pastes it back over the selection in the source program.

Layers (everything Win32 is behind :class:`Backend`, tests inject a fake one):

* **capture** — :func:`capture`: 1) UI Automation ``TextPattern.GetSelection`` of the focused element (no side
  effects at all); 2) only when the program does not expose its selection that way, the clipboard fallback:
  snapshot EVERY clipboard format that can be restored (HGLOBAL formats byte for byte, the enhanced metafile by
  handle copy; synthesized ones are re-synthesized by Windows), ``SendInput`` Ctrl+C to the foreground window
  BEFORE our window is shown (and make sure the keys reached that window's queue), then — while the window is
  already showing — wait in the background for ``GetClipboardSequenceNumber`` to change (≤ :data:`COPY_TIMEOUT`),
  read the text, restore the snapshot; the selection then comes to the open window (:class:`Capture`, :func:`start`). An unchanged sequence number means «nothing selected» — the old clipboard is never
  mistaken for a selection. The fallback is skipped when the snapshot cannot be complete (a format that cannot be
  restored, a huge clipboard, the clipboard is busy), for MemuAI's own windows, for terminals / remote desktops /
  VMs / IDEs with an integrated terminal (Ctrl+C there interrupts a running program) and for the desktop.
* **actions** — :data:`BUILTIN`, :func:`normalize_prefs` (``quick_ask_selection*`` keys), :func:`resolve_action`
  turns ``{"id", "target"?}`` from the page into the instruction the model gets (the window shows only the short
  label as the question) plus per-action overrides (no length cap for rewrites, no web search for transforms).
* **replace** — :func:`replace`: put the answer on the clipboard, return the focus to the source window, Ctrl+V,
  then restore the user's clipboard (only if nobody changed it meanwhile).

Off Windows :func:`available` is ``False`` and :func:`capture` returns ``reason="unsupported"``.
"""
from __future__ import annotations

import logging
import ntpath
import os
import re
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("memuai.quick.selection")

MAX_TEXT = 60000

COPY_TIMEOUT = 0.5

UIA_TIMEOUT = 0.2

MODIFIER_WAIT = 0.04

INPUT_FLUSH = 0.05

SETTLE_GAP = 0.03

MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024

RESTORE_AFTER_PASTE = 1.0

SNAPSHOT_BUDGET = 0.25
MAX_CUSTOM = 12

LANGS: Dict[str, Tuple[str, str, str]] = {

    "en": ("английский", "English", "Английский"),
    "ru": ("русский", "Russian", "Русский"),
    "de": ("немецкий", "German", "Немецкий"),
    "fr": ("французский", "French", "Французский"),
    "es": ("испанский", "Spanish", "Испанский"),
    "it": ("итальянский", "Italian", "Итальянский"),
    "pt": ("португальский", "Portuguese", "Португальский"),
    "uk": ("украинский", "Ukrainian", "Украинский"),
    "pl": ("польский", "Polish", "Польский"),
    "tr": ("турецкий", "Turkish", "Турецкий"),
    "zh": ("китайский", "Chinese", "Китайский"),
    "ja": ("японский", "Japanese", "Японский"),
    "ko": ("корейский", "Korean", "Корейский"),
    "ar": ("арабский", "Arabic", "Арабский"),
}

_CYR = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґЎў]")
_UKR = re.compile(r"[ІіЇїЄєҐґ]")
_LAT = re.compile(r"[A-Za-z]")
_LAT_EXT = re.compile(r"[À-ɏ]")
_EN_WORDS = re.compile(r"\b(the|and|is|are|of|to|in|that|it|you|for|with|this|have|be|on|not|was|we|i|my|your)\b", re.I)

def detect_lang(text: str) -> str:
    """A cheap guess of the selection's language: ``"ru"``, ``"en"``, ``"uk"`` or ``""`` (something else / unsure).

    Mirrored by ``guessLang()`` in ``quick/quick-selection.js`` (the button label) — keep them in sync.
    """
    sample = str(text or "")[:4000]
    cyr, lat, ext = len(_CYR.findall(sample)), len(_LAT.findall(sample)), len(_LAT_EXT.findall(sample))
    letters = cyr + lat + ext
    if letters < 2:
        return ""
    if cyr / letters >= 0.5:
        return "uk" if len(_UKR.findall(sample)) >= max(1, cyr // 60) else "ru"
    if lat / letters >= 0.85 and (len(_EN_WORDS.findall(sample)) or (letters < 40 and not ext)):
        return "en"
    return ""

def second_lang(prefs: Dict[str, Any], ui: str) -> str:
    """Where text that is ALREADY in the interface language is translated to."""
    value = str(prefs.get("quick_ask_selection_second_lang") or "")
    if value in LANGS and value != ui:
        return value
    return "en" if ui == "ru" else "ru"

def translate_target(text: str, prefs: Dict[str, Any], ui: str) -> str:
    """Into the interface language; text that is already in it — into the second language."""
    ui = "en" if ui == "en" else "ru"
    return second_lang(prefs, ui) if detect_lang(text) == ui else ui

def lang_name(code: str, lang: str) -> str:
    row = LANGS.get(code)
    if not row:
        return code
    return row[1] if lang == "en" else row[0]

BUILTIN: Dict[str, Dict[str, Any]] = {
    "translate": {"model": True, "replace": True, "style": "detailed", "web": False},
    "explain": {"model": True, "replace": False, "style": "", "web": True},
    "fix": {"model": True, "replace": True, "style": "detailed", "web": False},
    "summarize": {"model": True, "replace": False, "style": "balanced", "web": False},
    "reply": {"model": True, "replace": False, "style": "detailed", "web": False},
    "ask": {"model": False, "replace": False, "style": "", "web": True},
}
DEFAULT_ACTIONS = ["translate", "explain", "fix", "summarize", "reply", "ask"]

PREF_DEFAULTS: Dict[str, Any] = {

    "quick_ask_selection": True,

    "quick_ask_selection_clipboard": True,

    "quick_ask_selection_actions": list(DEFAULT_ACTIONS),

    "quick_ask_selection_custom": [],

    "quick_ask_selection_second_lang": "",
    "quick_ask_selection_v": 2,
}

def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)

def _clean_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]

def normalize_custom(rows: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = _clean_line(row.get("name"), 40)
        prompt = str(row.get("prompt") or "").strip()[:2000]
        if not name or not prompt:
            continue
        rid = str(row.get("id") or "")
        if not re.fullmatch(r"c_[a-z0-9]{4,16}", rid) or rid in seen:
            rid = "c_" + uuid.uuid4().hex[:8]
        seen.add(rid)
        out.append({"id": rid, "name": name, "prompt": prompt, "replace": _bool(row.get("replace"), False)})
        if len(out) >= MAX_CUSTOM:
            break
    return out

def normalize_prefs(out: Dict[str, Any], raw: Any = None) -> Dict[str, Any]:
    """Validate the ``quick_ask_selection*`` keys in place (called from ``quick_ask_core.normalize_prefs``).

    Migration (v1 → v2): before Round 27 ``quick_ask_selection`` was an opt-in Ctrl+C switch that was off for
    everybody who never opened the setting; the selection actions are now a headline feature (and read the
    selection through UI Automation first), so stored preferences without the v2 marker get it switched on once.
    """
    raw = raw if isinstance(raw, dict) else {}
    try:
        version = int(raw.get("quick_ask_selection_v") or 0)
    except (TypeError, ValueError):
        version = 0
    out["quick_ask_selection"] = True if version < 2 else _bool(out.get("quick_ask_selection"), True)
    out["quick_ask_selection_clipboard"] = _bool(out.get("quick_ask_selection_clipboard"), True)
    custom = normalize_custom(out.get("quick_ask_selection_custom"))
    out["quick_ask_selection_custom"] = custom
    known = set(BUILTIN) | {row["id"] for row in custom}
    actions = out.get("quick_ask_selection_actions")
    if not isinstance(actions, list):
        actions = list(DEFAULT_ACTIONS)
    ordered: List[str] = []
    for item in actions:
        item = str(item or "")
        if item in known and item not in ordered:
            ordered.append(item)
    out["quick_ask_selection_actions"] = ordered[:24]
    second = str(out.get("quick_ask_selection_second_lang") or "")
    out["quick_ask_selection_second_lang"] = second if second in LANGS else ""
    out["quick_ask_selection_v"] = 2
    return out

def _prompts(lang: str) -> Dict[str, str]:
    if lang == "en":
        return {
            "translate": ("Translate the text into {target}. Keep the meaning, tone, formatting and line breaks. "
                          "Output only the translation — no notes, no quotes. If the text is already in {target}, "
                          "translate it into {alt} instead."),
            "explain": ("Explain in simple words what this text means: the gist, the terms and any subtext. "
                        "Answer in English."),
            "fix": ("Fix the spelling, grammar, punctuation and typos; where a phrase sounds clumsy, make it natural. "
                    "Keep the language, meaning, tone, formatting and line breaks. Output only the corrected text, "
                    "no notes."),
            "summarize": ("Summarize the text: the main points in 2–4 sentences or up to 5 short bullets. "
                          "Answer in English."),
            "reply": ("This is a letter or a message I received. Write a reply from me, ready to send, in the "
                      "language of the message: polite, to the point, matching its tone. Where information I would "
                      "have to add is missing, leave a [placeholder]. Output only the reply text."),
            "custom_suffix": "The text is in <selected_text>.",
            "note": "Also: {note}",
        }
    return {
        "translate": ("Переведи текст на {target}. Сохрани смысл, тон, форматирование и переносы строк. "
                      "Верни только перевод — без пояснений и кавычек. Если текст уже на языке «{target}», "
                      "переведи его на {alt}."),
        "explain": ("Объясни простыми словами, что значит этот текст: суть, термины и подтекст, если он есть. "
                    "Ответь по-русски."),
        "fix": ("Исправь орфографию, грамматику, пунктуацию и опечатки; где фраза звучит неуклюже — сделай "
                "естественнее. Сохрани язык, смысл, тон, форматирование и переносы строк. Верни только "
                "исправленный текст, без пояснений."),
        "summarize": ("Кратко перескажи текст: главное в 2–4 предложениях или до 5 коротких пунктов. "
                      "Ответь по-русски."),
        "reply": ("Это письмо или сообщение, которое мне прислали. Напиши мой ответ, готовый к отправке, на языке "
                  "сообщения: вежливо, по делу, в тоне собеседника. Где не хватает данных, которые должен добавить "
                  "я, оставь [заполнитель]. Верни только текст ответа."),
        "custom_suffix": "Текст — в <selected_text>.",
        "note": "Дополнительно: {note}",
    }

def resolve_action(spec: Any, prefs: Dict[str, Any], *, lang: str, selection: str) -> Optional[Dict[str, Any]]:
    """``{"id", "target"?}`` from the page → what the model gets.

    Returns ``None`` when ``spec`` is not a selection action. Otherwise ``{"id", "text", "selection",
    "settings", "replace", "error"?}``: ``text`` replaces the short label the window shows as the question,
    ``selection`` is what goes into ``<selected_text>`` (empty when a custom prompt embedded it via ``{text}``),
    ``settings`` overrides Quick Ask settings for this one answer (length rule, web search).
    """
    if not isinstance(spec, dict) or not spec.get("id"):
        return None
    en = lang == "en"
    action_id = str(spec.get("id") or "")
    if action_id == "ask":
        return None
    selection = str(selection or "").strip()
    if not selection:
        return {"id": action_id, "error": "There is no selected text." if en else "Нет выделенного текста."}
    prompts = _prompts("en" if en else "ru")
    note = _clean_line(spec.get("note"), 500)
    tail = ("\n\n" + prompts["note"].format(note=note)) if note else ""
    settings: Dict[str, Any] = {}
    if action_id in BUILTIN and BUILTIN[action_id]["model"]:
        meta = BUILTIN[action_id]
        if action_id == "translate":
            ui = "en" if en else "ru"
            target = str(spec.get("target") or "")
            if target not in LANGS:
                target = translate_target(selection, prefs, ui)
            alt = second_lang(prefs, ui) if target == ui else ui
            text = prompts["translate"].format(target=lang_name(target, lang), alt=lang_name(alt, lang))
        else:
            text = prompts[action_id]
        if meta["style"]:
            settings["quick_ask_style"] = meta["style"]
        if not meta["web"]:
            settings["quick_ask_web_search"] = False
        return {"id": action_id, "text": text + tail, "selection": selection, "settings": settings,
                "replace": bool(meta["replace"])}
    custom = next((row for row in normalize_custom(prefs.get("quick_ask_selection_custom"))
                   if row["id"] == action_id), None)
    if custom is None:
        return {"id": action_id, "error": ("This action no longer exists — check Settings → Quick Ask → Selected text."
                                           if en else "Такого действия больше нет — проверьте Настройки → "
                                           "Быстрый доступ → Выделенный текст.")}
    prompt = custom["prompt"]
    settings["quick_ask_style"] = "detailed"
    if "{text}" in prompt:
        return {"id": action_id, "text": prompt.replace("{text}", selection) + tail, "selection": "",
                "settings": settings, "replace": custom["replace"]}
    return {"id": action_id, "text": prompt + "\n\n" + prompts["custom_suffix"] + tail, "selection": selection,
            "settings": settings, "replace": custom["replace"]}

NO_COPY_EXES = {
    "windowsterminal.exe", "openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wsl.exe",
    "wslhost.exe", "bash.exe", "mintty.exe", "putty.exe", "kitty.exe", "alacritty.exe", "wezterm-gui.exe",
    "tabby.exe", "hyper.exe", "conemu.exe", "conemu64.exe", "mobaxterm.exe", "termius.exe", "warp.exe",
    "mstsc.exe", "msrdc.exe", "vmconnect.exe", "vmware.exe", "vmplayer.exe", "virtualboxvm.exe", "vncviewer.exe",
    "tvnviewer.exe", "anydesk.exe", "teamviewer.exe", "rustdesk.exe", "parsecd.exe",

    "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "vscodium.exe", "zed.exe",
    "idea64.exe", "pycharm64.exe", "webstorm64.exe", "clion64.exe", "goland64.exe", "rider64.exe",
    "phpstorm64.exe", "rubymine64.exe", "datagrip64.exe", "studio64.exe", "fleet.exe",

    "emacs.exe", "runemacs.exe", "emacsclientw.exe", "gvim.exe", "vim.exe", "nvim-qt.exe", "neovide.exe",
}

LINE_COPY_EXES = {"sublime_text.exe", "devenv.exe"}
NO_COPY_CLASSES = {
    "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow", "mintty", "PuTTY",
    "TscShellContainerClass", "VMPlayerFrame", "VMwareUnityHostWndClass",
}

SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow"}

def copy_allowed(window: Dict[str, Any]) -> Tuple[bool, str]:
    """May the clipboard fallback send Ctrl+C to this window? → (allowed, reason when not)."""
    cls = str(window.get("class") or "")
    exe = ntpath.basename(str(window.get("exe") or "")).lower()
    if cls in SHELL_CLASSES:
        return False, "shell"
    if cls in NO_COPY_CLASSES or exe in NO_COPY_EXES:
        return False, "terminal"
    return True, ""

_CONTROL = re.compile(r"[\x00-\x08\x0c\x0e-\x1f\x7f]")

def clean_text(text: Any) -> Tuple[str, bool]:
    """The captured text: ``\\r\\n`` → ``\\n``, Word's manual line break (``\\v``) → ``\\n``, other control
    characters (Word's table-cell marks ``\\a``, NULs) dropped, trailing whitespace off, cut to :data:`MAX_TEXT`."""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n")
    value = _CONTROL.sub("", value)
    if not value.strip():
        return "", False
    value = value.strip("\n").rstrip()
    truncated = len(value) > MAX_TEXT
    return value[:MAX_TEXT], truncated

def _result(text: str = "", source: str = "", reason: str = "", **extra: Any) -> Dict[str, Any]:
    out = {"text": text, "source": source, "reason": reason, "editable": None, "truncated": False,
           "hwnd": 0, "app": "", "title": "", "id": ""}
    out.update(extra)
    return out

class Backend:
    """What :func:`capture` needs from the system. :class:`WinBackend` on Windows; tests subclass it."""
    supported = False

    def own_pid(self) -> int:
        return os.getpid()

    def window(self, hwnd: int) -> Dict[str, Any]:
        """``{"pid", "class", "exe", "title"}`` of a top-level window."""
        return {}

    def uia_selection(self, hwnd: int, own_pid: int) -> Optional[Dict[str, Any]]:
        """``None`` = the focused element has no usable TextPattern (try the clipboard);
        ``{"text", "editable"}`` = what UI Automation says is selected (``""`` = nothing, trust it)."""
        return None

    def snapshot(self) -> Optional["Snapshot"]:
        return None

    def restore(self, snapshot: "Snapshot") -> bool:
        return False

    def discard(self, snapshot: Optional["Snapshot"]) -> None:
        """A snapshot that will not be restored: free what it owns (the enhanced-metafile copy is a GDI handle)."""

    def sequence(self) -> int:
        return 0

    def read_text(self) -> Optional[str]:
        return None

    def set_text(self, text: str) -> bool:
        return False

    def send_copy(self) -> bool:
        return False

    def send_paste(self) -> bool:
        return False

    def flush_input(self) -> bool:
        """After :meth:`send_copy`: wait (≤ :data:`INPUT_FLUSH`) until the system has delivered the injected keys
        to the program in front — our window may take the focus after that. ``False`` = not confirmed."""
        return True

    def modifiers_down(self) -> bool:
        return False

    def is_window(self, hwnd: int) -> bool:
        return False

class Snapshot:
    """Everything that was on the clipboard, in a form that can be put back.

    ``items``: ``[(format, bytes)]`` for HGLOBAL formats, ``[(14, handle)]`` for an enhanced metafile copy.
    ``lost``: formats that cannot be restored and have no synthesized replacement — when non-empty the
    clipboard fallback is not used at all (better no selection than a damaged clipboard).
    """

    def __init__(self, items: Sequence[Tuple[int, Any]] = (), lost: Sequence[int] = (), size: int = 0) -> None:
        self.items = list(items)
        self.lost = list(lost)
        self.size = size
        self.restored = False

    @property
    def complete(self) -> bool:
        return not self.lost

    @property
    def empty(self) -> bool:
        return not self.items and not self.lost

_state: Dict[str, Any] = {"backend": None, "last": None, "busy_until": 0.0, "pending": None}
_state_lock = threading.Lock()

def backend() -> Backend:
    with _state_lock:
        if _state["backend"] is None:
            if sys.platform == "win32":
                try:
                    _state["backend"] = WinBackend()
                except Exception as exc:     # pragma: no cover - Windows only
                    log.warning("selection capture unavailable: %s", exc)
                    _state["backend"] = Backend()
            else:
                _state["backend"] = Backend()
        return _state["backend"]

def set_backend(value: Optional[Backend]) -> None:
    """Tests: inject a fake backend (``None`` = back to the real one)."""
    with _state_lock:
        _state["backend"] = value
        _state["last"] = None
        _state["pending"] = None

def available() -> bool:
    return bool(backend().supported)

def _wait_modifiers(api: Backend, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while api.modifiers_down() and time.monotonic() < deadline:
        time.sleep(0.01)

class Capture:
    """One selection read (:func:`start`). Policy and UI Automation answers are ``done`` at once; the Ctrl+C fallback
    returns with the keys already delivered to the program and finishes (wait for the copy, read, restore) in a
    background thread — the window opens meanwhile and gets the selection through :meth:`then`."""

    def __init__(self, result: Optional[Dict[str, Any]] = None) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._result: Dict[str, Any] = {}
        self._callbacks: List[Any] = []
        self.copied_at = 0.0
        self.flushed = True
        if result is not None:
            self._finish(result)

    @property
    def done(self) -> bool:
        return self._event.is_set()

    @property
    def result(self) -> Dict[str, Any]:
        """The result when :attr:`done`, else ``{}``."""
        return dict(self._result) if self.done else {}

    def wait(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        if not self._event.wait(timeout):
            return _result(reason="pending")
        return dict(self._result)

    def settle(self) -> None:
        """Call right before our window takes the focus: if the system did not confirm that Ctrl+C reached the
        program, give it :data:`SETTLE_GAP` since the keys were sent (a key still queued would go to our window)."""
        if self.copied_at and not self.flushed:
            left = self.copied_at + SETTLE_GAP - time.monotonic()
            if left > 0:
                time.sleep(left)

    def then(self, callback: Any) -> None:
        """``callback(result)`` when the read is finished — right now (in this thread) if it already is."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        self._call(callback)

    def _call(self, callback: Any) -> None:
        try:
            callback(dict(self._result))
        except Exception as exc:
            log.debug("selection: callback failed: %s", exc)

    def _finish(self, result: Dict[str, Any]) -> None:
        with self._lock:
            self._result = dict(result)
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            self._call(callback)

def capture(foreground: int, prefs: Optional[Dict[str, Any]] = None, *, api: Optional[Backend] = None) -> Dict[str, Any]:
    """The text selected in ``foreground`` — :func:`start` and wait for the result (blocking)."""
    return start(foreground, prefs, api=api).wait(COPY_TIMEOUT + 2.0)

def start(foreground: int, prefs: Optional[Dict[str, Any]] = None, *, api: Optional[Backend] = None) -> Capture:
    """Begin reading the text selected in ``foreground`` (call BEFORE showing the Quick Ask window).

    Returns at once for everything but the Ctrl+C fallback; that one returns as soon as Ctrl+C has reached the
    program (≈ the snapshot + a few milliseconds) and finishes in the background — :meth:`Capture.then`.

    ``reason`` when nothing was captured: ``off`` · ``unsupported`` · ``no_window`` · ``own`` (MemuAI's window) ·
    ``shell`` · ``empty`` (UIA: nothing selected / Ctrl+C copied nothing) · ``no_text`` (copied, but not text) ·
    ``terminal`` / ``clipboard_off`` / ``clipboard_unsafe`` / ``clipboard_busy`` (fallback not used).
    """
    prefs = prefs or {}
    api = api or backend()
    if not prefs.get("quick_ask_selection", True):
        return Capture(_result(reason="off"))
    if not api.supported:
        return Capture(_result(reason="unsupported"))
    if not foreground:
        return Capture(_result(reason="no_window"))
    window = api.window(foreground) or {}
    own = api.own_pid()
    if window.get("pid") == own:
        return Capture(_result(reason="own"))
    if str(window.get("class") or "") in SHELL_CLASSES:
        return Capture(_result(reason="shell"))
    meta = {"hwnd": int(foreground), "app": ntpath.basename(str(window.get("exe") or "")),
            "title": str(window.get("title") or "")[:200], "id": uuid.uuid4().hex[:12]}
    found = None
    try:
        found = api.uia_selection(foreground, own)
    except Exception as exc:
        log.debug("selection: uia failed: %s", exc)
    if found is not None:
        text, truncated = clean_text(found.get("text"))
        if not text:
            return Capture(_remember(_result(reason="empty", source="uia", **meta)))
        return Capture(_remember(_result(text, "uia", editable=found.get("editable"), truncated=truncated, **meta)))
    if not prefs.get("quick_ask_selection_clipboard", True):
        return Capture(_result(reason="clipboard_off", **meta))
    allowed, why = copy_allowed(window)
    if not allowed:
        return Capture(_result(reason=why, **meta))
    line_copy = meta["app"].lower() in LINE_COPY_EXES
    return _start_copy(api, meta, line_copy=line_copy)

def _start_copy(api: Backend, meta: Dict[str, Any], *, line_copy: bool = False) -> Capture:

    previous = _state.get("pending")
    if previous is not None and not previous.done and previous.wait(COPY_TIMEOUT + 1.5).get("reason") == "pending":
        return Capture(_result(reason="clipboard_busy", **meta))
    snapshot = api.snapshot()
    if snapshot is None:
        return Capture(_result(reason="clipboard_busy", **meta))
    if not snapshot.complete:
        api.discard(snapshot)
        return Capture(_result(reason="clipboard_unsafe", **meta))

    _wait_modifiers(api, MODIFIER_WAIT)
    before = api.sequence()
    _state["busy_until"] = time.monotonic() + COPY_TIMEOUT + 1.0
    if not api.send_copy():
        api.discard(snapshot)
        return Capture(_result(reason="input_blocked", **meta))
    pending = Capture()
    pending.copied_at = time.monotonic()
    try:
        pending.flushed = bool(api.flush_input())
    except Exception as exc:
        log.debug("selection: input flush failed: %s", exc)
        pending.flushed = False
    _state["pending"] = pending

    def finish() -> None:
        result = _result(reason="error", **meta)
        try:
            result = _finish_copy(api, meta, snapshot, before, line_copy)
        except Exception as exc:
            log.debug("selection: clipboard read failed: %s", exc)
        finally:

            if not snapshot.restored:
                api.discard(snapshot)
            pending._finish(_remember(result))
    threading.Thread(target=finish, name="memuai-selection-copy", daemon=True).start()
    return pending

def _finish_copy(api: Backend, meta: Dict[str, Any], snapshot: "Snapshot", before: int, line_copy: bool) -> Dict[str, Any]:
    deadline = time.monotonic() + COPY_TIMEOUT
    while api.sequence() == before and time.monotonic() < deadline:
        time.sleep(0.008)
    if api.sequence() == before:

        return _result(reason="empty", **meta)
    text = None
    for attempt in range(2):
        time.sleep(0.015)
        changed = api.sequence()
        text = api.read_text()
        if api.sequence() == changed:
            break
    restored = api.sequence() == changed and api.restore(snapshot)
    if not restored:
        log.debug("selection: clipboard changed by someone else meanwhile — not restored")
    if line_copy and re.fullmatch(r"[^\r\n]*\r?\n", str(text or "")):
        return _result(reason="empty", source="clipboard", **meta)
    text, truncated = clean_text(text)
    if not text:
        return _result(reason="no_text", source="clipboard", **meta)
    return _result(text, "clipboard", truncated=truncated, restored=restored, **meta)

def _remember(result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("text"):
        with _state_lock:
            _state["last"] = dict(result)
    return result

def clipboard_busy() -> bool:
    """``True`` while the Ctrl+C fallback owns the clipboard (the copy, the read, the restore — and a second after):
    clipboard watchers (the clipboard history of quick features) should skip changes made in that window."""
    return time.monotonic() < float(_state.get("busy_until") or 0.0)

def last() -> Optional[Dict[str, Any]]:
    with _state_lock:
        return dict(_state["last"]) if _state["last"] else None

def public(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What the page gets about the selection (never the window handle)."""
    if not result or not result.get("text"):
        return None
    return {"id": result.get("id") or "", "source": result.get("source") or "", "app": result.get("app") or "",
            "title": result.get("title") or "", "editable": result.get("editable"),
            "truncated": bool(result.get("truncated")), "chars": len(result.get("text") or "")}

def replace(manager: Any, text: str, selection_id: str, *, api: Optional[Backend] = None) -> Dict[str, Any]:
    """«Заменить выделение»: paste ``text`` over the selection in the program it came from.

    The selection must be the one captured last (``selection_id``) and its window must still exist. The
    user's clipboard is put back :data:`RESTORE_AFTER_PASTE` seconds after the paste, unless something
    else changed the clipboard meanwhile.
    """
    api = api or backend()
    text = str(text or "")
    source = last()
    if not api.supported:
        return {"ok": False, "code": "unsupported"}
    if not text.strip():
        return {"ok": False, "code": "empty"}
    if not source or not selection_id or source.get("id") != selection_id:
        return {"ok": False, "code": "stale"}
    hwnd = int(source.get("hwnd") or 0)
    if not hwnd or not api.is_window(hwnd):
        return {"ok": False, "code": "gone"}
    snapshot = api.snapshot()
    _state["busy_until"] = time.monotonic() + 1.5 + RESTORE_AFTER_PASTE
    if not api.set_text(text):
        api.discard(snapshot)
        return {"ok": False, "code": "clipboard_busy"}
    ours = api.sequence()
    if manager is not None:
        manager.previous_hwnd = hwnd
        manager.hide(restore_focus=True)
    _wait_modifiers(api, 0.5)
    time.sleep(0.06)
    if not api.send_paste():
        api.discard(snapshot)
        return {"ok": False, "code": "input_blocked"}
    if snapshot is not None and snapshot.complete:
        def put_back() -> None:
            time.sleep(RESTORE_AFTER_PASTE)
            if not (api.sequence() == ours and api.restore(snapshot)) and not snapshot.restored:
                api.discard(snapshot)
        threading.Thread(target=put_back, name="memuai-selection-restore", daemon=True).start()
    else:
        api.discard(snapshot)
    with _state_lock:
        _state["last"] = None
    return {"ok": True}

def status(prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    api = backend()
    prefs = prefs or {}
    return {"available": bool(api.supported), "platform": sys.platform,
            "enabled": bool(prefs.get("quick_ask_selection", True)),
            "clipboard": bool(prefs.get("quick_ask_selection_clipboard", True)),
            "uia": bool(getattr(api, "uia_ready", False))}

def warm() -> None:
    """Load the UI Automation assemblies in the background (the first read would otherwise miss its timeout)."""
    api = backend()
    if api.supported and hasattr(api, "warm"):
        threading.Thread(target=api.warm, name="memuai-selection-warm", daemon=True).start()

def register(app: Any, prefs: Any) -> None:
    """``GET /api/quick/selection/status`` — called from ``quick_ask_api.register``."""
    import asyncio

    @app.get("/api/quick/selection/status")
    async def quick_selection_status():
        return await asyncio.to_thread(status, prefs())

CF_TEXT, CF_BITMAP, CF_METAFILEPICT, CF_OEMTEXT, CF_DIB, CF_PALETTE = 1, 2, 3, 7, 8, 9
CF_UNICODETEXT, CF_ENHMETAFILE, CF_LOCALE, CF_DIBV5 = 13, 14, 16, 17
CF_OWNERDISPLAY, CF_DSPTEXT, CF_DSPBITMAP, CF_DSPMETAFILEPICT, CF_DSPENHMETAFILE = 0x80, 0x81, 0x82, 0x83, 0x8E
VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN = 0x11, 0x10, 0x12, 0x5B, 0x5C
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5

VK_MASK = 0xE8
VK_C, VK_V = 0x43, 0x56

SYNTHESIZED = {CF_TEXT: (CF_UNICODETEXT,), CF_OEMTEXT: (CF_UNICODETEXT,), CF_BITMAP: (CF_DIB, CF_DIBV5),
               CF_PALETTE: (CF_DIB, CF_DIBV5), CF_METAFILEPICT: (CF_ENHMETAFILE,), CF_DIB: (CF_DIBV5,)}

def plan_snapshot(formats: Sequence[int]) -> Tuple[List[int], List[int]]:
    """Which formats to save (bytes or EMF copy) and which cannot be restored (→ the fallback is not used).

    Pure (unit-tested): a synthesized format is skipped when its source is saved; GDI handles, owner-display and
    private-handle ranges cannot be copied.
    """
    present = set(int(f) for f in formats)
    save: List[int] = []
    lost: List[int] = []
    for fmt in formats:
        fmt = int(fmt)
        sources = SYNTHESIZED.get(fmt)
        if sources and any(src in present for src in sources):
            continue
        if fmt in (CF_BITMAP, CF_PALETTE, CF_METAFILEPICT, CF_OWNERDISPLAY, CF_DSPBITMAP, CF_DSPMETAFILEPICT,
                   CF_DSPENHMETAFILE) or 0x200 <= fmt <= 0x3FF:
            lost.append(fmt)
            continue
        save.append(fmt)
    return save, lost

class WinBackend(Backend):  # pragma: no cover - needs Windows
    """ctypes + the managed UI Automation client (pythonnet, as in ``quick_ask_browser``).

    ``SendInput`` and the INPUT layout come from ``assistant_actions_win._W`` (the same bindings Computer Use
    and the commands use); the extra clipboard calls are bound here.
    """
    supported = True

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt
        from .assistant_actions_win import _W
        self.ctypes, self.wt = ctypes, wt
        self.w = _W.get()
        u = self.w.user32

        def sig(dll, name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self.EnumClipboardFormats = sig(u, "EnumClipboardFormats", wt.UINT, wt.UINT)
        self.GetClipboardSequenceNumber = sig(u, "GetClipboardSequenceNumber", wt.DWORD)
        self.RegisterClipboardFormatW = sig(u, "RegisterClipboardFormatW", wt.UINT, wt.LPCWSTR)
        self.GetAsyncKeyState = sig(u, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
        gdi32 = ctypes.WinDLL("gdi32")
        self.CopyEnhMetaFileW = sig(gdi32, "CopyEnhMetaFileW", wt.HANDLE, wt.HANDLE, wt.LPCWSTR)
        self.DeleteEnhMetaFile = sig(gdi32, "DeleteEnhMetaFile", wt.BOOL, wt.HANDLE)
        self.GlobalSize = self.w.GlobalSize
        self._uia = None
        self.uia_ready = False
        self._uia_lock = threading.Lock()
        self._uia_failed = False

    def window(self, hwnd: int) -> Dict[str, Any]:
        from . import quick_ask_browser as qb
        w = self.w
        pid = self.wt.DWORD(0)
        w.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        buffer = self.ctypes.create_unicode_buffer(256)
        w.GetClassNameW(hwnd, buffer, 256)
        info = {}
        try:
            info = qb.foreground_info(hwnd) or {}
        except Exception:
            info = {}
        return {"pid": int(pid.value), "class": buffer.value, "exe": info.get("exe") or "", "title": info.get("title") or ""}

    def is_window(self, hwnd: int) -> bool:
        return bool(self.w.IsWindow(hwnd))

    def warm(self) -> None:
        self._uia_client()

    def _uia_client(self):
        if self._uia is not None or self._uia_failed:
            return self._uia
        with self._uia_lock:
            if self._uia is None and not self._uia_failed:
                try:
                    self._uia = _ManagedSelection()
                    self.uia_ready = True
                except Exception as exc:
                    log.debug("selection: UI Automation unavailable: %s", exc)
                    self._uia_failed = True
        return self._uia

    def uia_selection(self, hwnd: int, own_pid: int) -> Optional[Dict[str, Any]]:
        box: Dict[str, Any] = {}

        def run() -> None:
            client = self._uia_client()
            if client is not None:
                box["value"] = client.selection(own_pid)
        thread = threading.Thread(target=run, name="memuai-selection-uia", daemon=True)
        thread.start()
        thread.join(UIA_TIMEOUT)
        return box.get("value")

    def _open(self, tries: int = 12) -> bool:
        for _ in range(tries):
            if self.w.OpenClipboard(None):
                return True
            time.sleep(0.012)
        return False

    def sequence(self) -> int:
        return int(self.GetClipboardSequenceNumber())

    def snapshot(self) -> Optional[Snapshot]:
        ctypes, w = self.ctypes, self.w
        if not self._open():
            return None
        try:
            formats, fmt = [], 0
            while True:
                fmt = self.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                formats.append(int(fmt))
            save, lost = plan_snapshot(formats)
            items: List[Tuple[int, Any]] = []
            total = 0
            deadline = time.monotonic() + SNAPSHOT_BUDGET
            for index, fmt in enumerate(save):
                if time.monotonic() > deadline:
                    return Snapshot(items, lost + save[index:], total)
                handle = w.GetClipboardData(fmt)
                if not handle:
                    continue
                if fmt == CF_ENHMETAFILE:
                    copy = self.CopyEnhMetaFileW(handle, None)
                    if copy:
                        items.append((fmt, copy))
                    else:
                        lost.append(fmt)
                    continue
                size = int(self.GlobalSize(handle) or 0)
                pointer = w.GlobalLock(handle) if size else None
                if not pointer:
                    lost.append(fmt)
                    continue
                try:
                    total += size
                    if total > MAX_SNAPSHOT_BYTES:
                        return Snapshot(items, lost + [fmt], total)
                    items.append((fmt, ctypes.string_at(pointer, size)))
                finally:
                    w.GlobalUnlock(handle)
            return Snapshot(items, lost, total)
        finally:
            w.CloseClipboard()

    def _put(self, fmt: int, data: bytes) -> bool:
        w, ctypes = self.w, self.ctypes
        handle = w.GlobalAlloc(0x0002, max(1, len(data)))
        if not handle:
            return False
        pointer = w.GlobalLock(handle)
        if not pointer:
            w.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, len(data))
        w.GlobalUnlock(handle)
        if not w.SetClipboardData(fmt, handle):
            w.GlobalFree(handle)
            return False
        return True

    def _private_marks(self) -> None:

        zero = (0).to_bytes(4, "little")
        for name in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                     "CanUploadToCloudClipboard"):
            fmt = self.RegisterClipboardFormatW(name)
            if fmt:
                self._put(int(fmt), zero)

    def restore(self, snapshot: Snapshot) -> bool:
        if snapshot.restored or not self._open():
            return False
        try:
            self.w.EmptyClipboard()
            ok = True
            for fmt, data in snapshot.items:
                if fmt == CF_ENHMETAFILE:
                    placed = bool(self.w.SetClipboardData(fmt, data))
                    if not placed:
                        self.DeleteEnhMetaFile(data)
                    ok = placed and ok
                else:
                    ok = self._put(fmt, data) and ok
            if snapshot.items:
                self._private_marks()
            snapshot.restored = True
            return ok
        finally:
            self.w.CloseClipboard()

    def discard(self, snapshot: Optional[Snapshot]) -> None:
        if snapshot is None or snapshot.restored:
            return
        snapshot.restored = True
        for fmt, data in snapshot.items:
            if fmt == CF_ENHMETAFILE and data:
                try:
                    self.DeleteEnhMetaFile(data)
                except Exception:
                    pass

    def read_text(self) -> Optional[str]:
        if not self._open(tries=16):
            return None
        try:
            w = self.w
            if not w.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = w.GetClipboardData(CF_UNICODETEXT)
            pointer = w.GlobalLock(handle) if handle else None
            if not pointer:
                return ""
            try:
                size = int(self.GlobalSize(handle) or 0) // 2
                return self.ctypes.wstring_at(pointer, size).split("\x00", 1)[0] if size else self.ctypes.wstring_at(pointer)
            finally:
                w.GlobalUnlock(handle)
        finally:
            self.w.CloseClipboard()

    def set_text(self, text: str) -> bool:
        if not self._open():
            return False
        try:
            self.w.EmptyClipboard()
            return self._put(CF_UNICODETEXT, (str(text) + "\0").encode("utf-16-le"))
        finally:
            self.w.CloseClipboard()

    def _down(self, vk: int) -> bool:
        return bool(self.GetAsyncKeyState(vk) & 0x8000)

    def modifiers_down(self) -> bool:
        return any(self._down(vk) for vk in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN))

    def _chord(self, key: int) -> bool:
        from .assistant_actions_win import ActionError, WinBackend as Keys
        keys = Keys()
        held = [vk for vk in (VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN, VK_LSHIFT, VK_RSHIFT) if self._down(vk)]
        inputs = []
        if any(vk in held for vk in (VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)):
            inputs += [keys._key(VK_MASK), keys._key(VK_MASK, True)]
        inputs += [keys._key(vk, True) for vk in held]
        inputs += [keys._key(VK_CONTROL), keys._key(key), keys._key(key, True), keys._key(VK_CONTROL, True)]
        try:
            keys._send(inputs)
            return True
        except ActionError as exc:
            log.debug("selection: SendInput refused: %s", exc)
            return False

    def send_copy(self) -> bool:
        return self._chord(VK_C)

    def send_paste(self) -> bool:
        return self._chord(VK_V)

    def flush_input(self) -> bool:

        from .assistant_actions_win import ActionError, WinBackend as Keys
        keys = Keys()
        try:
            keys._send([keys._key(VK_MASK)])
        except ActionError:
            return False
        deadline = time.monotonic() + INPUT_FLUSH
        while not self._down(VK_MASK) and time.monotonic() < deadline:
            time.sleep(0.002)
        delivered = self._down(VK_MASK)
        try:
            keys._send([keys._key(VK_MASK, True)])
        except ActionError:
            pass
        return delivered

class _ManagedSelection:  # pragma: no cover - needs Windows + .NET
    """``AutomationElement.FocusedElement`` → TextPattern → ``GetSelection()`` (System.Windows.Automation)."""

    def __init__(self) -> None:
        import clr  # type: ignore
        clr.AddReference("UIAutomationClient")
        clr.AddReference("UIAutomationTypes")
        from System.Windows.Automation import (AutomationElement, SupportedTextSelection,  # type: ignore
                                               TextPattern, TreeWalker, ValuePattern)
        self.AE, self.TextPattern, self.TreeWalker = AutomationElement, TextPattern, TreeWalker
        self.ValuePattern, self.SupportedTextSelection = ValuePattern, SupportedTextSelection

    def selection(self, own_pid: int) -> Optional[Dict[str, Any]]:
        element = self.AE.FocusedElement
        if element is None:
            return None
        try:
            if int(element.Current.ProcessId) == int(own_pid):
                return None
        except Exception:
            return None
        walker = self.TreeWalker.ControlViewWalker
        node = element

        for _ in range(6):
            if node is None:
                break
            try:
                pattern = node.GetCurrentPattern(self.TextPattern.Pattern)
            except Exception:
                pattern = None
            if pattern is not None:
                if pattern.SupportedTextSelection == getattr(self.SupportedTextSelection, "None"):
                    return None
                parts = []
                size = 0
                for text_range in pattern.GetSelection() or []:
                    piece = str(text_range.GetText(MAX_TEXT + 1 - size) or "")
                    parts.append(piece)
                    size += len(piece)
                    if size > MAX_TEXT:
                        break
                return {"text": "\n".join(p for p in parts if p), "editable": self._editable(node)}
            try:
                node = walker.GetParent(node)
            except Exception:
                break
        return None

    def _editable(self, node) -> Optional[bool]:
        """ValuePattern.IsReadOnly when the element has it (an input, a web page document = read-only);
        ``None`` = unknown (a Word document has no ValuePattern) — «Заменить» is offered then."""
        try:
            value = node.GetCurrentPropertyValue(self.ValuePattern.IsReadOnlyProperty, True)
            if isinstance(value, bool):
                return not value
        except Exception:
            pass
        return None
