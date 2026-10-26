"""Round 40 (r40-files-max): «Разложить по смыслу» — the ``classify_and_organize`` plan action of the AI assistant.

A fast model reads the beginning of every document in a folder (text of txt/md/csv/code, the first page of a PDF,
the body of DOCX/ODT/PPTX/XLSX, EXIF/size of pictures, tags of audio) and proposes, for each file, a category folder
and a clean name («Счета/2026-09-Ромашка.pdf», «HR/Резюме — Петров А.С.txt», «Книги/Линейная алгебра.pdf»).

Nothing moves here: the result is a list of plan items ``{op: "organize", path, dest, new_name, category}`` that the
assistant shows as the usual checklist. :func:`app.memufiles.opqueue.OpQueue._do_organize` executes the ticked ones as
ONE operation (so a single Ctrl+Z puts every file back under its old name and removes the folders it created).

Safety: the model only returns a category and a name; both are sanitised here (no separators, no «..», no drive
letters, invalid characters replaced, the original extension always kept, length capped) and every target is
resolved inside the base folder, which is inside the assistant's scope.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import zipfile
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import fsops

MAX_FILES = 300
BATCH = 12
PARALLEL = 4
SNIPPET = 700
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".ini", ".cfg",
            ".yaml", ".yml", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".php", ".rb",
            ".sh", ".ps1", ".bat", ".sql", ".tex", ".srt", ".rtf", ".eml"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic"}
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".wma", ".aac"}
OFFICE_XML = {".docx": ("word/document.xml",), ".odt": ("content.xml",), ".ods": ("content.xml",),
              ".odp": ("content.xml",), ".xlsx": ("xl/sharedStrings.xml",), ".pptx": ("ppt/slides/slide1.xml", "ppt/slides/slide2.xml")}

PROMPT_RU = """Ты раскладываешь файлы пользователя по папкам по СМЫСЛУ содержимого.
Для каждого файла ниже (номер, имя, начало содержимого или метаданные) предложи:
- "folder": папку назначения (1–2 уровня через "/", по-русски, короткие понятные названия: «Бухгалтерия/Счета», «HR/Резюме», «Книги», «Договоры», «Фото/2026», «Скриншоты», «Музыка», «Архивы», «Разное»);
- "name": новое понятное имя БЕЗ расширения (для счетов/документов с датой и контрагентом — «ГГГГ-ММ-Контрагент», для резюме — «Резюме — Фамилия И.О.», для книг/учебников — название; если имя уже хорошее или о содержимом ничего не известно — оставь исходное имя);
- "why": 2–5 слов, что это за документ.
Одинаковые по смыслу файлы — в одну папку. Не выдумывай данные, которых нет в содержимом.
{instructions}Ответь ТОЛЬКО JSON-массивом без пояснений: [{{"i": 0, "folder": "...", "name": "...", "why": "..."}}, ...]"""

PROMPT_EN = """You sort the user's files into folders by the MEANING of their contents.
For every file below (number, name, start of contents or metadata) propose:
- "folder": the destination folder (1–2 levels joined by "/", short clear names: "Accounting/Invoices", "HR/Resumes", "Books", "Contracts", "Photos/2026", "Screenshots", "Music", "Archives", "Misc");
- "name": a clear new name WITHOUT the extension (invoices/dated documents — "YYYY-MM-Counterparty", resumes — "Resume — Surname N.", books — the title; keep the original name when it is already good or nothing is known about the contents);
- "why": 2–5 words saying what the document is.
Files with the same meaning go to the same folder. Never invent data that is not in the contents.
{instructions}Answer ONLY with a JSON array, no prose: [{{"i": 0, "folder": "...", "name": "...", "why": "..."}}, ...]"""

def _xml_text(data: bytes, limit: int) -> str:
    text = re.sub(rb"</(w:p|text:p|a:p|si)>", b"\n", data)
    text = re.sub(rb"<[^>]+>", b" ", text)
    out = text.decode("utf-8", "replace")
    out = re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", re.sub(r"&amp;", "&", out)))
    out = re.sub(r"[ \t\r\f\v]+", " ", out)
    return re.sub(r"\n\s*\n+", "\n", out).strip()[:limit]

def _pdf_text(path: str, limit: int) -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {}
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001
        PdfReader = None
    if PdfReader is not None:
        try:
            reader = PdfReader(path)
            meta["pages"] = len(reader.pages)
            info = reader.metadata or {}
            title = str(info.get("/Title") or "").strip()
            if title:
                meta["title"] = title[:120]
            text = ""
            for page in reader.pages[:2]:
                text += (page.extract_text() or "") + "\n"
                if len(text) >= limit:
                    break
            return re.sub(r"\s+\n", "\n", text).strip()[:limit], meta
        except Exception:  # noqa: BLE001 - broken / encrypted PDFs: try the crude reader
            pass
    try:
        with open(path, "rb") as handle:
            raw = handle.read(2 * 1024 * 1024)
    except OSError:
        return "", meta
    chunks: List[bytes] = []
    import zlib
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        body = match.group(1)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        chunks.extend(re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", body))
        for arr in re.findall(rb"\[(.*?)\]\s*TJ", body, re.S):
            chunks.append(b"".join(re.findall(rb"\((.*?)(?<!\\)\)", arr)))
        if sum(len(c) for c in chunks) > limit * 2:
            break
    text = b" ".join(chunks).decode("latin-1", "replace")
    return re.sub(r"\s+", " ", text).strip()[:limit], meta

def _image_meta(path: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    try:
        from PIL import Image
        with Image.open(path) as img:
            meta["size"] = f"{img.width}x{img.height}"
            exif = img.getexif() if hasattr(img, "getexif") else {}
            taken = exif.get(36867) or exif.get(306)
            if not taken and hasattr(exif, "get_ifd"):
                taken = exif.get_ifd(0x8769).get(36867)
            if taken:
                meta["taken"] = str(taken)[:19]
            camera = " ".join(str(exif.get(k) or "").strip() for k in (271, 272)).strip()
            if camera:
                meta["camera"] = camera[:60]
            software = str(exif.get(305) or "").strip()
            if software:
                meta["software"] = software[:40]
    except Exception:  # noqa: BLE001 - not readable by Pillow
        pass
    return meta

def _audio_meta(path: str) -> Dict[str, Any]:
    try:
        import mutagen
        tags = mutagen.File(path, easy=True)
    except Exception:  # noqa: BLE001
        return {}
    if not tags:
        return {}
    meta = {k: str((tags.get(k) or [""])[0])[:80] for k in ("artist", "title", "album") if tags.get(k)}
    if getattr(tags, "info", None) is not None and getattr(tags.info, "length", 0):
        meta["duration"] = int(tags.info.length)
    return meta

def describe(path: str, limit: int = SNIPPET) -> Dict[str, Any]:
    """What the model sees about one file: ``{text?, meta?}`` — the first page / lines / metadata."""
    ext = os.path.splitext(path)[1].lower()
    out: Dict[str, Any] = {}
    try:
        if ext in TEXT_EXT:
            from .preview import read_text
            info = read_text(path, limit * 4)
            if not info.get("binary"):
                out["text"] = re.sub(r"\s+\n", "\n", info.get("text", "")).strip()[:limit]
        elif ext == ".pdf":
            text, meta = _pdf_text(path, limit)
            if text:
                out["text"] = text
            if meta:
                out["meta"] = meta
        elif ext in OFFICE_XML:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                def head(name: str, size: int) -> bytes:
                    with zf.open(name) as stream:
                        return stream.read(size)
                parts = [head(n, 400000) for n in OFFICE_XML[ext] if n in names]
                core = head("docProps/core.xml", 20000) if "docProps/core.xml" in names else b""
            text = "\n".join(_xml_text(p, limit) for p in parts).strip()[:limit]
            if text:
                out["text"] = text
            title = re.search(rb"<dc:title>(.*?)</dc:title>", core)
            if title and title.group(1).strip():
                out["meta"] = {"title": title.group(1).decode("utf-8", "replace")[:120]}
        elif ext in IMAGE_EXT:
            out["meta"] = _image_meta(path)
        elif ext in AUDIO_EXT:
            out["meta"] = _audio_meta(path)
        elif ext in (".zip",):
            with zipfile.ZipFile(path) as zf:
                out["meta"] = {"archive": [n for n in zf.namelist()[:12]]}
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, RuntimeError):
        pass
    if not out.get("meta"):
        out.pop("meta", None)
    return out

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def clean_component(text: str, limit: int = 80) -> str:
    """One safe path component («Счета», «2026-09-Ромашка»): no separators / reserved chars / dots at the ends."""
    text = _BAD.sub(" ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = text[:limit].rstrip(" .")
    if text in ("", ".", ".."):
        return ""
    if fsops.valid_name(text):
        return ""
    return text

def clean_folder(text: str) -> str:
    """«Бухгалтерия/Счета» → a relative path of ≤3 safe components (never absolute, never «..»)."""
    parts = [clean_component(p, 60) for p in re.split(r"[\\/]+", str(text or ""))]
    parts = [p for p in parts if p]
    return os.path.join(*parts[:3]) if parts else ""

def new_file_name(original: str, proposed: str) -> str:
    """The model's name with the ORIGINAL extension (the model may drop or change it — never trusted)."""
    stem, ext = os.path.splitext(original)
    name = str(proposed or "")
    if ext and name.lower().endswith(ext.lower()):
        name = name[: -len(ext)]
    name = clean_component(name, 120) or stem
    return name + ext

def _parse_answer(text: str) -> List[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    raw = match.group(0)
    for candidate in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
        try:
            data = json.loads(candidate)
            return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        except ValueError:
            continue
    rows = []
    for obj in re.findall(r"\{[^{}]*\}", raw):
        try:
            rows.append(json.loads(re.sub(r",\s*}", "}", obj)))
        except ValueError:
            continue
    return rows

def classify(llm: Callable[[List[Dict[str, str]]], Iterator[str]], docs: List[Dict[str, Any]], lang: str = "ru",
             instructions: str = "", cancel: Optional[threading.Event] = None) -> Dict[int, Dict[str, str]]:
    """``{i: {folder, name, why}}`` for every document the model answered about (batches of :data:`BATCH`)."""
    template = PROMPT_EN if lang == "en" else PROMPT_RU
    extra = ""
    if instructions.strip():
        extra = ("Пожелания пользователя: " if lang != "en" else "User's wishes: ") + instructions.strip()[:500] + "\n"
    system = template.format(instructions=extra)
    out: Dict[int, Dict[str, str]] = {}

    def one(batch: List[Dict[str, Any]]) -> Dict[int, Dict[str, str]]:
        found: Dict[int, Dict[str, str]] = {}
        if cancel is not None and cancel.is_set():
            return found
        lines = []
        for doc in batch:
            row = {"i": doc["i"], "name": doc["name"], "modified": doc["modified"]}
            if doc.get("text"):
                row["text"] = doc["text"]
            if doc.get("meta"):
                row["meta"] = doc["meta"]
            lines.append(json.dumps(row, ensure_ascii=False))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]
        wanted = {d["i"] for d in batch}
        for attempt in range(2):
            try:
                answer = "".join(llm(messages))
            except Exception:  # noqa: BLE001 - one failed batch leaves its files unclassified, not the whole plan
                return found
            for row in _parse_answer(answer):
                try:
                    index = int(row.get("i"))
                except (TypeError, ValueError):
                    continue
                if index in wanted:
                    found[index] = {"folder": str(row.get("folder") or ""), "name": str(row.get("name") or ""),
                                    "why": str(row.get("why") or "")[:80]}
            if found or attempt:
                break
            messages.append({"role": "assistant", "content": answer[:2000]})
            messages.append({"role": "user", "content": "Ответ должен быть только JSON-массивом." if lang != "en"
                             else "Answer with the JSON array only."})
        return found

    batches = [docs[k:k + BATCH] for k in range(0, len(docs), BATCH)]
    if len(batches) <= 1:
        for batch in batches:
            out.update(one(batch))
        return out
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="memufiles-organize") as pool:
        for found in pool.map(one, batches):
            out.update(found)
    return out

def collect(root: str, recursive: bool, select: Optional[Dict[str, Any]], scope: str,
            cancel: Optional[threading.Event] = None) -> List[Dict[str, Any]]:
    """The files to classify: the folder's own files (or a ``find_files`` selection), hidden ones skipped."""
    if select:
        from .assistant import find_files
        found = find_files(scope, dict(select, root=select.get("root") or root), cancel, hard_limit=MAX_FILES)
        return [r for r in found["items"] if not r.get("is_dir")][:MAX_FILES]
    rows: List[Dict[str, Any]] = []
    stack = [root]
    while stack and len(rows) < MAX_FILES:
        folder = stack.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith((".", "~$")) or entry.name.lower() in ("desktop.ini", "thumbs.db"):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        stack.append(entry.path)
                    continue
                if entry.is_symlink():
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.name.lower().endswith((".part", ".crdownload", ".tmp")):
                continue
            rows.append({"path": entry.path, "size": st.st_size, "mtime": int(st.st_mtime), "is_dir": False})
            if len(rows) >= MAX_FILES:
                break
    return rows

def plan_items(scope: str, action: Dict[str, Any], llm: Optional[Callable], lang: str,
               resolve: Callable[[str, Any], str], check: Callable[[str, str], str],
               cancel: Optional[threading.Event] = None,
               notify: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Expand one ``classify_and_organize`` action into checklist items (and the rejected rows)."""
    root = resolve(scope, action.get("root") or action.get("path"))
    base = resolve(scope, action.get("dest") or action.get("base") or root)
    if not os.path.isdir(root):
        return [], [{"op": "organize", "path": root, "why": "not_found"}]
    if not fsops.is_inside(base, scope):
        return [], [{"op": "organize", "path": base, "why": "dest_outside_scope"}]
    if llm is None:
        return [], [{"op": "organize", "path": root, "why": "no_model"}]
    rename = action.get("rename", True) is not False
    files = collect(root, bool(action.get("recursive")), action.get("select") if isinstance(action.get("select"), dict) else None,
                    scope, cancel)
    rejected: List[Dict[str, Any]] = []
    docs = []
    for index, row in enumerate(files):
        why = check(scope, row["path"])
        if why:
            rejected.append({"op": "organize", "path": row["path"], "why": why})
            continue
        doc = {"i": index, "name": os.path.basename(row["path"]),
               "modified": time.strftime("%Y-%m-%d", time.localtime(row["mtime"]))}
        doc.update(describe(row["path"]))
        docs.append(doc)
    if notify is not None:
        notify({"t": "step", "tool": "classify_docs", "args": {"root": root, "files": len(docs)}, "note": ""})
    answers = classify(llm, docs, lang, str(action.get("instructions") or action.get("reason") or ""), cancel)
    items: List[Dict[str, Any]] = []
    taken: Dict[str, str] = {}
    for doc in docs:
        row = files[doc["i"]]
        answer = answers.get(doc["i"])
        if not answer:
            rejected.append({"op": "organize", "path": row["path"], "why": "not_classified"})
            continue
        folder = clean_folder(answer["folder"])
        name = new_file_name(doc["name"], answer["name"]) if rename else doc["name"]
        dest = os.path.normpath(os.path.join(base, folder)) if folder else base
        if not fsops.is_inside(dest, base) and fsops.norm(dest) != fsops.norm(base):
            rejected.append({"op": "organize", "path": row["path"], "why": "dest_outside_scope"})
            continue
        if fsops.norm(dest) == fsops.norm(os.path.dirname(row["path"])) and name == doc["name"]:
            rejected.append({"op": "organize", "path": row["path"], "why": "same_folder"})
            continue
        stem, ext = os.path.splitext(name)
        candidate, n = name, 2
        while True:
            key = fsops.norm(os.path.join(dest, candidate))
            exists = os.path.lexists(os.path.join(dest, candidate)) and fsops.norm(os.path.join(dest, candidate)) != fsops.norm(row["path"])
            if key not in taken and not exists:
                break
            candidate, n = f"{stem} ({n}){ext}", n + 1
        taken[fsops.norm(os.path.join(dest, candidate))] = row["path"]
        items.append({"op": "organize", "path": row["path"], "dest": dest, "new_name": candidate,
                      "category": os.path.relpath(dest, base) if fsops.norm(dest) != fsops.norm(base) else ".",
                      "reason": answer["why"], "size": row["size"], "is_dir": False, "mtime": row["mtime"], "why": ""})
    return items, rejected
