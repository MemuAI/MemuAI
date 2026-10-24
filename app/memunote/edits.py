"""MemuNote AI agent mode: the document edit protocol (Round 31).

The model never gets direct access to the file. It answers in prose and, when
the user wants the document changed, adds *edit blocks* — a tag format every
chat model can produce (no native tool calls needed, multi-line text needs no
escaping)::

    <edit op="replace">
    <find>exact text copied from the document</find>
    <text>the new text</text>
    </edit>

Operations (``op``):

``replace``           ``<find>`` → ``<text>`` (first occurrence nearest the cursor)
``replace_all``       every occurrence of ``<find>`` (``regex="true"`` allowed) → ``<text>``
``delete``            remove ``<find>``
``insert``            ``at="cursor|start|end|after|before"``; ``after``/``before`` need ``<anchor>``
``replace_selection`` the user's selection → ``<text>``
``rewrite_section``   the section under ``<heading>`` (up to the next heading of the same or higher level)
``append``            alias of ``insert at="end"``
``new_document``      ``title="…" kind="markdown|text"`` + ``<text>``: opens a new tab

A JSON fallback is accepted too (some models insist on JSON): a fenced block
``memunote-edit`` / ``json`` holding ``{"edits": [{"op": …, "find": …, "text": …}]}``.

:class:`EditStream` separates the streamed answer into prose deltas and edit
events while it streams (``edit_start`` → ``edit_progress`` → ``edit``), so the
page can show "writing a change…" live; positions are resolved by the page
against the full document (it alone has it — only an excerpt is sent here).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

OPS = ("replace", "replace_all", "delete", "insert", "replace_selection", "rewrite_section", "append",
       "new_document", "set_cells", "add_sheet", "add_column")
SHEET_OPS = ("set_cells", "add_sheet", "add_column")       # Round 40 (r40-note): spreadsheet documents
_RANGE_RE = re.compile(r"^\$?[A-Za-z]{1,3}\$?\d{1,7}(?::\$?[A-Za-z]{1,3}\$?\d{1,7})?$")
AT_VALUES = ("cursor", "start", "end", "after", "before")
_OPEN_RE = re.compile(r"<edit\b([^>]*)>", re.I)
_ATTR_RE = re.compile(r'([a-zA-Z_][\w-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))')
_CHILD_TAGS = ("find", "anchor", "text", "heading", "formula")
_MAX_FIELD = 2_000_000
# Markers the page puts into the excerpt; a model sometimes copies them into <find>.
_MARKERS_RE = re.compile(r"⟦(?:cursor|/?selection)⟧")

PROTOCOL = """\
DOCUMENT EDITING. You can change the open document. When (and only when) the user asks to write, insert, \
change, fix, translate, format, delete or reorganise text in the document, add edit blocks to your answer. \
Never paste the new text as a normal answer when an edit block can do it. Format of an edit block:

<edit op="replace">
<find>exact text copied character-for-character from the document</find>
<text>new text</text>
</edit>

Operations:
- op="replace": replace <find> with <text>. <find> must be copied exactly from the document (a whole \
sentence, line or paragraph; long enough to be unique; never paraphrase it).
- op="replace_all" (optionally regex="true"): replace every occurrence of <find> with <text>.
- op="delete": remove <find>.
- op="insert" at="cursor" | at="start" | at="end" | at="after" | at="before": insert <text>; for "after" and \
"before" give <anchor> — exact document text the new text goes after/before.
- op="replace_selection": replace the user's selected text with <text> (only when there is a selection).
- op="rewrite_section": <heading> is the exact heading line of a section; the whole section body (up to the \
next heading of the same or higher level) is replaced with <text> (the heading itself stays).
- op="new_document" title="…" kind="markdown" (or "text"): create a new document with <text>.
Rules: use as many edit blocks as needed, smallest edits that do the job (several small replaces beat one huge \
one); write edit blocks directly in the answer — never inside code fences, never as JSON; text inside <text> is \
inserted verbatim (keep the document's language, formatting and line breaks; Markdown documents use Markdown). \
Before the edit blocks write ONE short sentence saying what you are changing; after them nothing or one short \
line. The user previews every change and accepts or rejects it. If the user only asks a question, answer \
normally without edit blocks. The markers ⟦cursor⟧ and ⟦selection⟧…⟦/selection⟧ show where the user's \
cursor/selection is; they are not part of the document — never copy them into <find> or <anchor>."""


# Round 40 (r40-note): several documents in one request and spreadsheet edits.
MULTI_PROTOCOL = """\
SEVERAL DOCUMENTS. The user gave you several documents, each as <document id="dN" …>. id="d0" is the active one \
(the tab the user is looking at). An edit block changes d0 unless it has a doc attribute: add doc="d2" (the id) to \
change another document, e.g. <edit op="replace" doc="d2">…</edit>. Combine, compare and move text between the \
documents as the user asks; quote <find> exactly from the document the edit is for. To put a result (a merged \
table, a comparison, a summary of several files) into a NEW document use op="new_document" title="…" with \
kind="markdown" (text and Markdown tables) or kind="csv" (a plain data table: comma-separated, first row = \
header). When asked to compare documents, list the concrete differences (what, where, in which document)."""

SHEET_PROTOCOL = """\
SPREADSHEETS. A document with kind="sheet" is a workbook: each sheet is "## Sheet: name" followed by a grid — the \
first line holds the column letters, every next line starts with its row number, cells are separated by TABs; a \
formula cell is shown as "=FORMULA → value". Change a spreadsheet ONLY with these operations (never replace/insert):
- <edit op="set_cells" sheet="Sheet name" range="B2:D3"><text>rows of cells, TAB-separated, one line per row</text></edit> \
writes values starting at the range's top-left cell ("B2" alone is fine for one cell or a block). A cell starting \
with "=" is a formula (Excel syntax, English function names, comma between arguments, A1 references, e.g. \
=SUM(B2:B9), =IF(C2>100,"high","low"), =VLOOKUP(A2,Prices!A:B,2,FALSE)). To fill a whole range with ONE relative \
formula write it for the first cell in <formula>=B2*C2</formula> and no <text>: it is adjusted for every row/column \
like Excel's fill. An empty <text> clears the range.
- <edit op="add_column" sheet="Sheet name" header="Total"><text>one value or formula per line, for rows 2, 3, …</text></edit> \
adds a column after the last used one (formulas written for their own row, e.g. =B2*C2 then =B3*C3).
- <edit op="add_sheet" name="New sheet"><text>optional TAB-separated rows</text></edit> adds a sheet.
Use the exact sheet names; prefer formulas over computed numbers when the user asks to calculate something, so the \
workbook stays live. Keep the existing layout (headers, columns) unless asked to change it."""


def _attrs(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _ATTR_RE.finditer(raw or ""):
        out[m.group(1).lower()] = next(g for g in (m.group(2), m.group(3), m.group(4), "") if g is not None)
    return out


def _child(body: str, tag: str) -> Optional[str]:
    """Content of ``<tag>…</tag>``; for ``text`` up to the LAST closing tag (the text may mention the tag)."""
    m = re.search(rf"<{tag}\b[^>]*>", body, re.I)
    if not m:
        return None
    start = m.end()
    closes = [c.start() for c in re.finditer(rf"</{tag}\s*>", body[start:], re.I)]
    if not closes:
        value = body[start:]
        nxt = re.search(r"<(?:find|anchor|text|heading)\b[^>]*>", value, re.I) if tag != "text" else None
        if nxt:
            value = value[:nxt.start()]
    else:
        value = body[start:start + (closes[-1] if tag == "text" else closes[0])]
    return _trim_block(value)


def _trim_block(value: str) -> str:
    """One leading/trailing newline is layout of the tag, not content."""
    if value.startswith("\r\n"):
        value = value[2:]
    elif value.startswith("\n"):
        value = value[1:]
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    return value[:_MAX_FIELD]


def _clean_ref(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _MARKERS_RE.sub("", value)


def normalize(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate one edit (from tags or JSON) → ``{op, find?, anchor?, text, at?, heading?, regex?, title?, kind?}``."""
    if not isinstance(raw, dict):
        return None
    op = str(raw.get("op") or raw.get("type") or raw.get("action") or "").strip().lower().replace("-", "_")
    op = {"insert_at_cursor": "insert", "write": "insert", "add": "insert", "remove": "delete",
          "replace_section": "rewrite_section", "rewrite": "rewrite_section", "new": "new_document",
          "create_document": "new_document", "new_doc": "new_document", "find_replace": "replace_all",
          "replaceall": "replace_all", "insert_text": "insert", "replace_text": "replace"}.get(op, op)
    if op not in OPS:
        return None
    text = raw.get("text", raw.get("replace", raw.get("content", raw.get("with", ""))))
    edit: Dict[str, Any] = {"op": op, "text": str(text if text is not None else "")[:_MAX_FIELD]}
    find = _clean_ref(None if raw.get("find", raw.get("search")) is None else str(raw.get("find", raw.get("search"))))
    anchor = _clean_ref(None if raw.get("anchor") is None else str(raw.get("anchor")))
    if op in SHEET_OPS:
        return _normalize_sheet(op, raw, edit)
    if op == "append":
        edit.update(op="insert", at="end")
    elif op == "insert":
        at = str(raw.get("at") or raw.get("position") or ("after" if anchor else "cursor")).strip().lower()
        edit["at"] = at if at in AT_VALUES else "cursor"
        if edit["at"] in ("after", "before"):
            if not anchor:
                return None
            edit["anchor"] = anchor
    elif op in ("replace", "replace_all", "delete"):
        if not find:
            return None
        edit["find"] = find
        if op == "delete":
            edit["text"] = ""
        if op == "replace_all":
            edit["regex"] = str(raw.get("regex", "")).lower() in ("1", "true", "yes")
    elif op == "rewrite_section":
        heading = _clean_ref(str(raw.get("heading") or raw.get("section") or "")).strip()
        if not heading:
            return None
        edit["heading"] = heading
    elif op == "new_document":
        edit["title"] = str(raw.get("title") or raw.get("name") or "")[:120]
        kind = str(raw.get("kind") or "markdown").lower()
        edit["kind"] = kind if kind in ("markdown", "text", "csv") else "markdown"
    if op in ("insert", "replace_selection", "new_document", "append") and not edit["text"]:
        return None
    if raw.get("incomplete"):
        edit["incomplete"] = True
    _target(raw, edit)
    return edit


def _target(raw: Dict[str, Any], edit: Dict[str, Any]) -> None:
    """Round 40: ``doc="d2"`` — the document (of several given to the model) the edit is for."""
    doc = str(raw.get("doc") or raw.get("document") or raw.get("tab") or "").strip()[:40]
    if doc:
        edit["doc"] = doc


def _normalize_sheet(op: str, raw: Dict[str, Any], edit: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``set_cells`` sheet= range="B2" | "B2:D9" + TSV <text> (a cell starting with "=" is a formula; <formula>
    alone fills the whole range with one relative formula); ``add_sheet`` name= [+ TSV <text>];
    ``add_column`` sheet= [header=] + one value / formula per line in <text> (goes into the first empty column)."""
    sheet = str(raw.get("sheet") or "").strip()[:64]
    if sheet:
        edit["sheet"] = sheet
    if op == "set_cells":
        ref = str(raw.get("range") or raw.get("cell") or raw.get("at") or raw.get("ref") or "").replace("$", "").strip()
        if "!" in ref and not sheet:
            edit["sheet"], ref = ref.rsplit("!", 1)[0].strip("'"), ref.rsplit("!", 1)[1]
        if not _RANGE_RE.match(ref):
            return None
        edit["range"] = ref.upper()
        formula = raw.get("formula")
        if formula and not edit["text"]:
            edit["formula"] = "=" + str(formula).strip().lstrip("=")
        elif not edit["text"] and not raw.get("clear"):
            edit["clear"] = True
    elif op == "add_sheet":
        name = str(raw.get("name") or raw.get("title") or sheet or "").strip()[:31]
        if not name:
            return None
        edit["name"] = name
        edit.pop("sheet", None)
    elif op == "add_column":
        if not edit["text"] and not raw.get("header"):
            return None
        edit["header"] = str(raw.get("header") or "")[:200]
    if raw.get("incomplete"):
        edit["incomplete"] = True
    _target(raw, edit)
    return edit


def parse_block(open_tag_attrs: str, body: str, complete: bool = True) -> Optional[Dict[str, Any]]:
    attrs = _attrs(open_tag_attrs)
    raw: Dict[str, Any] = dict(attrs)
    for tag in _CHILD_TAGS:
        value = _child(body, tag)
        if value is not None:
            raw[tag] = value
    if "text" not in raw and not any(f"<{t}" in body.lower() for t in _CHILD_TAGS):
        raw["text"] = _trim_block(body)          # <edit op="insert">plain text</edit>
    if not complete:
        raw["incomplete"] = True
    return normalize(raw)


def parse_json_edits(text: str) -> List[Dict[str, Any]]:
    """``{"edits": [...]}`` / ``[...]`` / one edit object."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("edits", [data] if data.get("op") else [])
    if not isinstance(data, list):
        return []
    return [e for e in (normalize(x) for x in data) if e]


_JSON_FENCE_RE = re.compile(r"```(?:memunote-edit|memunote_edit|mn-edit|json)\s*\n(.*?)\n?```", re.S | re.I)


def parse_all(answer: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Whole-answer parse (tests, non-streaming callers): ``(prose, edits)``."""
    stream = EditStream()
    prose: List[str] = []
    edits: List[Dict[str, Any]] = []
    for kind, value in stream.feed(answer) + stream.finish():
        if kind == "delta":
            prose.append(value)
        elif kind == "edit":
            edits.append(value)
    return "".join(prose), edits


class EditStream:
    """Incremental splitter: ``feed(text)`` → ``[(kind, value)]`` with kind ``delta`` (prose),
    ``edit_start`` ``{op, index}``, ``edit_progress`` ``{index, chars, preview}`` or ``edit`` (the parsed edit).

    Prose that may be the beginning of ``<edit`` (or of a JSON edit fence) is held back until it is decided.
    """

    def __init__(self) -> None:
        self.buf = ""
        self.inside: Optional[Dict[str, Any]] = None   # {"attrs", "body", "index"}
        self.count = 0
        self.json_fence: Optional[str] = None          # body of a ```json fence being collected
        self.json_open = ""
        self.last_progress = 0
        self.eat_nl = False
        self.fenced = False                            # edit blocks wrapped in ```xml … ``` by the model

    # -- helpers
    @staticmethod
    def _hold_len(data: str) -> int:
        """Length of a tail that may grow into ``<edit`` or a JSON edit fence."""
        tail = data[-40:]
        for i in range(len(tail)):
            piece = tail[i:]
            low = piece.lower()
            if "<edit".startswith(low[:5]) and (len(low) < 5 or (low.startswith("<edit") and ">" not in low)):
                return len(piece)
            if "```".startswith(piece[:3]) and (
                    len(piece) < 3 or re.fullmatch(r"```[\w-]*[ \t]*(?:\r?\n[ \t]*(?:<|<e|<ed|<edi|<edit[^>]*)?)?", piece, re.I)):
                return len(piece)
        return 0

    def feed(self, text: str) -> List[Tuple[str, Any]]:
        self.buf += text
        out: List[Tuple[str, Any]] = []
        while self.buf:
            if self.json_fence is not None:
                end = self.buf.find("```")
                if end < 0:
                    self.json_fence += self.buf
                    self.buf = ""
                    break
                body = self.json_fence + self.buf[:end]
                self.buf = self.buf[end + 3:]
                self.json_fence = None
                edits = parse_json_edits(body.strip())
                if edits:
                    for e in edits:
                        out.append(("edit_start", {"op": e["op"], "index": self.count}))
                        out.append(("edit", dict(e, index=self.count)))
                        self.count += 1
                else:                                   # an ordinary ```json code block: give it back
                    out.append(("delta", self.json_open + body + "```"))
                continue
            if self.inside is None:
                if self.eat_nl:
                    self.buf = self.buf.lstrip("\r\n")
                    if not self.buf:
                        break
                    self.eat_nl = False
                if self.fenced:
                    stripped = self.buf.lstrip()
                    if not stripped or "```".startswith(stripped[:3]) and len(stripped) < 3:
                        break                           # wait: whitespace / a partial closing fence
                    if stripped.startswith("```"):
                        self.buf = stripped[3:]
                        self.fenced = False
                        self.eat_nl = True
                        continue
                    if not stripped.lower().startswith("<edit") and not "<edit".startswith(stripped.lower()[:5]):
                        self.fenced = False
                    else:
                        self.buf = stripped                 # whitespace between fenced edit blocks
                low = self.buf.lower()
                idx = low.find("<edit")
                fence = re.search(r"```(?:memunote-edit|memunote_edit|mn-edit|json)[ \t]*\n", self.buf, re.I)
                if fence and (idx < 0 or fence.start() < idx):
                    # JSON fence: only an edit list if it parses as one (decided at the closing fence).
                    if fence.start():
                        out.append(("delta", self.buf[:fence.start()]))
                    self.json_open = fence.group(0)
                    self.json_fence = ""
                    self.buf = self.buf[fence.end():]
                    continue
                if idx < 0:
                    hold = self._hold_len(self.buf)
                    emit = self.buf[:len(self.buf) - hold] if hold else self.buf
                    if emit:
                        out.append(("delta", emit))
                    self.buf = self.buf[len(emit):]
                    break
                m = _OPEN_RE.match(self.buf, idx)
                if not m:
                    if ">" in self.buf[idx:] or len(self.buf) - idx > 400:
                        # "<editor>" or prose mentioning "<edit": not ours
                        out.append(("delta", self.buf[:idx + 1]))
                        self.buf = self.buf[idx + 1:]
                        continue
                    cut = idx
                    fm = re.search(r"```[\w-]*[ \t]*\r?\n[ \t]*$", self.buf[:idx])
                    if fm:
                        cut = fm.start()                # keep a fence opener until the tag is decided
                    if cut:
                        out.append(("delta", self.buf[:cut]))
                        self.buf = self.buf[cut:]
                    break
                if idx:
                    before = self.buf[:idx]
                    fm = re.search(r"```[\w-]*[ \t]*\r?\n[ \t]*$", before)
                    if fm:
                        before = before[:fm.start()]
                        self.fenced = True
                    if before:
                        out.append(("delta", before))
                attrs = m.group(1)
                self.inside = {"attrs": attrs, "body": "", "index": self.count}
                self.count += 1
                self.last_progress = 0
                op = _attrs(attrs).get("op", "")
                out.append(("edit_start", {"op": op, "index": self.inside["index"]}))
                self.buf = self.buf[m.end():]
                continue
            # inside an edit block
            low = self.buf.lower()
            end = low.find("</edit>")
            if end < 0:
                keep = 0
                for size in range(min(6, len(self.buf)), 0, -1):
                    if "</edit>".startswith(low[-size:]):
                        keep = size
                        break
                self.inside["body"] += self.buf[:len(self.buf) - keep] if keep else self.buf
                self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                body = self.inside["body"]
                if len(body) - self.last_progress >= 24:
                    self.last_progress = len(body)
                    preview = _child(body, "text")
                    out.append(("edit_progress", {"index": self.inside["index"], "chars": len(body),
                                                  "preview": (preview or "")[-400:]}))
                break
            body = self.inside["body"] + self.buf[:end]
            self.buf = self.buf[end + len("</edit>"):]
            if self.buf.startswith("\r\n"):
                self.buf = self.buf[2:]
            elif self.buf.startswith("\n"):
                self.buf = self.buf[1:]
            edit = parse_block(self.inside["attrs"], body)
            index = self.inside["index"]
            self.inside = None
            out.append(("edit", dict(edit, index=index)) if edit else ("edit_invalid", {"index": index}))
        return out

    def finish(self) -> List[Tuple[str, Any]]:
        """End of stream: an unterminated edit block is offered as ``incomplete`` (the reply was cut)."""
        out: List[Tuple[str, Any]] = []
        if self.json_fence is not None:
            edits = parse_json_edits(self.json_fence.strip())
            if edits:
                for e in edits:
                    out.append(("edit", dict(e, index=self.count)))
                    self.count += 1
            else:
                out.append(("delta", self.json_open + self.json_fence))
            self.json_fence = None
        if self.inside is not None:
            body = self.inside["body"] + self.buf
            self.buf = ""
            edit = parse_block(self.inside["attrs"], body, complete=False)
            out.append(("edit", dict(edit, index=self.inside["index"])) if edit
                       else ("edit_invalid", {"index": self.inside["index"]}))
            self.inside = None
        if self.buf:
            out.append(("delta", self.buf))
            self.buf = ""
        return out
