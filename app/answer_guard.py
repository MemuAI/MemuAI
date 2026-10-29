"""Round 33 (r33-chat, item #2): an answer never shows the model's internal markup.

Python twin of ``app/pgstatic/answer-guard.js`` (the main chat). Used by Quick Access (streamed deltas),
Telegram (drafts and final messages) and background agents (``clean_ai_text``). Both implementations are tested
against the same recorded samples: ``tests/data/round33_leak_samples.json``.

Models trained on native function calling write their OWN call syntax as plain text when the provider gets no
native tools (``<tool_call>{…}</tool_call>``, GLM ``<tool_call>name(args)``, Kimi ``<web_search>{…}</web_search>``,
Qwen3-Coder ``<function=…>``, Claude-style ``<invoke>``, DeepSeek DSML / ``<｜tool▁calls▁begin｜>``, Kimi K2
``<|tool_call_begin|>``, gpt-oss harmony, Mistral ``[TOOL_CALLS]``, Llama ``<|python_tag|>``), template control
tokens (``<|im_end|>``, ``<｜end▁of▁sentence｜>``), echoes of tool results and MemuAI's own report envelopes, and
an orphan ``</think>`` after inline reasoning.

* :func:`clean` — the visible text (prose only: code blocks and inline code are never touched). ``partial``
  (streaming) also holds back an unfinished opener at the very end and an opened block without its closer.
* :func:`extract_calls` — ``(text, calls)``: foreign calls converted to MemuAI calls ``{"tool", "args"}``.
* :class:`StreamGuard` — incremental filter for a stream of deltas that can never be taken back.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

TOOL_TAG = re.compile(
    r"^(?:web_[a-z_]+|docs_[a-z_]+|work_[a-z_]+|code_[a-z_]+|panel_[a-z_]+|agent_[a-z_]+|mcp_[\w-]+|github_[a-z_]+|"
    r"gmail_[a-z_]+|gdrive_[a-z_]+|drive_[a-z_]+|gcal_[a-z_]+|calendar_[a-z_]+|notion_[a-z_]+|product_lookup|"
    r"current_time|system_info|working_directory|disk_space|open_apps|process_list|environment|project_tree|"
    r"file_exists|search_files|network_status|app_version|memory_[a-z_]+|image_[a-z_]+|take_screenshot|current_page|read_page|"
    r"search|browse|fetch|python|code_interpreter)$", re.I)
NAME_RE = re.compile(r"^[A-Za-z][\w.-]{0,63}$")
ENVELOPES = ("local-tool_report|terminal-command_report|browser_report|computer-control_report|agent_report|"
             "supplemental_context|session_summary|context_safety|attached_files|image_description|video_analysis|"
             "generated_images|tool_response|tool_result|tool_results|tool_output|function_results|function_result|"
             "ds_safety")

class _Rule:
    def __init__(self, open_: str, close: Any = None, *, call: bool = False, line: bool = False, flags=re.I):
        self.open = re.compile(open_, flags)
        self.close = close if callable(close) or close is None else re.compile(close, flags)
        self.call, self.line, self.tag = call, line, ""
        self.first = open_[1] if open_.startswith("\\") else open_[0]

RULES: List[_Rule] = [
    _Rule(r"<tool_call\b[^>]*>", r"</tool_call>", call=True),
    _Rule(r"<tool_calls?>", r"</tool_calls?>", call=True),
    _Rule(r"<function_calls>", r"</function_calls>", call=True),
    _Rule(r"""<invoke\s+name\s*=\s*["'][^"']+["'][^>]*>""", r"</invoke>", call=True),
    _Rule(r"<function\s*=\s*[\w.-]+\s*>", r"</function>", call=True),
    _Rule(r"<[｜|]\s*DSML\s*[｜|]\s*(?:function_calls|calls)\s*>", r"</[｜|]\s*DSML\s*[｜|]\s*(?:function_calls|calls)\s*>", call=True),
    _Rule(r"""<[｜|]\s*DSML\s*[｜|]\s*invoke\s+name\s*=\s*["'][^"']+["'][^>]*>""",
          r"</[｜|]\s*DSML\s*[｜|]\s*invoke\s*>", call=True),
    _Rule(r"<[｜|]tool[▁_ ]calls[▁_ ]begin[｜|]>", r"<[｜|]tool[▁_ ]calls[▁_ ]end[｜|]>", call=True),
    _Rule(r"<\|tool_calls_section_begin\|>", r"<\|tool_calls_section_end\|>", call=True),
    _Rule(r"<\|tool_call_begin\|>", r"<\|tool_call_end\|>", call=True),
    _Rule(r"<\|channel\|>\s*commentary\s+to=[^<]*?(?:<\|constrain\|>\s*\w+\s*)?<\|message\|>", r"<\|call\|>", call=True),
    _Rule(r"<\|channel\|>\s*analysis\s*<\|message\|>", r"<\|end\|>"),
    _Rule(r"\[TOOL_CALLS\]", None, call=True, line=True, flags=0),
    _Rule(r"<\|python_tag\|>", r"<\|eom_id\|>|<\|eot_id\|>", call=True, line=True),
    _Rule(r"<(" + ENVELOPES + r")\b[^>]*>", lambda m: re.compile("</" + re.escape(m.group(1)) + ">", re.I)),
    _Rule(r"\[TOOL_RESULTS?\]", r"\[/TOOL_RESULTS?\]", flags=0),
    _Rule(r"◁think▷", r"◁/think▷", flags=0),
]
TOKENS = re.compile(
    r"<\|(?:im_start\|>\s*(?:assistant|user|system)?|start\|>\s*assistant|start_header_id\|>\s*\w+\s*<\|end_header_id\|>"
    r"|[a-z][a-z0-9_]{1,40}\|>)|<｜[^｜<>\n]{1,40}｜>|<\|channel\|>\s*final\s*<\|message\|>|</?s>(?=\s*$)"
    r"|\[(?:/?INST|/?TOOL_CALLS|AVAILABLE_TOOLS|/AVAILABLE_TOOLS)\]", re.I)
OPENER_WORDS = ["<tool_call>", "<tool_calls>", "<function_calls>", "<function=", "<invoke name=", "<|", "<｜",
                "[TOOL_CALLS]", "[TOOL_RESULT]", "<think>", "</think>", "<thinking>", "</thinking>", "◁think▷",
                "◁/think▷"] + ["<" + name + ">" for name in ENVELOPES.split("|")]
TOKEN_BLOCK = re.compile(r"<[｜|](?:\s*DSML\s*[｜|]\s*(?:(?:function_calls|calls)\s*>|invoke\s+name\s*=)|tool[▁_ ]calls[▁_ ]begin[｜|]>|"
                         r"tool_calls_section_begin\|>)", re.I)
_TAG = re.compile(r"<([A-Za-z][\w.-]{1,63})>")
_TOOL_TAG_PREFIX = re.compile(
    r"^(?:w(?:e(?:b(?:_\w*)?)?)?|d(?:o(?:c(?:s(?:_\w*)?)?)?)?|t(?:o(?:o(?:l\w*)?)?)?|f(?:u(?:n\w*)?)?|i(?:n(?:v\w*)?)?|"
    r"th\w*|c(?:o(?:d(?:e(?:_\w*)?)?)?)?|p(?:a(?:n(?:e(?:l(?:_\w*)?)?)?)?)?|m(?:c(?:p\w*)?)?)$", re.I)

class _Block:
    __slots__ = ("start", "end", "inner", "rule", "open", "closed", "tag")

    def __init__(self, start, end, inner, rule, open_, closed, tag=""):
        self.start, self.end, self.inner, self.rule, self.open, self.closed, self.tag = \
            start, end, inner, rule, open_, closed, tag

def segments(text: str) -> List[Tuple[bool, str]]:
    """``(is_code, text)`` parts: fences (an unclosed one runs to the end) and inline code are code."""
    source = str(text or "")
    out: List[Tuple[bool, str]] = []
    pattern = re.compile(r"(^|\n)([ \t]*)(`{3,}|~{3,})[^\n]*(?:\n|$)|`+")
    last, pos = 0, 0
    while True:
        match = pattern.search(source, pos)
        if not match:
            break
        if match.group(3):
            start = match.start() + len(match.group(1))
            fence = match.group(3)
            close_re = re.compile(r"\n[ \t]*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*(?=\n|$)")
            close = close_re.search(source, match.end() - (1 if match.group(0).endswith("\n") else 0))
            end = close.end() if close else len(source)
            if start > last:
                out.append((False, source[last:start]))
            out.append((True, source[start:end]))
            last = pos = end
        else:
            ticks = match.group(0)
            close = source.find(ticks, match.end())
            if close < 0 or re.search(r"\n\s*\n", source[match.start():close]):
                pos = match.end()
                continue
            if match.start() > last:
                out.append((False, source[last:match.start()]))
            out.append((True, source[match.start():close + len(ticks)]))
            last = pos = close + len(ticks)
    if last < len(source):
        out.append((False, source[last:]))
    return out

def _scan_json(text: str, start: int) -> Optional[Tuple[int, int]]:
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] not in "[{":
        return None
    begin, depth, in_string, escaped = i, 0, False, False
    for i in range(begin, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return begin, i + 1
    return None

_ANY_OPEN = re.compile("|".join("(?:" + rule.open.pattern + ")" for rule in RULES).replace("(" + ENVELOPES + ")",
                                                                                         "(?:" + ENVELOPES + ")"), re.I)

CALL_BODY = re.compile(r"\s*(?:[\[{]|<(?:invoke|function|arg_key|parameter|tool_call|[｜|])|[A-Za-z][\w.:-]*\s*"
                       r"(?:\(|<arg_key>|\n\s*<arg_key>|\n\s*[\[{]|<\|tool_call_argument_begin))", re.I)
CALL_PREFIX = re.compile(r"\s*<[a-z_]*=?|\s*(?:[A-Za-z][\w.:-]*\s*(?:\([\s\S]*|<[a-z_]*>?[\s\S]*|\{[\s\S]*)?)?")
PROSE_RULE = re.compile(r"(?:<tool_call|<tool_calls?>|<function_calls>|<invoke|<function\s*=|\[TOOL_|"
                        r"<(?:local-tool|terminal-command|browser|computer-control|agent)_report|"
                        r"<(?:supplemental_context|session_summary|context_safety|attached_files|image_description|"
                        r"video_analysis|generated_images|tool_response|tool_result|tool_results|tool_output|"
                        r"function_results|function_result|ds_safety)\b)", re.I)

def _next_block(text: str, start_at: int, partial: bool = False) -> Optional[_Block]:
    best: Optional[_Block] = None
    pos = start_at
    while best is None:
        first = _ANY_OPEN.search(text, pos)
        if not first:
            break
        i = first.start()
        for rule in RULES:
            m = rule.open.match(text, i)
            if not m:
                continue
            body_start = m.end()
            close_re = rule.close(m) if callable(rule.close) else rule.close
            end, inner, closed = len(text), text[body_start:], False
            if close_re is not None:
                c = close_re.search(text, body_start)
                if c:
                    inner, end, closed = text[body_start:c.start()], c.end(), True
            if not closed and rule.line:
                span = _scan_json(text, body_start)
                if span:
                    inner, end, closed = text[body_start:span[1]], span[1], True
            opener = m.group(0)
            prose_tag = PROSE_RULE.match(opener) is not None
            if prose_tag and rule.call and not opener.upper().startswith("[TOOL_CALLS]"):
                looks = CALL_BODY.match(inner) is not None or (
                    re.match(r"<function\s*=|<invoke", opener, re.I) is not None
                    and re.match(r"\s*(?:<parameter|[\[{]|$)", inner) is not None)
                if not looks and not (partial and not closed and CALL_PREFIX.fullmatch(inner)):
                    break
            if not closed and rule.call and opener.lower().startswith("<tool_call"):
                nxt = re.search(r"<tool_call\b|\n[ \t]*\n", text[body_start:], re.I)
                if nxt:
                    inner, end, closed = text[body_start:body_start + nxt.start()], body_start + nxt.start(), "soft"
            if not closed and not partial and prose_tag and not rule.call:
                break
            if not closed and not partial and opener.upper().startswith("[TOOL_CALLS]"):
                break
            best = _Block(i, end, inner, rule, opener, closed)
            break
        if best is None:
            pos = i + 1
    for t in _TAG.finditer(text, start_at, best.start if best else len(text)):
        if best is not None and t.start() >= best.start:
            break
        if not TOOL_TAG.match(t.group(1)):
            continue
        body_start = t.end()
        close_tag = "</" + t.group(1) + ">"
        close_at = text.find(close_tag, body_start)
        body = text[body_start:close_at if close_at >= 0 else len(text)]
        if not re.match(r"\s*[\[{]", body) and close_at < 0 and not (partial and re.fullmatch(r"\s*(?:<\w*)?", body)):
            continue
        if close_at >= 0 and not re.match(r"\s*[\[{]", body) and not re.match(r"\s*<(?:parameter|arg)", body, re.I):
            continue
        best = _Block(t.start(), close_at + len(close_tag) if close_at >= 0 else len(text), body,
                      _Rule(r"x", call=True), t.group(0), close_at >= 0, tag=t.group(1))
        break
    return best

def _hold_back(text: str) -> str:
    tail_start = max(text.rfind("<"), text.rfind("["), text.rfind("◁"))
    if tail_start < 0 or len(text) - tail_start > 40:
        return text
    tail = text[tail_start:]
    if re.search(r"\s", tail[1:]) and not tail.lower().startswith("<invoke ") and not tail.startswith("<|channel|>") \
            and not re.match(r"<[｜|]\s*DSML\b", tail, re.I):
        return text
    low = tail.lower()
    possible = any(word.lower().startswith(low) or (low.startswith(word.lower().rstrip(">")) and ">" not in low)
                   for word in OPENER_WORDS) \
        or re.fullmatch(r"<\|[\w|]*", tail) is not None or re.fullmatch(r"<｜[^｜]*", tail) is not None \
        or (re.fullmatch(r"<[A-Za-z][\w.-]*", tail) is not None
            and (_TOOL_TAG_PREFIX.match(tail[1:]) is not None or TOOL_TAG.match(tail[1:]) is not None))
    return text[:tail_start] if possible else text

def _clean_prose(text: str, partial: bool, is_last: bool) -> str:
    out, pos = [], 0
    while True:
        block = _next_block(text, pos, partial)
        if block is None:
            break
        out.append(text[pos:block.start])
        if not block.closed and not partial and block.tag:
            out.append(text[block.start:block.end])
        pos = block.end
    out.append(text[pos:])
    value = TOKENS.sub("", "".join(out))
    if partial and is_last:
        value = _hold_back(value)
    return value

def _has_tool_tag(text: str) -> bool:
    return any(TOOL_TAG.match(t.group(1)) for t in _TAG.finditer(text))

def split_think(text: str) -> Tuple[str, str]:
    """``(content, reasoning)`` for an orphan closing think tag (reasoning came before it)."""
    source = str(text or "")
    last = None
    for last in re.finditer(r"</(?:think|thinking|reasoning)>|◁/think▷", source, re.I):
        pass
    if last is None:
        return source, ""
    before = source[:last.start()]
    if re.search(r"<(?:think|thinking|reasoning)\b[^>]*>|◁think▷", before, re.I):
        return source, ""
    return source[last.end():].lstrip(), before.strip()

def _token_blocks(text: str, on_block: Optional[Callable[[_Block], None]] = None) -> str:
    if not TOKEN_BLOCK.search(text):
        return text
    out, pos = [], 0
    protected, offset, code_index = [], 0, 0
    for code, chunk in segments(text):
        if code:
            protected.append((offset, offset + len(chunk)))
        offset += len(chunk)
    while True:
        m = TOKEN_BLOCK.search(text, pos)
        if not m:
            break
        while code_index < len(protected) and protected[code_index][1] <= m.start():
            code_index += 1
        if code_index < len(protected) and protected[code_index][0] <= m.start() < protected[code_index][1]:
            end = protected[code_index][1]
            out.append(text[pos:end])
            pos = end
            continue
        block = _next_block(text, m.start())
        if block is None or block.start != m.start():
            out.append(text[pos:m.start() + 1])
            pos = m.start() + 1
            continue
        out.append(text[pos:m.start()])
        if on_block:
            on_block(block)
        pos = block.end
    out.append(text[pos:])
    return "".join(out)

def clean(text: Any, *, partial: bool = False) -> str:
    source = str(text or "")
    if not source:
        return source
    source = _token_blocks(split_think(source)[0])
    if not _ANY_OPEN.search(source) and not TOKENS.search(source) and not _has_tool_tag(source) \
            and (not partial or _hold_back(source) == source):
        return source
    parts = segments(source)
    value = "".join(chunk if code else _clean_prose(chunk, partial, index == len(parts) - 1)
                    for index, (code, chunk) in enumerate(parts))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", value))

def _norm_name(raw: Any) -> str:
    name = re.sub(r"^functions[.:]", "", str(raw or "").strip(), flags=re.I)
    name = re.sub(r":\d+$", "", name).strip("\"'")
    if not NAME_RE.match(name):
        return ""
    return "mcp_" + name[4:] if name.lower().startswith("mcp_") else name.lower()

def _json_loose(raw: Any) -> Any:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip())
    repaired = re.sub(r'("(?:[^"\\]|\\.)*")|,\s*(?=[\]}])', lambda m: m.group(1) or "", value)
    escaped = re.sub(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|\\',
                     lambda m: "\\\\" if m.group(0) == "\\" else m.group(0), repaired)
    for candidate in (value, repaired, escaped):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None

def _args(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        parsed = _json_loose(value)
        value = {"value": value} if parsed is None else parsed
    return value if isinstance(value, dict) else {}

def _from_object(row: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    fn = row.get("function") if isinstance(row.get("function"), dict) else {}
    name = _norm_name(row.get("name") or row.get("tool") or row.get("tool_name") or row.get("recipient_name")
                      or fn.get("name") or "")
    if not name:
        return None
    for key in ("arguments", "args", "parameters", "input"):
        if row.get(key) is not None:
            return {"tool": name, "args": _args(row[key])}
    return {"tool": name, "args": _args(fn.get("arguments") or {})}

def _json_objects(text: str) -> List[Any]:
    rows, pos = [], 0
    while True:
        span = _scan_json(text, pos)
        if not span:
            return rows
        data = _json_loose(text[span[0]:span[1]])
        if data is not None:
            rows.extend(data if isinstance(data, list) else [data])
        pos = span[1]

def _scalar(raw: Any) -> Any:
    value = str(raw if raw is not None else "").strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return float(value) if "." in value else int(value)
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value[:1] in "[{\"":
        parsed = _json_loose(value)
        if parsed is not None:
            return parsed
    return value.strip("\"'")

def _call_expr(body: str) -> Optional[Dict[str, Any]]:
    m = re.match(r"^\s*([A-Za-z][\w.-]*)\s*\(([\s\S]*)\)\s*$", str(body or ""))
    if not m:
        return None
    name = _norm_name(m.group(1))
    if not name:
        return None
    inside = m.group(2).strip()
    if inside[:1] in "[{":
        return {"tool": name, "args": _args(inside)}
    args: Dict[str, Any] = {}
    for a in re.finditer(r"""([A-Za-z_]\w*)\s*[:=]\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\[[^\]]*\]|\{[^}]*\}|[^,]+)\s*(?:,|$)""",
                         inside):
        raw = a.group(2).strip()
        if raw.startswith('"'):
            parsed = _json_loose(raw)
            args[a.group(1)] = parsed if parsed is not None else raw[1:-1]
        elif raw.startswith("'"):
            args[a.group(1)] = raw[1:-1]
        else:
            args[a.group(1)] = _scalar(raw)
    return {"tool": name, "args": args}

_PARAMS = re.compile(
    r"""<parameter\s*(?:=\s*|name\s*=\s*["'])([\w.-]+)["']?[^>]*>([\s\S]*?)</parameter>"""
    r"""|<arg_key>([\s\S]*?)</arg_key>\s*<arg_value>([\s\S]*?)</arg_value>"""
    r"""|<[｜|]\s*DSML\s*[｜|]\s*parameter\s+name\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)</[｜|]\s*DSML\s*[｜|]\s*parameter\s*>""",
    re.I)

def _xml_params(body: str) -> Optional[Dict[str, Any]]:
    args: Dict[str, Any] = {}
    found = False
    for m in _PARAMS.finditer(body or ""):
        found = True
        if m.group(1):
            key, value = m.group(1), m.group(2)
        elif m.group(3) is not None:
            key, value = m.group(3), m.group(4)
        else:
            key, value = m.group(5), m.group(6)
        if key and key.strip():
            string_value = re.search(r"""\bstring\s*=\s*["']true["']""", m.group(0).split(">", 1)[0], re.I)
            args[key.strip()] = value if string_value else _scalar(value)
    return args if found else None


def _invoke_args(body: str, *, dsml: bool = False) -> Dict[str, Any]:
    args = _xml_params(body)
    wrapped = args is not None
    if args is None:
        span = _scan_json(body, 0)
        args = _args(body[span[0]:span[1]]) if span else {}
    # Only DSML XML uses these names as transport holders. Ordinary XML
    # parameters and direct JSON bodies already describe the tool's argument map.
    if dsml and wrapped and len(args) == 1:
        key = next(iter(args))
        if key in ("args", "arguments", "parameters", "input") and isinstance(args[key], dict):
            return args[key]
    return args

def _calls_from_block(block: _Block) -> List[Dict[str, Any]]:
    inner, open_ = block.inner or "", block.open or ""
    out: List[Dict[str, Any]] = []
    dsml = bool(re.match(r"<[｜|]\s*DSML\s*[｜|]", open_, re.I))

    def add(name: str, args: Any) -> None:
        name = _norm_name(name)
        if name:
            out.append({"tool": name, "args": args if isinstance(args, dict) else {}})

    if block.tag:
        add(block.tag, _xml_params(inner) or _args(inner.strip()))
        return out
    m = re.match(r"<function\s*=\s*([\w.-]+)", open_, re.I)
    if m:
        add(m.group(1), _xml_params(inner) or _args(inner.strip()))
        return out
    m = re.match(r"""<(?:[｜|]\s*DSML\s*[｜|]\s*)?invoke\s+name\s*=\s*["']([^"']+)""", open_, re.I)
    if m:
        if block.closed:
            add(m.group(1), _invoke_args(inner, dsml=dsml))
        return out
    m = re.search(r"commentary\s+to=([\w.:-]+)", open_, re.I)
    if m:
        add(m.group(1), _args(inner.strip()))
        return out
    for m in re.finditer(r"""<(?:[｜|]\s*DSML\s*[｜|]\s*)?invoke\s+name\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)"""
                         r"""</(?:[｜|]\s*DSML\s*[｜|]\s*)?invoke\s*>""", inner, re.I):
        add(m.group(1), _invoke_args(m.group(2), dsml=dsml or bool(re.match(r"<[｜|]\s*DSML\s*[｜|]", m.group(0), re.I))))
    if out:
        return out
    for m in re.finditer(r"(?:<\|tool_call_begin\|>)?\s*([\w.:-]+)\s*<\|tool_call_argument_begin\|>\s*([\s\S]*?)\s*"
                         r"(?:<\|tool_call_end\|>|$)", inner, re.I):
        add(m.group(1), _args(m.group(2)))
    if out:
        return out
    for m in re.finditer(r"(?:function)?\s*<[｜|]tool[▁_ ]sep[｜|]>\s*([\w.-]+)\s*(?:```(?:json)?\s*)?([\s\S]*?)(?:```)?\s*"
                         r"(?:<[｜|]tool[▁_ ]call[▁_ ]end[｜|]>|$)", inner, re.I):
        add(m.group(1), _args(m.group(2)))
    if out:
        return out
    m = re.match(r"^\s*([A-Za-z][\w.-]*)\s*(?=<arg_key>)", inner)
    if m:
        add(m.group(1), _xml_params(inner) or {})
        return out
    expr = _call_expr(inner)
    if expr:
        return [expr]
    for row in _json_objects(inner):
        call = _from_object(row)
        if call:
            out.append(call)
    return out

def extract_calls(text: Any, allowed: Optional[set] = None) -> Tuple[str, List[Dict[str, Any]]]:
    """Foreign calls → MemuAI calls. ``allowed`` (names) drops calls of tools this surface does not have."""
    source = str(text or "")
    calls: List[Dict[str, Any]] = []
    seen = set()

    def add(call: Dict[str, Any]) -> None:
        key = call["tool"] + json.dumps(call["args"], sort_keys=True, ensure_ascii=False)
        if len(calls) < 12 and key not in seen and (allowed is None or call["tool"] in allowed):
            seen.add(key)
            calls.append(call)

    answer = _token_blocks(split_think(source)[0], lambda block: [add(c) for c in _calls_from_block(block)])
    parts = []
    for code, chunk in segments(answer):
        if code:
            parts.append(chunk)
            continue
        out, pos = [], 0
        while True:
            block = _next_block(chunk, pos)
            if block is None:
                break
            out.append(chunk[pos:block.start])
            pos = block.end
            if block.rule.call and (block.closed or not block.tag):
                if block.closed == "soft" and not re.match(
                        r"\s*(?:[A-Za-z][\w.-]*\s*\(|[\[{]|[A-Za-z][\w.-]*\s*<arg_key>)", block.inner):
                    continue
                for call in _calls_from_block(block):
                    add(call)
        out.append(chunk[pos:])
        parts.append("".join(out))
    return (clean("".join(parts)).strip() if calls else source), calls

class StreamGuard:
    """Incremental filter for deltas that cannot be taken back once sent (Quick Access popup).

    Each fed piece returns the newly visible text. Whatever may still turn out to be internal (a half-written
    opener, an opened call block) waits. If an earlier visible part later turns out to be hidden (an orphan
    ``</think>`` arrives), nothing more is shown in that round; :meth:`final` gives the clean whole answer.
    """

    def __init__(self) -> None:
        self.raw = ""
        self.sent = ""
        self.diverged = False

    def feed(self, piece: str) -> str:
        self.raw += str(piece or "")
        return self._emit(clean(self.raw, partial=True))

    def finish(self) -> str:
        return self._emit(clean(self.raw).rstrip())

    def _emit(self, visible: str) -> str:
        if self.diverged:
            return ""
        if not visible.startswith(self.sent):

            if self.sent.rstrip() and visible.startswith(self.sent.rstrip()):
                self.sent = self.sent.rstrip()
            else:
                self.diverged = True
                return ""
        out = visible[len(self.sent):]
        self.sent = visible
        return out

    def final(self) -> str:
        return clean(self.raw).strip()
