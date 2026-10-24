"""Tool calls for models that have no native function calling.

Most MemuAI accounts are web chats: they only produce text. The model calls
tools by ending its message with a hidden block (the same protocol as the
desktop MemuCode, so skills and habits carry over)::

    ```memuai-tool
    [{"tool": "Read", "args": {"path": "src/app.py"}}]
    ```

**The parser is forgiving because web models are.** It reads the JSON a model
actually writes, not the JSON it should have written: ``\\U`` in a Windows path,
raw line breaks and unescaped quotes inside a long ``content`` string, trailing
commas, single quotes, unquoted keys, ``args/arguments/parameters/input``,
string-encoded arguments, a block left unclosed at the end of the answer, plus
the foreign shapes ``<tool_call>{…}</tool_call>`` and Anthropic-style
``<invoke name="…"><parameter name="…">…</parameter></invoke>``. Calls that read
cleanly are kept even when a neighbour in the same block is broken, and a call
that was cut off by the end of the answer is reported and never executed (half a
file must not be written).

**Big files without escaping.** A file can follow the block as a raw content
block, referenced from the arguments::

    ```memuai-tool
    [{"tool": "Write", "args": {"file_path": "a.jsx", "content": "@content:1"}}]
    ```
    <memuai-content id="1">
    …the file exactly as it must be written…
    </memuai-content>

:class:`StreamHider` removes those blocks from the text while it streams, so
the user never sees raw JSON flash by. It does its work once per character — the
cost of a long block does not grow with the number of chunks it arrived in.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_CALLS_PER_ROUND = 16

_OPENER = re.compile(r"```[ \t]*memuai-tool\b[^\n]*\n?", re.I)
_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S | re.I)
_INVOKE = re.compile(r"<invoke\s+name=\"([^\"]+)\"\s*>(.*?)</invoke>", re.S | re.I)
_PARAM = re.compile(r"<parameter\s+name=\"([^\"]+)\"\s*>(.*?)</parameter>", re.S | re.I)
_FUNCTION_CALLS = re.compile(r"</?(?:antml:)?function_calls>", re.I)
_QWEN_FUNCTION = re.compile(r"<function=([\w.\-]{1,80})>(.*?)</function>", re.S)
_QWEN_PARAM = re.compile(r"<parameter=([\w.\-]{1,80})>(.*?)</parameter>", re.S)
#: ``<memuai-content id="1">`` — and the spellings a model drifts into: ``memuai_content``, ``id=1``, ``id=\"1\"``,
#: ``name="1"``, a bare ``1`` or no id at all (then the blocks are numbered by their order).
_CONTENT_OPEN = re.compile(r"<memuai[-_ ]?content\b([^<>\n]{0,160})>", re.I)
_CONTENT_CLOSE_TAG = re.compile(r"</\s*memuai[-_ ]?content\s*>", re.I)
_CONTENT_CLOSE = "</memuai-content>"
_CONTENT_ID = re.compile(r"""(?:\b(?:id|name|ref|n)\s*[=:]\s*\\?["']?|^\s*\\?["']?(?![\w.\-]*\s*=))([\w.\-]+)""", re.I)
_CONTENT_REF = re.compile(r"""^\s*@content\s*[:=]\s*\\?["']?([\w.\-]+)\\?["']?\s*$""")
_CONTENT_REF_ANY = re.compile(r"""@content\s*[:=]\s*\\?["']?([\w.\-]+)""")

#: Argument names the lenient parser trusts as "the next key starts here". The tool registry adds its own.
DEFAULT_KEYS = frozenset({
    "tool", "name", "args", "arguments", "parameters", "input", "id", "function", "recipient_name",
    "file_path", "path", "content", "old_string", "new_string", "replace_all", "edits", "command", "description",
    "timeout", "run_in_background", "pattern", "glob", "type", "output_mode", "head_limit", "query", "url",
    "prompt", "todos", "status", "activeForm", "subagent_type", "model", "offset", "limit", "question", "options",
    "action", "ref", "staged", "plan", "state", "levels", "task_id", "wait_seconds", "multi_select", "limit",
    "text", "task", "name", "value"})


#: Keys of a call object that are not arguments of the tool.
_CALL_META = frozenset({"tool", "name", "id", "function", "recipient_name", "type", "index", "call_id"})
#: Keys under which a call keeps its arguments.
_ARG_HOLDERS = ("args", "arguments", "parameters", "input", "params", "tool_input")


@dataclass
class ParsedCall:
    name: str
    args: Dict[str, Any]
    id: str = field(default_factory=lambda: "t" + uuid.uuid4().hex[:10])
    raw_name: str = ""
    notes: List[str] = field(default_factory=list)      # what had to be repaired to read this call


@dataclass
class ParseResult:
    text: str
    calls: List[ParsedCall]
    errors: List[str]
    repairs: List[str] = field(default_factory=list)
    cut_off: List[str] = field(default_factory=list)    # calls the answer ended in the middle of: never executed


# ---------------------------------------------------------------- lenient JSON

class LenientError(ValueError):
    def __init__(self, message: str, position: int) -> None:
        super().__init__(message)
        self.message = message
        self.position = position


class LenientJSON:
    """A reader for the JSON that language models write.

    It accepts what a strict parser refuses and says what it had to fix (``repairs``). When the text stops in
    the middle of a structure it closes it and sets ``incomplete`` — the caller decides what a half-finished
    value is worth. The one genuinely ambiguous thing is a ``"`` inside a string: it ends the string only if
    what follows could continue the JSON (``,`` and the next key, ``}``, ``]``, ``:``); otherwise it is text.
    """

    _NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
    _BARE = re.compile(r"[A-Za-z_$][\w$\-]*")
    _DOUBLE = re.compile(r'[^"\\]*')
    _SINGLE = re.compile(r"[^'\\]*")
    _HEX = re.compile(r"[0-9a-fA-F]{4}")
    _ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/", "\\": "\\", '"': '"', "'": "'"}
    _LITERALS = (("true", True), ("false", False), ("null", None), ("True", True), ("False", False),
                 ("None", None), ("undefined", None))
    DEPTH_LIMIT = 60

    def __init__(self, text: str, pos: int = 0, known_keys: Optional[Iterable[str]] = None, *,
                 relaxed: bool = False) -> None:
        self.s = text
        self.i = pos
        self.n = len(text)
        self.known = frozenset(known_keys) if known_keys is not None else DEFAULT_KEYS
        self.relaxed = relaxed
        self.repairs: Dict[str, int] = {}
        self.errors: List[Tuple[str, int]] = []                 # calls of the top array that could not be read
        self.incomplete = False
        self.depth = 0

    # ------------------------------------------------------------ helpers
    def note(self, what: str) -> None:
        self.repairs[what] = self.repairs.get(what, 0) + 1

    def repair_notes(self) -> List[str]:
        return [what if count == 1 else f"{what} ×{count}" for what, count in self.repairs.items()]

    def _skip(self) -> None:
        s, n = self.s, self.n
        i = self.i
        while i < n:
            c = s[i]
            if c in " \t\r\n\ufeff":
                i += 1
            elif c == "/" and i + 1 < n and s[i + 1] == "/":          # // comment
                end = s.find("\n", i)
                i = n if end < 0 else end + 1
                self.note("comment")
            elif c == "/" and i + 1 < n and s[i + 1] == "*":          # /* comment */
                end = s.find("*/", i + 2)
                i = n if end < 0 else end + 2
                self.note("comment")
            else:
                break
        self.i = i

    def _ws_from(self, k: int) -> int:
        s, n = self.s, self.n
        while k < n and s[k] in " \t\r\n":
            k += 1
        return k

    # ------------------------------------------------------------ values
    def value(self, ctx: str = "top") -> Any:
        self._skip()
        if self.i >= self.n:
            self.incomplete = True
            return None
        c = self.s[self.i]
        if c == "{":
            return self._object()
        if c == "[":
            return self._array()
        if c in "\"'":
            return self._string(c, ctx)
        if c in ",}]" and ctx == "object_value":
            self.note("missing value")                 # {"a": , "b": 1}  /  {"a": }
            return None
        match = self._NUMBER.match(self.s, self.i)
        if match:
            self.i = match.end()
            text = match.group(0)
            return float(text) if any(ch in text for ch in ".eE") else int(text)
        for word, result in self._LITERALS:
            if self.s.startswith(word, self.i):
                end = self.i + len(word)
                if end >= self.n or not (self.s[end].isalnum() or self.s[end] == "_"):
                    self.i = end
                    if word[0].isupper() or word == "undefined":
                        self.note("Python-style literal")
                    return result
        raise LenientError(f"unexpected text {self.s[self.i:self.i + 24]!r}", self.i)

    def _object(self) -> Dict[str, Any]:
        self.depth += 1
        if self.depth > self.DEPTH_LIMIT:
            raise LenientError("nested too deeply", self.i)
        self.i += 1
        out: Dict[str, Any] = {}
        while True:
            self._skip()
            if self.i >= self.n:
                self.incomplete = True
                break
            c = self.s[self.i]
            if c == "}":
                self.i += 1
                break
            if c == ",":
                self.i += 1
                self.note("extra comma")
                continue
            if c == "]" or (c == "{" and out):
                # A key is expected but a bracket of the structure around us (or the next object) starts: this
                # object was never closed. Close it here and let the enclosing structure carry on.
                self.note("missing }")
                break
            key = self._key()
            self._skip()
            if self.i >= self.n:
                self.incomplete = True
                break
            colon = self.s[self.i]
            if colon == ":":
                self.i += 1
            elif colon in ",=" or self.s.startswith("=>", self.i):
                # {"file_path", "a.py"}  /  {"file_path" = "a.py"}: the writer meant a colon.
                self.i += 2 if self.s.startswith("=>", self.i) else 1
                self.note("colon missing after a key")
            elif colon in "\"'[{" or colon == "-" or colon.isdigit():
                self.note("colon missing after a key")      # {"file_path" "a.py"}
            else:
                raise LenientError(f"expected ':' after the key {key!r}", self.i)
            out[key] = self.value("object_value")
            if self.incomplete:
                break
            self._skip()
            if self.i >= self.n:
                self.incomplete = True
                break
            c = self.s[self.i]
            if c == ",":
                self.i += 1
            elif c == "}":
                self.i += 1
                break
            elif c in "]{":
                self.note("missing }")                     # the object was not closed before the array ended
                break
            elif c in "\"'":
                self.note("missing comma")
            else:
                raise LenientError(f"expected ',' or '}}' but found {c!r}", self.i)
        self.depth -= 1
        return out

    _RESYNC = re.compile(r"\{\s*[\"']?(?:tool|name|function|recipient_name)[\"']?\s*:")

    def _array(self) -> List[Any]:
        top = self.depth == 0                                  # the array of calls itself
        self.depth += 1
        if self.depth > self.DEPTH_LIMIT:
            raise LenientError("nested too deeply", self.i)
        self.i += 1
        out: List[Any] = []
        while True:
            self._skip()
            if self.i >= self.n:
                self.incomplete = True
                break
            c = self.s[self.i]
            if c == "]":
                self.i += 1
                break
            if c == ",":
                self.i += 1
                self.note("extra comma")
                continue
            if c == "}":
                if top:
                    self.i += 1                                  # a closing brace too many at the end of a call
                    self.note("extra }")
                    continue
                self.note("missing ]")                           # the array was not closed: this brace is the object's
                break
            try:
                out.append(self.value("array_item"))
            except LenientError as exc:
                if not top:
                    raise
                # One call is unreadable: report it and carry on with the next call of the block.
                self.errors.append((exc.message, exc.position))
                found = self._RESYNC.search(self.s, exc.position + 1)
                if found is None:
                    self.i = self.n
                    break
                self.i = found.start()
                self.depth = 1
                continue
            if self.incomplete:
                break
            self._skip()
            if self.i >= self.n:
                self.incomplete = True
                break
            c = self.s[self.i]
            if c == ",":
                self.i += 1
            elif c == "]":
                self.i += 1
                break
            elif c == "}":
                if top:
                    self.i += 1
                    self.note("extra }")
                else:
                    self.note("missing ]")
                    break
            elif c in "{[\"'":
                self.note("missing comma")
            else:
                raise LenientError(f"expected ',' or ']' but found {c!r}", self.i)
        self.depth -= 1
        return out

    def _key(self) -> str:
        c = self.s[self.i]
        if c in "\"'":
            return self._string(c, "key")
        match = self._BARE.match(self.s, self.i)
        if match:
            self.i = match.end()
            self.note("unquoted key")
            return match.group(0)
        raise LenientError(f"expected a key but found {c!r}", self.i)

    # ------------------------------------------------------------ strings
    def _string(self, quote: str, ctx: str) -> str:
        s, n = self.s, self.n
        self.i += 1
        parts: List[str] = []
        chunk = self._DOUBLE if quote == '"' else self._SINGLE
        if quote == "'":
            self.note("single-quoted string")
        while True:
            match = chunk.match(s, self.i)
            raw = match.group(0)
            if raw:
                if "\n" in raw or "\r" in raw or "\t" in raw:
                    self.note("raw line break or tab in a string")
                parts.append(raw)
            self.i = match.end()
            if self.i >= n:
                self.incomplete = True
                break
            c = s[self.i]
            if c == "\\":
                self.i += 1
                if self.i >= n:
                    self.incomplete = True
                    break
                e = s[self.i]
                if e in self._ESCAPES:
                    parts.append(self._ESCAPES[e])
                    self.i += 1
                elif e == "u":
                    match = self._HEX.match(s, self.i + 1)
                    if match:
                        code = int(match.group(0), 16)
                        self.i += 5
                        if 0xD800 <= code < 0xDC00 and s.startswith("\\u", self.i):
                            low = self._HEX.match(s, self.i + 2)
                            if low and 0xDC00 <= int(low.group(0), 16) < 0xE000:
                                code = 0x10000 + ((code - 0xD800) << 10) + (int(low.group(0), 16) - 0xDC00)
                                self.i += 6
                        parts.append(chr(code) if not 0xD800 <= code < 0xE000 else "�")
                    else:
                        parts.append("\\u")
                        self.i += 1
                        self.note("invalid \\u escape")
                else:
                    # \U in a Windows path, \d in a regex, \$ in a template: the writer meant a backslash.
                    parts.append("\\" + e)
                    self.i += 1
                    self.note("invalid escape")
                continue
            # a quote of the same kind: does it end the string, or is it part of the text?
            if self._closes(self.i + 1, ctx):
                self.i += 1
                break
            parts.append(c)
            self.i += 1
            self.note("unescaped quote")
        return "".join(parts)

    def _after_value(self, k: int) -> bool:
        """After a closing bracket: what can follow a value (or the end of the text)."""
        k = self._ws_from(k)
        return k >= self.n or self.s[k] in ",}]" or self.s.startswith("```", k) or self.s.startswith("</", k)

    def _next_key_follows(self, k: int) -> bool:
        s = self.s
        k = self._ws_from(k)
        if k >= self.n:
            return True                          # the text stops after the comma: that string did end
        c = s[k]
        if c == "}":
            return True                          # a trailing comma
        if c not in "\"'":
            bare = self._BARE.match(s, k)        # an unquoted key: {tool: "Read", args: {…}}
            if bare is None:
                return False
            after = self._ws_from(bare.end())
            return s[after:after + 1] == ":" and (self.relaxed or bare.group(0) in self.known)
        end = s.find(c, k + 1, k + 90)
        if end < 0:
            return False
        key = s[k + 1:end]
        if "\n" in key:
            return False
        after = self._ws_from(end + 1)
        if s[after:after + 1] != ":":
            return False
        return self.relaxed or key in self.known

    def _closes(self, j: int, ctx: str) -> bool:
        """Does the quote just before ``j`` end the string being read?"""
        s, n = self.s, self.n
        k = self._ws_from(j)
        if k >= n:
            return True
        c = s[k]
        if ctx == "key":
            # A key is short and has no quotes inside; what follows it is a colon — or, when the writer slipped,
            # a comma / "=" / the value itself ({"file_path", "a.py"}).
            return c in ":,=\"'[{" or s.startswith("=>", k)
        if ctx == "object_value":
            if c == "}":
                return self._after_value(k + 1)
            if c == ",":
                return self._next_key_follows(k + 1)
            if c in "\"'" and not self.relaxed:
                return self._next_key_follows(k)                  # a comma was forgotten between two pairs
            return False
        if ctx == "array_item":
            if c == "]":
                return self._after_value(k + 1)
            if c == ",":
                k2 = self._ws_from(k + 1)
                return k2 >= n or s[k2] in "\"'{[]-0123456789tfnTFN"
            return False
        return True


_STRICT = json.JSONDecoder()


@dataclass
class ReadResult:
    value: Any
    end: int
    incomplete: bool
    repairs: List[str]
    errors: List[Tuple[str, int]]


def read_json(text: str, pos: int, known_keys: Optional[Iterable[str]] = None) -> ReadResult:
    """Read the JSON that starts at ``pos`` (after spaces).

    Two passes: first the next key of an object must be one the tools know (so a ``", "x": 1`` inside a long
    string is text); if that makes a string swallow the rest of the answer, a second pass trusts any key.

    JSON that is valid is read as JSON, before any of that: the guessing about where a string ends exists for text
    that is *not* valid, and applied to valid text it can only do harm (``"old_text"`` is not a key the tools know,
    so ``{"file_path": "a.py", "old_text": "x"}`` used to come out as one long file path)."""
    start = pos
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    if start < len(text) and text[start] in "{[":
        try:
            value, end = _STRICT.raw_decode(text, start)
            return ReadResult(value, end, False, [], [])
        except ValueError:
            pass
    first = LenientJSON(text, pos, known_keys)
    value = first.value()
    if not first.incomplete:
        return ReadResult(value, first.i, False, first.repair_notes(), first.errors)
    second = LenientJSON(text, pos, known_keys, relaxed=True)
    try:
        other = second.value()
    except LenientError:
        return ReadResult(value, first.i, True, first.repair_notes(), first.errors)
    return ReadResult(other, second.i, second.incomplete, second.repair_notes(), second.errors)


def describe_error(source: str, position: int, message: str, *, around: int = 60) -> str:
    """``line 3, column 7 (char 418): message — near: …text…`` for the model to see where a block went wrong."""
    position = max(0, min(position, len(source)))
    line = source.count("\n", 0, position) + 1
    column = position - (source.rfind("\n", 0, position) + 1) + 1
    near = source[max(0, position - around):position + around].replace("\n", "⏎")
    return f"line {line}, column {column} (char {position}): {message} — near: …{near}…"


# ---------------------------------------------------------------- calls

def _looks_like_arguments(row: Dict[str, Any], keys: Iterable[str]) -> bool:
    """An entry that has no tool name but carries argument names (``{"file_path": "a.py"}``)."""
    named = [key for key in row if key in keys and key not in _CALL_META and key not in _ARG_HOLDERS]
    return bool(named) and len(named) * 2 >= len(row)


def _looks_like_junk(row: Any, keys: Iterable[str]) -> bool:
    """An entry that is not an attempt at a call at all: not an object, or an object none of whose keys the
    protocol or any tool knows (``{"memuai-tool--redacted-arg--": "nope"}``)."""
    if not isinstance(row, dict):
        return True
    return not any(key in keys for key in row)


def normalize_call(row: Any) -> Optional[ParsedCall]:
    if isinstance(row, (list, tuple)) and 1 <= len(row) <= 2 and isinstance(row[0], str):
        row = {"tool": row[0], "args": row[1] if len(row) == 2 else {}}       # ["Read", {"file_path": "a.py"}]
    if not isinstance(row, dict):
        return None
    function = row.get("function") if isinstance(row.get("function"), dict) else {}
    raw_name = str(row.get("tool") or row.get("name") or function.get("name") or row.get("recipient_name") or "")
    raw_name = raw_name.strip().replace("functions.", "", 1)
    if raw_name.lower().startswith("memuai-tool") and ":" in raw_name:      # "memuai-tool:Read" / "memuai-tool: Read"
        raw_name = raw_name.split(":", 1)[1].strip()
    if not raw_name or not re.fullmatch(r"[A-Za-z_][\w.\-:]{0,120}", raw_name):
        return None
    args: Any = {}
    for key in _ARG_HOLDERS:
        if key in row:
            args = row[key]
            break
    else:
        if function.get("arguments") is not None:
            args = function["arguments"]
        else:
            # {"tool": "Read", "file_path": "a.py"}: the arguments sit next to the name instead of inside "args"
            meta = _CALL_META - {"name"} if row.get("tool") else _CALL_META    # {"tool": "Skill", "name": "x"}
            args = {key: value for key, value in row.items() if key not in meta}
    if isinstance(args, str):
        try:
            read = read_json(args, 0)
            args = read.value if not read.incomplete else {"value": args}
        except (LenientError, ValueError):
            args = {"value": args}
    if not isinstance(args, dict):
        args = {}
    return ParsedCall(name=raw_name, args=args, raw_name=raw_name,
                      id=str(row.get("id") or "") or "t" + uuid.uuid4().hex[:10])


#: Arguments that are text by nature: an XML-style call's "42" or "[1, 2]" for them is the text itself (a file with
#: the content 42, a command), never a number or a list.
_TEXT_ARGS = {"content", "old_string", "new_string", "new_text", "old_text", "text", "command", "file_path", "path",
              "pattern", "query", "prompt", "description", "url", "question", "plan", "glob", "name", "task"}


def _xml_value(value: str, key: str = "") -> Any:
    value = value.strip("\n")
    stripped = value.strip()
    if key in _TEXT_ARGS:
        return value
    if stripped[:1] in "[{" or stripped in ("true", "false", "null") or re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    return value


_FENCE_LINE = re.compile(r"^[ \t]*```", re.M)
_MISSING = "\0missing:"          # marks a reference whose content block did not arrive (see ``_describe_problems``)


def _describe_problems(problems: List[str], blocks: Dict[str, Tuple[str, bool]]) -> str:
    """One sentence for everything that was wrong with the content blocks of a call."""
    refs = [item[len(_MISSING):] for item in problems if item.startswith(_MISSING)]
    parts = [item for item in problems if not item.startswith(_MISSING)]
    if refs:
        named = ", ".join(f'"@content:{ref}"' for ref in refs)
        have = ", ".join(sorted(blocks)[:8])
        parts.insert(0, f"content block missing: the arguments say {named} but this message has "
                        + (f"no matching <memuai-content id=\"…\"> block (its blocks: {have})" if blocks else
                           "no <memuai-content> block at all")
                        + ". The block must follow the closing ``` of the tool block, in the same message")
    return "; ".join(parts)


def _unfence(body: str) -> Tuple[str, bool]:
    """A model that wraps the file it writes in a Markdown code fence inside the content block: the fence is not
    part of the file. Only the plain shape is undone — one fence line at the start, one at the end, none inside."""
    text = body.strip("\r\n")
    lines = text.split("\n")
    if len(lines) < 2 or not re.match(r"^```[\w+#.\-]*[ \t]*\r?$", lines[0]) or lines[-1].strip() != "```":
        return body, False
    if len(_FENCE_LINE.findall(text)) != 2:
        return body, False
    inner = "\n".join(lines[1:-1])
    return inner + ("\n" if body.endswith("\n") else ""), True


def _content_blocks(source: str, notes: Optional[List[str]] = None, finished: bool = False
                    ) -> Tuple[Dict[str, Tuple[str, bool]], List[Tuple[int, int]]]:
    """``({id: (text, closed)}, spans)`` of the raw content blocks of an answer; ``spans`` are cut out of the text.

    A block ends at its closing tag, or — when the writer forgot it — where the next block starts. The last block of
    an answer that ended by itself (``finished``) is complete even without the tag; with the answer cut by a length
    limit it is not. ``notes`` collects what had to be tolerated."""
    blocks: Dict[str, Tuple[str, bool]] = {}
    spans: List[Tuple[int, int]] = []
    position = 0
    count = 0
    while True:
        match = _CONTENT_OPEN.search(source, position)
        if not match:
            break
        count += 1
        found = _CONTENT_ID.search(match.group(1) or "")
        block_id = found.group(1) if found else str(count)
        if notes is not None and (not found or not re.fullmatch(r"""\s*id\s*=\s*"[\w.\-]+"\s*""", match.group(1) or "")):
            note = "content block opened in a different spelling" if found else "content block without an id"
            if note not in notes:
                notes.append(note)
        body_start = match.end()
        close = _CONTENT_CLOSE_TAG.search(source, body_start)
        following = _CONTENT_OPEN.search(source, body_start)
        if close is not None and (following is None or close.start() <= following.start()):
            end, stop, closed = close.start(), close.end(), True
        elif following is not None:
            end = stop = following.start()                 # no closing tag: the next block ends this one
            closed = True
            if notes is not None and "content block without a closing tag" not in notes:
                notes.append("content block without a closing tag")
        else:
            end = stop = len(source)
            # Forgotten closing tag after a complete last line, or an answer cut in the middle of a line?
            # Only the first is trusted: half a file must never be written.
            closed = finished and source[body_start:end].endswith(("\n", "\r"))
            if closed and notes is not None and "content block without a closing tag" not in notes:
                notes.append("content block without a closing tag")
        body = source[body_start:end]
        # Like a here-document: the line break after the opening tag is not part of the text, the one before the
        # closing tag is (a file written on its own lines ends with a line break, as files do).
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        body, stripped = _unfence(body)
        if stripped and notes is not None and "code fence inside a content block removed" not in notes:
            notes.append("code fence inside a content block removed")
        blocks[block_id] = (body, closed)
        spans.append((match.start(), stop))
        position = max(stop, match.end())
    return blocks, spans


def _alias_missing(source: str, blocks: Dict[str, Tuple[str, bool]], spans: List[Tuple[int, int]],
                   notes: List[str]) -> None:
    """A model that numbered its blocks one way and referred to them another (blocks ``a``, ``b`` and references
    ``@content:1``, ``@content:2``): when the references that find no block and the blocks nobody refers to are
    equally many, they belong together in order."""
    refs: List[str] = []
    for match in _CONTENT_REF_ANY.finditer(source):
        if any(start <= match.start() < stop for start, stop in spans):
            continue
        if match.group(1) not in refs:
            refs.append(match.group(1))
    missing = [ref for ref in refs if ref not in blocks]
    spare = [block for block in blocks if block not in refs]
    if missing and len(missing) <= len(spare):
        for ref, block in zip(missing, spare):
            blocks[ref] = blocks[block]
        notes.append("content blocks matched to references by their order")


def _fill_content(value: Any, blocks: Dict[str, Tuple[str, bool]], problems: List[str]) -> Any:
    """Replace ``"@content:1"`` strings by the raw block 1."""
    if isinstance(value, str):
        match = _CONTENT_REF.match(value)
        if not match:
            return value
        ref = match.group(1)
        found = blocks.get(ref)
        if found is None:
            if _MISSING + ref not in problems:
                problems.append(_MISSING + ref)
            return ""
        if not found[1]:
            problems.append(f"content block {ref!r} was cut off before </memuai-content>")
        return found[0]
    if isinstance(value, dict):
        return {key: _fill_content(item, blocks, problems) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill_content(item, blocks, problems) for item in value]
    return value


def _rows(data: Any) -> List[Any]:
    if isinstance(data, dict):
        for key in ("tool_calls", "calls", "tools"):
            if isinstance(data.get(key), list):
                return list(data[key])
        return [data]
    return list(data) if isinstance(data, list) else []


def _close_fence(source: str, end: int, incomplete: bool) -> int:
    """Where a block that ended at ``end`` stops: after its closing fence when there is one."""
    closer = re.match(r"[ \t]*\r?\n?[ \t]*```[ \t]*", source[end:])
    if closer:
        return end + closer.end()
    if not incomplete:
        tail = re.match(r"[\s\S]*?\n[ \t]*```[ \t]*(?=\n|$)", source[end:])
        if tail and tail.end() < 400:
            return end + tail.end()
    return end


def parse(text: str, known_keys: Optional[Iterable[str]] = None, *, finished: bool = False) -> ParseResult:
    """The tool calls of an answer and the text that remains once the blocks are cut out.

    Calls that read cleanly are returned even when another call of the same block is broken (that one is
    reported in ``errors``); a call the answer ended in the middle of goes to ``cut_off`` and is not returned;
    ``repairs`` lists what had to be fixed in the JSON, in plain words. ``finished``: the model ended its answer
    by itself (not by a length limit), so a last content block without its closing tag is taken as complete."""
    source = str(text or "")
    keys = DEFAULT_KEYS | frozenset(known_keys or ())
    repairs: List[str] = []
    blocks, content_spans = _content_blocks(source, repairs, finished)
    _alias_missing(source, blocks, content_spans, repairs)
    calls: List[ParsedCall] = []
    errors: List[str] = []
    cut_off: List[str] = []
    out: List[str] = []
    last = 0

    def take(rows: List[Any], block_repairs: List[str], incomplete: bool) -> None:
        stray: List[Any] = []
        made = 0
        for index, row in enumerate(rows):
            partial = incomplete and index == len(rows) - 1
            call = normalize_call(row)
            if call is None:
                if partial:
                    continue
                if isinstance(row, dict) and calls and not calls[-1].args and _looks_like_arguments(row, keys):
                    calls[-1].args = row                          # [{"tool": "Read"}, {"file_path": "a.py"}]
                    repairs.append("arguments sent as a separate entry were joined to their call")
                elif _looks_like_junk(row, keys):
                    stray.append(row)
                else:
                    errors.append("an entry of the block is not a tool call (no valid \"tool\" name): "
                                  + json.dumps(row, ensure_ascii=False, default=str)[:160])
                continue
            if partial:
                cut_off.append(call.name)
                continue
            problems: List[str] = []
            target = str(call.args.get("file_path") or call.args.get("path") or call.args.get("command") or "")[:70]
            call.args = _fill_content(call.args, blocks, problems)
            if problems:
                errors.append(f"{call.name}({target}): " if target else f"{call.name}: ")
                errors[-1] += _describe_problems(problems, blocks)
                if any("cut off" in item for item in problems):
                    cut_off.append(call.name)
                continue
            call.notes = list(block_repairs)
            if len(calls) < MAX_CALLS_PER_ROUND:
                calls.append(call)
                made += 1
        if stray:
            if made or calls:
                repairs.append("an entry of the block that was not a tool call was ignored")
            else:
                errors.append("the block holds no tool call: its entries have no \"tool\" name, e.g. "
                              + json.dumps(stray[0], ensure_ascii=False, default=str)[:120])

    for match in _OPENER.finditer(source):
        if match.start() < last or any(start <= match.start() < stop for start, stop in content_spans):
            continue                                       # inside an earlier block, or inside a file being written
        body_start = match.end()
        probe = body_start
        while probe < len(source) and source[probe].isspace():
            probe += 1
        if probe < len(source) and source[probe] in "[{":
            try:
                read = read_json(source, probe, keys)
            except LenientError as exc:
                # Nothing in the block could be read: skip to its closing fence and say where it broke.
                tail = re.search(r"\n[ \t]*```[ \t]*(?=\n|$)", source[probe:])
                end = probe + (tail.end() if tail else len(source) - probe)
                out.append(source[last:match.start()])
                last = end
                errors.append(describe_error(source, exc.position, exc.message))
                continue
            rows = _rows(read.value)
            block_repairs = list(read.repairs)
            incomplete = read.incomplete
            end = read.end
            extra = 0
            while not incomplete and extra < MAX_CALLS_PER_ROUND:
                # One call per line instead of one array ("JSON lines"): read on while another value follows.
                follow = end
                while follow < len(source) and source[follow] in " \t\r\n,":
                    follow += 1
                if follow >= len(source) or source[follow] not in "{[":
                    break
                try:
                    more = read_json(source, follow, keys)
                except LenientError:
                    break
                rows.extend(_rows(more.value))
                block_repairs.extend(item for item in more.repairs if item not in block_repairs)
                incomplete, end, extra = more.incomplete, more.end, extra + 1
                read.errors.extend(more.errors)
                if "several JSON values in one block" not in block_repairs:
                    block_repairs.append("several JSON values in one block")
            stop = _close_fence(source, end, incomplete)
            out.append(source[last:match.start()])
            last = stop
            for item in block_repairs:
                if item not in repairs:
                    repairs.append(item)
            for message, position in read.errors:
                errors.append(describe_error(source, position, message))
            take(rows, block_repairs, incomplete)
        else:
            tail = re.match(r"([\s\S]*?)(?:\n[ \t]*```[ \t]*(?=\n|$)|$)", source[body_start:])
            raw = tail.group(1) if tail else source[body_start:]
            out.append(source[last:match.start()])
            last = body_start + (tail.end() if tail else len(raw))
            errors.append(f"the block does not contain a JSON array of calls: {raw.strip()[:200]!r}")
    out.append(source[last:])
    cleaned = "".join(out)

    def tag_call(match: re.Match) -> str:
        try:
            read = read_json(match.group(1), 0, keys)
        except (LenientError, ValueError):
            return match.group(0)
        found = [call for call in (normalize_call(row) for row in _rows(read.value)) if call]
        if not found or read.incomplete:
            return match.group(0)
        for call in found[:MAX_CALLS_PER_ROUND - len(calls)]:
            call.notes = list(read.repairs)
            calls.append(call)
        return ""

    cleaned = _TOOL_CALL_TAG.sub(tag_call, cleaned)

    def invoke_call(match: re.Match) -> str:
        args = {name: _xml_value(value, name) for name, value in _PARAM.findall(match.group(2))}
        if len(calls) < MAX_CALLS_PER_ROUND:
            calls.append(ParsedCall(name=match.group(1).strip(), args=args, raw_name=match.group(1).strip()))
        return ""

    if "<invoke" in cleaned:
        cleaned = _INVOKE.sub(invoke_call, cleaned)
        cleaned = _FUNCTION_CALLS.sub("", cleaned)

    def qwen_call(match: re.Match) -> str:
        # Qwen3-Coder (and models trained on its template) write <function=Read><parameter=file_path>a.py
        # </parameter></function>, usually inside <tool_call>. It was shown to the user as text and never ran.
        name = match.group(1).strip()
        args = {key: _xml_value(value, key) for key, value in _QWEN_PARAM.findall(match.group(2))}
        if len(calls) < MAX_CALLS_PER_ROUND:
            calls.append(ParsedCall(name=name, args=args, raw_name=name))
        return ""

    if "<function=" in cleaned:
        cleaned = _QWEN_FUNCTION.sub(qwen_call, cleaned)
        cleaned = re.sub(r"<tool_call>\s*</tool_call>", "", cleaned)
    cleaned = _strip_content_blocks(cleaned)               # the files that travelled as raw blocks
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return ParseResult(cleaned, calls, errors, repairs, cut_off)


def _strip_content_blocks(text: str) -> str:
    """Raw content blocks do not belong to the visible text."""
    while True:
        match = _CONTENT_OPEN.search(text)
        if not match:
            return text
        close = _CONTENT_CLOSE_TAG.search(text, match.end())
        following = _CONTENT_OPEN.search(text, match.end())
        if close is not None and (following is None or close.start() <= following.start()):
            text = text[:match.start()] + text[close.end():]
        elif following is not None:
            text = text[:match.start()] + text[following.start():]
        else:
            text = text[:match.start()]


def serialize_calls(calls: List[Dict[str, Any]]) -> str:
    """The block an assistant turn is replayed with in later requests."""
    rows = [{"tool": call.get("name"), "args": call.get("args") or {}} for call in calls]
    return "```memuai-tool\n" + json.dumps(rows, ensure_ascii=False) + "\n```"


# ---------------------------------------------------------------- streaming

_PEEK_TOOL = re.compile(r'"(?:tool|name)"\s*:\s*"([A-Za-z0-9_.\-]{1,60})"')
_PEEK_TARGET = re.compile(r'"(?:file_path|path|command|pattern|url|query|description|task)"\s*:\s*"((?:[^"\\]|\\.){1,100})')


class StreamHider:
    """Streaming view of the visible text: tool blocks are cut out as they appear.

    ``feed`` returns the text safe to show now; a tail that might be the start of an opener (```` ``` ````,
    ``<tool_c``) is held back until it is decided. What is hidden is still counted: ``hidden_chars`` and
    ``peek`` ("Write(src/App.jsx)") let the interface say what the model is writing.

    Every character of a block is looked at once: the state of the JSON scan is kept between chunks, so a
    34 000-character file arriving in 7 000 pieces costs 34 000 steps, not 7 000 × 17 000.
    """

    FENCE = "```memuai-tool"
    OPENERS = ((FENCE, "```"), ("<tool_call>", "</tool_call>"),
               ("<function_calls>", "</function_calls>"), ("<invoke", "</invoke>"),
               ("<memuai-content", _CONTENT_CLOSE), ("<memuai_content", _CONTENT_CLOSE),
               ("<memuai content", _CONTENT_CLOSE))
    #: Closers that are written in more than one way.
    CLOSERS = {_CONTENT_CLOSE: _CONTENT_CLOSE_TAG}

    def __init__(self) -> None:
        self.buffer = ""          # text not yet decided (normal state) or the block being hidden
        self.mode = "text"        # text | fence | tag
        self.closer = ""
        self.saw_tool = False
        self.hidden_chars = 0     # characters of tool blocks swallowed so far, all blocks of the answer
        self.block_chars = 0      # … of the block being read now
        self.peek = ""            # what the latest tool call is about: "Write(src/App.jsx)"
        self._tail = ""           # the last part of the hidden text, for ``peek``
        self._eat_newline = False
        self._reset_fence()

    # ------------------------------------------------------------ bookkeeping
    def _reset_fence(self) -> None:
        self._phase = "head"      # head (the opener line) | body | json | raw | after
        self._pos = 0             # how far ``buffer`` has been scanned
        self._counted = 0         # how much of ``buffer`` has been counted as hidden
        self._depth = 0
        self._in_string = False
        self._escaped = False

    def _swallow(self, chunk: str) -> None:
        """Count hidden text and keep what ``peek`` needs."""
        if not chunk:
            return
        self.hidden_chars += len(chunk)
        self.block_chars += len(chunk)
        self._tail = (self._tail + chunk)[-700:]
        found = None
        for found in _PEEK_TOOL.finditer(self._tail):
            pass
        if found is not None:
            target = _PEEK_TARGET.search(self._tail, found.end())
            name = found.group(1)
            self.peek = f"{name}({target.group(1)})" if target else name

    def _finish(self, rest: str, *, eat_newline: bool = False) -> None:
        """The block is over; ``rest`` (already counted as hidden) is text again. ``eat_newline``: the line
        break that ends the closing fence line belongs to the block (it may not have arrived yet)."""
        self.hidden_chars -= len(rest)
        self.block_chars -= len(rest)
        self.buffer = rest
        self.mode = "text"
        self._eat_newline = eat_newline
        self._reset_fence()

    # ------------------------------------------------------------ text mode
    def _hold_len(self, low: str) -> int:
        """Longest suffix of ``low`` that could still become an opener."""
        hold = 0
        for opener, _closer in self.OPENERS:
            for size in range(min(len(opener) - 1, len(low)), 0, -1):
                if opener.startswith(low[-size:]):
                    hold = max(hold, size)
                    break
        return hold

    def feed(self, text: str) -> str:
        self.buffer += text
        shown: List[str] = []
        while self.buffer:
            if self.mode == "text":
                if self._eat_newline:
                    self._eat_newline = False
                    if self.buffer.startswith("\r\n"):
                        self.buffer = self.buffer[2:]
                    elif self.buffer.startswith("\n"):
                        self.buffer = self.buffer[1:]
                    if not self.buffer:
                        break
                low = self.buffer.lower()
                best = None
                for opener, closer in self.OPENERS:
                    index = low.find(opener)
                    if index >= 0 and (best is None or index < best[0]):
                        best = (index, opener, closer)
                if best is None:
                    hold = self._hold_len(low)
                    shown.append(self.buffer[:len(self.buffer) - hold])
                    self.buffer = self.buffer[len(self.buffer) - hold:]
                    break
                index, opener, closer = best
                shown.append(self.buffer[:index])
                self.buffer = self.buffer[index + len(opener):]
                self.saw_tool = True
                self.closer = closer
                self.block_chars = 0
                if opener == self.FENCE:
                    self.mode = "fence"
                    self._reset_fence()
                else:
                    self.mode = "tag"
                continue
            if self.mode == "tag":
                pattern = self.CLOSERS.get(self.closer)
                if pattern is not None:
                    found = pattern.search(self.buffer)
                    index, size = (found.start(), found.end() - found.start()) if found else (-1, 0)
                else:
                    index = self.buffer.lower().find(self.closer)
                    size = len(self.closer)
                if index < 0:
                    keep = (24 if pattern is not None else len(self.closer) - 1)
                    keep = min(keep, len(self.buffer))
                    self._swallow(self.buffer[:len(self.buffer) - keep])
                    self.buffer = self.buffer[len(self.buffer) - keep:]
                    break
                self._swallow(self.buffer[:index])
                self.buffer = self.buffer[index + size:]
                self.mode = "text"
                continue
            if not self._feed_fence():
                break
        return "".join(shown)

    # ------------------------------------------------------------ fence mode
    def _feed_fence(self) -> bool:
        """Advance through the block. False when more text is needed; True when the block is over (the
        buffer then holds what follows it, in text mode)."""
        buf = self.buffer
        if len(buf) > self._counted:
            self._swallow(buf[self._counted:])
            self._counted = len(buf)
        if self._phase == "head":
            newline = buf.find("\n")
            line = (buf if newline < 0 else buf[:newline]).lstrip()
            if line[:1] in ("[", "{"):
                self._pos = 0
                self._phase = "json"
            elif newline < 0:
                return False                                   # the opener line is still streaming
            else:
                self._pos = newline + 1
                self._phase = "body"
        if self._phase == "body":                              # after the opener line, before the body
            body = buf[self._pos:].lstrip()
            if not body:
                return False
            self._phase = "json" if body[0] in "[{" else "raw"
        if self._phase == "json":
            self._scan_json(buf)
        if self._phase == "raw":
            index = buf.find("```", self._pos)
            if index < 0:
                self._pos = max(self._pos, len(buf) - 2)
                return False
            self._finish(buf[index + 3:], eat_newline=True)
            return True
        if self._phase == "after":
            rest = buf[self._pos:]
            fence = re.match(r"[ \t]*\r?\n?[ \t]*```[ \t]*", rest)
            if fence:
                self._finish(rest[fence.end():], eat_newline=True)
                return True
            if not rest.strip() or "```".startswith(rest.strip()):
                return False                                   # the closing fence may still come
            self._finish(rest)                                 # a block without a closing fence
            return True
        return False

    def _scan_json(self, buf: str) -> None:
        """Move the JSON scan forward from where it stopped. Ends in phase ``after`` (the JSON closed) or
        ``raw`` (a fence line inside unfinished JSON: the block is broken), else stays in ``json``."""
        n = len(buf)
        i = self._pos
        depth, in_string, escaped = self._depth, self._in_string, self._escaped
        phase = "json"
        while i < n:
            ch = buf[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth <= 0:
                    i += 1
                    phase = "after"
                    break
            elif ch == "`" and depth > 0 and (i == 0 or buf[i - 1] == "\n"):
                if n - i < 3:
                    break                                      # the line may turn out to be a fence: wait for it
                if buf.startswith("```", i):
                    tail = buf[i + 3:i + 40]
                    line_end = tail.find("\n")
                    if not (tail if line_end < 0 else tail[:line_end]).strip():
                        phase = "raw"                          # a bracket is missing; the fence closes the block
                        break
            i += 1
        self._pos, self._depth, self._in_string, self._escaped, self._phase = i, depth, in_string, escaped, phase

    def flush(self) -> str:
        if self.mode != "text":
            self.buffer = ""
            return ""
        rest, self.buffer = self.buffer, ""
        return rest


def result_payload(name: str, call_id: str, ok: bool, output: Any, error: str = "") -> Dict[str, Any]:
    row: Dict[str, Any] = {"tool": name, "id": call_id, "ok": bool(ok)}
    if ok:
        row["result"] = output
    else:
        row["error"] = error or (output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))
    return row


def format_results(rows: List[Dict[str, Any]], *, budget: int = 90_000) -> str:
    """One user message with every result of a round, each within its share."""
    from .tokens import split_budget
    texts = [json.dumps(row, ensure_ascii=False) for row in rows]
    texts = split_budget(texts, budget)
    return "<tool_results>\n" + "\n".join(texts) + "\n</tool_results>"
