"""Input helpers: keyboard, completion, history, pastes and images.

**Shift+Enter.** prompt_toolkit reads the Windows console in VT mode, where
Enter and Shift+Enter are the same ``\\r``. MemuCode switches it back to the
classic ``ReadConsoleInput`` reader, which reports modifier keys, and turns
Shift+Enter into Meta+Enter — the "insert a newline" chord. Terminals that
report Shift+Enter as an escape sequence (kitty protocol ``CSI 13;2u``,
xterm ``modifyOtherKeys`` ``CSI 27;2;13~``) are mapped to the same chord.
Alt+Enter, Ctrl+J and a trailing backslash work everywhere.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys

from .. import paths

def install_escape_sequences() -> None:
    """Shift/Ctrl+Enter escape sequences of modern terminals → Meta+Enter."""
    try:
        from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    except ImportError:
        return
    for sequence in ("\x1b[13;2u", "\x1b[27;2;13~", "\x1b[13;5u", "\x1b[27;5;13~", "\x1b[13;3u"):
        ANSI_SEQUENCES.setdefault(sequence, (Keys.Escape, Keys.ControlM))

def _debug_keys(keys: List[KeyPress]) -> None:
    path = os.environ.get("MEMUCODE_DEBUG_KEYS")
    if path and keys:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                names = " ".join((press.key.name if isinstance(press.key, Keys) else repr(press.key))
                                 for press in keys[:60])
                handle.write(f"{time.monotonic():.3f} n={len(keys)} {names}\n")
        except OSError:
            pass

def is_paste_burst(keys: List[KeyPress]) -> bool:
    """A batch of key events that a human could not have typed: a paste or a drop.

    Typing (even fast, or backed up behind a slow redraw) delivers a few characters at a time and a
    trailing Enter; text after a newline, or a long run of characters, only comes from the clipboard.
    """
    printable = 0
    newline_seen = False
    text_after_newline = False
    for press in keys:
        if press.key in (Keys.ControlM, Keys.ControlJ):
            newline_seen = True
        elif not isinstance(press.key, Keys):
            printable += 1
            if newline_seen:
                text_after_newline = True
    return printable >= 8 or (text_after_newline and printable >= 2)

def keys_to_paste(keys: List[KeyPress]) -> List[KeyPress]:
    """Merge the text keys of a burst into ``BracketedPaste`` events (other keys stay in order)."""
    out: List[KeyPress] = []
    text: List[str] = []

    def flush() -> None:
        if text:
            out.append(KeyPress(Keys.BracketedPaste, "".join(text)))
            text.clear()

    skip = False
    for index, press in enumerate(keys):
        if skip:
            skip = False
            continue
        if press.key in (Keys.ControlM, Keys.ControlJ):
            text.append("\n")
        elif press.key == Keys.ControlI:
            text.append("\t")
        elif press.key == Keys.Escape and index + 1 < len(keys) and keys[index + 1].key in (Keys.ControlM, Keys.ControlJ):
            text.append("\n")
            skip = True
        elif not isinstance(press.key, Keys):
            text.append(press.data or str(press.key))
        else:
            flush()
            out.append(press)
    flush()
    return out

def create_input():
    """prompt_toolkit input that can tell Shift+Enter from Enter on Windows and understands pastes.

    On Windows the console delivers a paste as a stream of key events, and a long one arrives in several
    batches. prompt_toolkit's own heuristic looks at one batch at a time, so a big paste became a "Pasted
    text" chip followed by the rest of the text typed in. Here every burst is gathered until the input
    goes quiet and is delivered as ONE paste event.
    """
    from prompt_toolkit.input import create_input as default_input
    install_escape_sequences()
    created = default_input()
    if sys.platform != "win32":
        return created
    try:
        from prompt_toolkit.input.win32 import ConsoleInputReader, Win32Input
    except ImportError:
        return created
    if not isinstance(created, Win32Input):
        return created

    class ModifierAwareReader(ConsoleInputReader):
        def __init__(self) -> None:
            super().__init__(recognize_paste=False)

        def _event_to_key_presses(self, ev):  # type: ignore[override]
            result = super()._event_to_key_presses(ev)
            try:
                state = ev.ControlKeyState
                if ev.VirtualKeyCode == 13 and state & self.SHIFT_PRESSED and not (
                        state & (self.LEFT_CTRL_PRESSED | self.RIGHT_CTRL_PRESSED)):
                    return [KeyPress(Keys.Escape, ""), KeyPress(Keys.ControlM, "\r")]
            except Exception:
                pass
            return result

        def read(self):  # type: ignore[override]
            keys = list(super().read())
            _debug_keys(keys)
            if not keys or not is_paste_burst(keys):
                return keys
            import ctypes
            kernel32 = ctypes.windll.kernel32
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and len(keys) < 4_000_000:
                if kernel32.WaitForSingleObject(self.handle, 35) != 0:
                    break
                more = list(super().read())
                if not more:
                    break
                keys.extend(more)
            return keys_to_paste(keys)

    try:
        created._use_virtual_terminal_input = False
        created.console_input_reader = ModifierAwareReader()
    except Exception:
        pass
    return created

def fuzzy_score(query: str, candidate: str) -> Optional[float]:
    """Subsequence match with bonuses for word starts, basenames and contiguity."""
    if not query:
        return 0.0
    q, c = query.lower(), candidate.lower()
    if q in c:
        base = c.rfind("/") + 1
        bonus = 30 if c.startswith(q, base) else (15 if c.find(q) >= base else 0)
        return 100 + bonus - len(c) * 0.05 - c.find(q) * 0.1
    position, score, streak = 0, 0.0, 0
    for char in q:
        found = c.find(char, position)
        if found < 0:
            return None
        streak = streak + 1 if found == position else 0
        boundary = found == 0 or c[found - 1] in "/_-. "
        score += 1 + streak * 2 + (4 if boundary else 0)
        position = found + 1
    return score - len(c) * 0.05

@dataclass
class CommandItem:
    name: str
    description: str
    hint: str = ""
    aliases: Tuple[str, ...] = ()
    source: str = ""

_WORD_SPLIT = re.compile(r"[\s_/:@\-]+")

def _is_dir(entry: "os.DirEntry") -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False

def _model_parts(item: Any) -> Tuple[str, str, str]:
    """(id, provider, label) of a model given as a plain id or as an object with those attributes."""
    if isinstance(item, str):
        model_id = item.lstrip("@")
        provider = model_id.split(":", 1)[0] if item.startswith("@") and ":" in item else model_id.split("-", 1)[0]
        return item, provider, ""
    return (str(getattr(item, "id", "") or ""), str(getattr(item, "provider", "") or ""),
            str(getattr(item, "label", "") or ""))

def _pretty_name(item: Any, model_id: str) -> str:
    """Round 33 (r33c-models-lists): the readable model name (catalogue ``name`` or ``app/model_names.py``)."""
    name = str(getattr(item, "name", "") or "") if not isinstance(item, str) else ""
    if name:
        return name
    try:
        from ... import model_names
        return model_names.pretty(model_id.lstrip("@"))
    except Exception:
        return ""

def match_models(query: str, items: Iterable[Any], limit: int = 60) -> List[Tuple[float, Any]]:
    """Models that fit what was typed, best first.

    A model is found by its provider prefix (``antigravity`` → every Antigravity model), by the start of any
    word of its name (``glm`` → every GLM model, whatever the provider), by a piece of a word, or by its
    label. Several words must all match (``gemini flash``). Letters in order (``g3f``) are the last resort.
    """
    tokens = [token for token in query.lower().split() if token]
    rows: List[Tuple[float, Any]] = []
    for item in items:
        model_id, provider, label = _model_parts(item)
        if not model_id:
            continue
        low, prov, lab = model_id.lower(), provider.lower(), label.lower()
        words = [word for word in _WORD_SPLIT.split(low) if word]
        label_words = [word for word in _WORD_SPLIT.split(lab) if word]
        total = 0.0
        for token in tokens:
            if low == token or low.lstrip("@") == token:
                score = 1000.0
            elif low.startswith(token) or low.lstrip("@").startswith(token):
                score = 600.0
            elif prov.startswith(token):
                score = 500.0
            elif any(word.startswith(token) for word in words):
                score = 400.0
            elif any(word.startswith(token) for word in label_words):
                score = 300.0
            elif token in low:
                score = 200.0
            elif token in lab:
                score = 150.0
            else:
                fuzzy = fuzzy_score(token, low) if len(token) >= 3 else None
                if fuzzy is None:
                    total = -1.0
                    break
                score = 50.0 + min(40.0, fuzzy)
            total += score
        if tokens and total < 0:
            continue
        rows.append((total - len(low) * 0.01, item))
    if not tokens:
        rows.sort(key=lambda row: (_model_parts(row[1])[1].lower(), _model_parts(row[1])[0].lower()))
    else:
        rows.sort(key=lambda row: (-row[0], _model_parts(row[1])[1], _model_parts(row[1])[0]))
    return rows[:limit]

class MemuCompleter(Completer):
    """``/`` → commands, ``@`` → project files and folders, ``@agent-…`` → agents."""

    def __init__(self, commands: Callable[[], List[CommandItem]], files: Callable[[], List[str]],
                 agents: Callable[[], List[str]], root: Callable[[], Path] = Path.cwd,
                 models: Callable[[], List[Any]] = lambda: [], sessions: Callable[[], List[str]] = lambda: []) -> None:
        self.commands = commands
        self.files = files
        self.agents = agents
        self.root = root
        self.models = models
        self.sessions = sessions
        self._names: Tuple[Any, List[str], List[str], List[str]] = (None, [], [], [])

    MENTION = re.compile(r"(?:^|[\s(])@(\"[^\"]*|[^\s@\"]*)$")
    PATH_COMMANDS = ("/cd", "/add-dir", "/attach")

    def kind(self, text: str) -> str:
        """What the text before the cursor completes: ``command``, ``mention``, ``path``, ``argument`` or ``""``.

        ``mention`` and ``path`` look at the disk or score the whole file list, so the interface
        computes them on a worker thread; the other kinds are instant and are matched in place.
        """
        if text.startswith("/") and " " not in text and "\n" not in text:
            return "command"
        match = self.MENTION.search(text)
        if match:
            return "argument" if match.group(1).lstrip('"').startswith("agent-") else "mention"
        if text.startswith("/") and " " in text and "\n" not in text:
            return "path" if text.partition(" ")[0].lower() in self.PATH_COMMANDS else "argument"
        return ""

    def span(self, text: str) -> int:
        """How many characters before the cursor a chosen suggestion replaces."""
        kind = self.kind(text)
        if kind == "command":
            return len(text)
        match = self.MENTION.search(text)
        if match:
            return len(match.group(1)) + 1
        if kind in ("path", "argument"):
            return len(text.partition(" ")[2])
        return 0

    def get_completions(self, document: Document, complete_event=None,
                        cancelled: Callable[[], bool] = lambda: False) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text and "\n" not in text:
            yield from self._commands(text)
            return
        match = self.MENTION.search(text)
        if match:
            yield from self._mentions(match.group(1), cancelled)
            return
        if text.startswith("/") and " " in text and "\n" not in text:
            yield from self._arguments(text, cancelled)

    def _commands(self, text: str) -> Iterable[Completion]:
        query = text[1:].lower()
        scored: List[Tuple[float, CommandItem]] = []
        for item in self.commands():
            best = None
            for name in (item.name, *item.aliases):
                score = fuzzy_score(query, name) if query else 0.0
                if score is not None and name.lower().startswith(query):
                    score += 50
                if score is not None and (best is None or score > best):
                    best = score
            if best is None and query and len(query) >= 3 and query in item.description.lower():
                best = 1.0
            if best is not None:
                scored.append((best, item))
        scored.sort(key=lambda row: (-row[0], row[1].name) if query else (0, _command_rank(row[1]), row[1].name))
        for _score, item in scored[:60]:
            display = "/" + item.name + (f" {item.hint}" if item.hint else "")
            yield Completion("/" + item.name, start_position=-len(text), display=display,
                             display_meta=item.description)

    MENTION_ROWS = 40

    def _project_names(self) -> Tuple[List[str], List[str], List[str]]:
        """(folders, files, both lower-cased) — rebuilt only when the file index hands out a new list."""
        files = self.files()
        if self._names[0] is not files:

            seen = set()
            for rel in files:
                head = rel
                while "/" in head:
                    head = head.rsplit("/", 1)[0]
                    if head + "/" in seen:
                        break
                    seen.add(head + "/")
            folders = sorted(seen)
            self._names = (files, folders, files, [name.lower() for name in (*folders, *files)])
        return self._names[1], self._names[2], self._names[3]

    @staticmethod
    def _mention_token(value: str, is_folder: bool) -> str:
        """The text inserted for a suggestion. A folder with a space keeps its quote open, so the menu can
        go on listing what is inside it; a file closes the quote."""
        if " " not in value:
            return "@" + value
        return '@"' + value if is_folder else '@"' + value + '"'

    def _mention_paths(self, query: str) -> Iterable[Tuple[str, str]]:
        """(path, "folder" | "file") of the entries of the folder the typed text points into.

        ``src/`` lists ``src``, ``~/`` the home folder, ``C:/`` a drive: straight from the disk, so it is
        instant whatever the size of the project and finds files the project index leaves out.
        """
        text = query.replace("\\", "/")
        head, _slash, tail = text.rpartition("/")
        directory = head + "/" if head else ("/" if text.startswith("/") else "")
        if re.fullmatch(r"[A-Za-z]:", head):
            directory = head + "/"
        base = Path(os.path.expanduser(directory)) if directory else Path(".")
        if not base.is_absolute():
            base = self.root() / base
        try:
            with os.scandir(base) as handle:
                entries = sorted(handle, key=lambda entry: (not _is_dir(entry), entry.name.lower()))
        except OSError:
            return
        low = tail.lower()
        show_hidden = tail.startswith(".")
        starts, inside = [], []
        for entry in entries:
            name = entry.name
            if name.startswith(".") and not show_hidden:
                continue
            lowered = name.lower()
            row = (directory + name + ("/" if _is_dir(entry) else ""), "folder" if _is_dir(entry) else "file")
            if lowered.startswith(low):
                starts.append(row)
            elif low in lowered:
                inside.append(row)
        yield from (starts + inside)[:self.MENTION_ROWS]

    def _mentions(self, raw: str, cancelled: Callable[[], bool] = lambda: False) -> Iterable[Completion]:
        query = raw.lstrip('"')
        if query.startswith("agent-"):
            for name in self.agents():
                if name.startswith(query[6:]):
                    yield Completion("@agent-" + name, start_position=-(len(raw) + 1), display="@agent-" + name,
                                     display_meta="sub-agent")
            return
        start = -(len(raw) + 1)
        pathlike = "/" in query or "\\" in query or query.startswith("~") or bool(re.match(r"^[A-Za-z]:", query))
        shown: set = set()
        if pathlike:

            for value, kind in self._mention_paths(query):
                shown.add(value)
                yield Completion(self._mention_token(value, kind == "folder"), start_position=start, display=value,
                                 display_meta=kind)
            if re.match(r"^(~|[A-Za-z]:|/)", query.replace("\\", "/")):
                return
        folders, files, lowered = self._project_names()
        if not files and not pathlike:

            for value, kind in self._mention_paths(query):
                yield Completion(self._mention_token(value, kind == "folder"), start_position=start, display=value,
                                 display_meta=kind)
            return
        split = len(folders)

        def name_at(index: int) -> Tuple[str, str]:
            return (folders[index], "folder") if index < split else (files[index - split], "file")

        low = query.lower().replace("\\", "/")
        if not low:

            import heapq
            picked = heapq.nsmallest(self.MENTION_ROWS, range(len(lowered)), key=lambda i: (len(lowered[i]), i >= split))
            rows = [(0.0, *name_at(index)) for index in picked]
        else:

            hits = [index for index, name in enumerate(lowered) if low in name]
            rows = []
            for index in hits[:20_000]:
                rel, kind = name_at(index)
                rows.append(((fuzzy_score(query, rel) or 0.0) + (2 if kind == "folder" else 0), rel, kind))
            if len(hits) < self.MENTION_ROWS and not cancelled():
                seen = set(hits)
                first = low[0]
                for index, name in enumerate(lowered):
                    if index in seen or first not in name:
                        continue
                    if not index % 2000 and cancelled():
                        return
                    rel, kind = name_at(index)
                    score = fuzzy_score(query, rel)
                    if score is not None:
                        rows.append((score + (2 if kind == "folder" else 0), rel, kind))
            rows.sort(key=lambda row: (-row[0], len(row[1])))
        room = self.MENTION_ROWS - len(shown)
        for _score, rel, kind in rows[:self.MENTION_ROWS]:
            if rel in shown:
                continue
            if room <= 0:
                break
            room -= 1
            yield Completion(self._mention_token(rel, kind == "folder"), start_position=start, display=rel,
                             display_meta=kind)

    CHOICES = {
        "/goal": ("status", "pause", "continue", "clear"),
        "/theme": ("dark", "light", "dark-daltonized", "light-daltonized"),
        "/config": ("list", "get", "set", "unset", "edit"),
        "/cost": ("set", "refresh", "prices"),
        "/stats": ("7", "30", "all"),
        "/schedule": ("list", "new", "run", "delete", "pause", "resume"),
        "/skills": ("list", "new", "edit", "delete", "disable", "enable"),
        "/import": ("claude", "codex", "cursor", "agy", "grok", "gemini", "opencode", "all"),
        "/permissions": ("default", "acceptEdits", "plan", "auto", "bypassPermissions"),

        "/auto-continue": ("auto", "status", "cancel", "now", "+15m", "+30m", "+1h", "+2h", "+5h", "tomorrow 09:00"),
    }

    def model_matches(self, raw: str) -> List[Tuple[float, Any]]:
        """Models for ``/model <text>``: by provider, by name, by label (see :func:`match_models`)."""
        return match_models(raw.strip(), self.models(), limit=80)

    def _arguments(self, text: str, cancelled: Callable[[], bool] = lambda: False) -> Iterable[Completion]:
        name, _, raw = text.partition(" ")
        name = name.lower()
        if name in ("/cd", "/add-dir", "/attach"):
            yield from self._path_arguments(raw, directories_only=name != "/attach", cancelled=cancelled)
            return
        if name == "/model":
            for _score, item in self.model_matches(raw):
                model_id, provider, label = _model_parts(item)
                context = int(getattr(item, "context", 0) or 0)
                bits = [provider] if provider else []
                pretty = _pretty_name(item, model_id)
                if pretty and pretty != model_id:
                    bits.insert(0, pretty)
                if context:
                    bits.append(f"{context // 1_000_000}M" if context >= 1_000_000 and context % 1_000_000 == 0
                                else f"{context // 1000}k")
                if label and label.lower() != model_id.lower():
                    bits.append(label)
                yield Completion(model_id, start_position=-len(raw), display=model_id,
                                 display_meta=" · ".join(bits))
            return
        choices: Tuple[str, ...] = self.CHOICES.get(name, ())
        if name == "/resume":
            choices = tuple(self.sessions())
        elif name == "/help":
            choices = tuple(item.name for item in self.commands())
        low = raw.lower().strip()
        starts = [choice for choice in choices if choice.lower().startswith(low)]
        inside = [choice for choice in choices if low and low in choice.lower() and choice not in starts]
        for choice in (*starts, *inside):
            yield Completion(choice, start_position=-len(raw), display=choice)

    PATH_SCAN_LIMIT = 30000
    PATH_SCAN_SECONDS = 0.25
    PATH_ROWS = 300

    def _path_arguments(self, raw: str, *, directories_only: bool,
                        cancelled: Callable[[], bool] = lambda: False) -> Iterable[Completion]:

        raw = re.sub(r"\s+(?:--new|—new|--create|--mkdir|-n)\s*$", "", raw)
        query = raw.strip('"')
        typed = Path(query).expanduser() if query else Path(".")
        base = typed if query.endswith(("/", "\\")) else typed.parent
        if not base.is_absolute():
            base = self.root() / base
        prefix = "" if query.endswith(("/", "\\")) else typed.name.lower()
        found: List[Tuple[bool, str, str]] = []
        started = time.monotonic()
        try:

            with os.scandir(base) as entries:
                for count, entry in enumerate(entries):
                    if count >= self.PATH_SCAN_LIMIT or (not count % 64 and (cancelled() or
                                                                           time.monotonic() - started > self.PATH_SCAN_SECONDS)):
                        break
                    if prefix and not entry.name.lower().startswith(prefix):
                        continue
                    folder = _is_dir(entry)
                    if folder or not directories_only:
                        found.append((not folder, entry.name.lower(), entry.path))
                        if len(found) >= self.PATH_ROWS * 3:
                            break
        except OSError:
            return
        if cancelled():
            return
        found.sort()
        for not_folder, _name, value in found[:self.PATH_ROWS]:
            folder = not not_folder

            token = f'"{value}"' if " " in value else value
            yield Completion(token, start_position=-len(raw), display=value + (os.sep if folder else ""),
                             display_meta="folder" if folder else "file")

_ORDER = ["help", "model", "clear", "compact", "resume", "init", "review", "context", "usage", "cost",
          "permissions", "plan", "goal", "memory", "mcp", "settings", "doctor"]

def _command_rank(item: CommandItem) -> int:
    return _ORDER.index(item.name) if item.name in _ORDER else len(_ORDER) + (1 if item.source else 0)

class PromptHistory:
    """Prompts of this project first, then the rest (``history.jsonl``)."""

    LIMIT = 3000

    def __init__(self, project: str) -> None:
        self.project = project
        self.path = paths.history_file()
        self.items: List[str] = []
        self.index: Optional[int] = None
        self.draft = ""
        self._load()

    def _load(self) -> None:
        mine: List[str] = []
        other: List[str] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    text = str(row.get("t") or "")
                    if not text:
                        continue
                    (mine if row.get("p") == self.project else other).append(text)
        except OSError:
            pass
        self.items = _dedupe(other[-500:] + mine[-1500:])

    def add(self, text: str) -> None:
        text = text.rstrip()
        if not text or text.startswith("/exit"):
            return
        if self.items and self.items[-1] == text:
            self.index = None
            return
        self.items.append(text)
        self.index = None
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": text[:20000], "p": self.project, "ts": time.time()},
                                        ensure_ascii=False) + "\n")
            if self.path.stat().st_size > 4_000_000:
                self._trim()
        except OSError:
            pass

    def _trim(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()[-self.LIMIT:]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def previous(self, current: str) -> Optional[str]:
        if not self.items:
            return None
        if self.index is None:
            self.draft = current
            self.index = len(self.items)
        if self.index <= 0:
            return None
        self.index -= 1
        return self.items[self.index]

    def next(self) -> Optional[str]:
        if self.index is None:
            return None
        self.index += 1
        if self.index >= len(self.items):
            self.index = None
            return self.draft
        return self.items[self.index]

    def search(self, query: str, before: Optional[int] = None) -> Optional[Tuple[int, str]]:
        start = (len(self.items) if before is None else before) - 1
        low = query.lower()
        for index in range(start, -1, -1):
            if low in self.items[index].lower():
                return index, self.items[index]
        return None

def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in reversed(items):
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return list(reversed(out))

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp", ".bmp": "image/bmp"}

@dataclass
class Attachments:
    pastes: Dict[int, str] = field(default_factory=dict)
    images: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    files: Dict[int, Tuple[str, str]] = field(default_factory=dict)
    counter: int = 0

    def paste_token(self, text: str) -> str:
        self.counter += 1
        self.pastes[self.counter] = text
        lines = text.count("\n") + 1
        return f"[Pasted text #{self.counter} +{lines} lines]"

    def image_token(self, image: Dict[str, Any]) -> str:
        self.counter += 1
        self.images[self.counter] = image
        return f"[Image #{self.counter}]"

    def file_token(self, path: Path) -> str:
        if not path.is_file() or path.stat().st_size > 400_000:
            raise ValueError("Choose a text file smaller than 400 KB")
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            raise ValueError("This is a binary file; attach an image or a text file")
        content = raw.decode("utf-8", "replace")
        self.counter += 1
        self.files[self.counter] = (str(path.resolve()), content)
        name = path.name.replace("[", "(").replace("]", ")")
        return f"[File #{self.counter}: {name}]"

    def expand_pastes(self, text: str) -> str:
        """The message as it reads in the conversation: pasted text in full (the chip is only for
        the input box); pictures and files stay short chips."""
        return re.sub(r"\[Pasted text #(\d+) \+\d+ lines\]",
                      lambda match: self.pastes.get(int(match.group(1)), match.group(0)), text)

    def expand(self, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        """Replace paste tokens with their text; collect images in order of their tokens."""
        def paste(match: re.Match) -> str:
            return self.pastes.get(int(match.group(1)), match.group(0))

        expanded = re.sub(r"\[Pasted text #(\d+) \+\d+ lines\]", paste, text)
        def file_content(match: re.Match) -> str:
            item = self.files.get(int(match.group(1)))
            if not item:
                return match.group(0)
            name, content = item
            return f'<file path="{name}">\n{content}\n</file>'
        expanded = re.sub(r"\[File #(\d+): [^\]]+\]", file_content, expanded)
        images: List[Dict[str, Any]] = []
        for match in re.finditer(r"\[Image #(\d+)\]", expanded):
            image = self.images.get(int(match.group(1)))
            if image:
                images.append(image)
        return expanded, images

    def clear(self) -> None:
        self.pastes.clear()
        self.images.clear()
        self.files.clear()

def encode_image(image: Any, name: str = "image") -> Dict[str, Any]:
    """PIL image → a JPEG/PNG attachment no larger than 1568 px."""
    frame = image
    if frame.mode not in ("RGB", "RGBA", "L"):
        frame = frame.convert("RGBA")
    frame = frame.copy()
    frame.thumbnail((1568, 1568))
    buffer = io.BytesIO()
    if frame.mode == "RGBA":
        frame.save(buffer, format="PNG", optimize=True)
        media = "image/png"
    else:
        frame.convert("RGB").save(buffer, format="JPEG", quality=88)
        media = "image/jpeg"
    return {"media_type": media, "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "width": frame.size[0], "height": frame.size[1], "name": name}

def image_from_path(path: str) -> Optional[Dict[str, Any]]:
    file = Path(path.strip().strip('"').strip("'"))
    if file.suffix.lower() not in IMAGE_EXT or not file.is_file():
        return None
    try:
        from PIL import Image
        with Image.open(file) as image:
            image.load()
            return encode_image(image, file.name)
    except Exception:
        return None

def clipboard_image() -> Optional[Dict[str, Any]]:
    """An image from the clipboard (a screenshot, or a copied image file)."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    try:
        grabbed = ImageGrab.grabclipboard()
    except Exception:
        return None
    if grabbed is None:
        return None
    if isinstance(grabbed, list):
        for item in grabbed:
            found = image_from_path(str(item))
            if found:
                return found
        return None
    try:
        return encode_image(grabbed, "clipboard.png")
    except Exception:
        return None

def pasted_paths(text: str) -> List[str]:
    """Paths dropped into the terminal: ``"C:\\a b\\x.png" C:\\y.jpg``."""
    raw = text.strip()
    if len(raw) > 4096:
        return []
    try:
        if Path(raw.strip('"').strip("'")).exists():
            return [raw.strip('"').strip("'")]
    except OSError:
        return []
    if not raw or "\n" in raw.strip() and not all(Path(line.strip().strip('"')).exists() for line in raw.splitlines() if line.strip()):
        return []
    parts = re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', raw)
    paths_found = [next(group for group in groups if group) for groups in parts]
    if paths_found and all(Path(item).exists() for item in paths_found):
        return paths_found
    return []
