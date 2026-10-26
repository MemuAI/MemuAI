"""New features of MemuFiles, Round 39 (r39-files-feat).

Everything here is reached through ``/api/x/<op>`` (``server._dispatch`` falls back to
:func:`handle`) or registered as a background job kind (``jobs.EXTRA_RUNNERS``):

* ``compare`` job — folder comparison (size + modified time, optional SHA-256) for
  «Сравнить папки» / sync; the sync itself goes through the normal copy queue.
* ``imgconvert`` job — batch image conversion (format / max side / quality) with Pillow.
* ``newdoc`` — «Создать» templates: empty but valid DOCX / XLSX / PPTX built with
  ``zipfile`` (no Office needed) plus text templates (py, ps1, bat, html, csv…).
* ``editors`` / ``editor`` — «Открыть в VS Code» (and Cursor / VSCodium / Sublime / Notepad++).
* ``recent`` — files opened from MemuFiles (``server.api_open`` records them).
* ``saved`` — saved searches.
* ``eject`` — safely remove a removable drive (Windows Shell verb).
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import threading
import time
import zipfile
from typing import Any, Callable, Dict, List, Optional

from .fsops import FsError, clean_path, unique_name, valid_name
from .paths import files_dir, read_json, write_json

IS_WIN = os.name == "nt"
_LOCK = threading.Lock()
RECENT_MAX = 60
SAVED_MAX = 40

def _scan(root: str, recursive: bool, job: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    stack = [""]
    while stack:
        if job.cancel.is_set():
            raise FsError("cancelled")
        rel = stack.pop()
        try:
            with os.scandir(os.path.join(root, rel) if rel else root) as it:
                for entry in it:
                    key = f"{rel}/{entry.name}" if rel else entry.name
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    out[key] = {"d": is_dir, "s": 0 if is_dir else st.st_size, "m": int(st.st_mtime * 1000)}
                    job.progress["files"] = job.progress.get("files", 0) + 1
                    if is_dir and recursive:
                        stack.append(key)
        except OSError:
            continue
    return out

def _sha(path: str, job: Any) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            if job.cancel.is_set():
                raise FsError("cancelled")
            digest.update(chunk)
    return digest.hexdigest()

def compare_job(job: Any) -> Dict[str, Any]:
    """``{left, right, recursive=True, content=False}`` → rows ``{rel, d, state, l, r}``.

    state: ``same`` | ``left`` (only on the left) | ``right`` | ``newer_left`` | ``newer_right`` | ``differ``
    (same time, other size/content). Times within 2 s count as equal (FAT/zip rounding).
    """
    args = job.args
    left, right = clean_path(args.get("left")), clean_path(args.get("right"))
    if not os.path.isdir(left) or not os.path.isdir(right):
        raise FsError("not_a_directory")
    recursive = args.get("recursive", True) is not False
    content = bool(args.get("content"))
    job.progress["current"] = left
    a = _scan(left, recursive, job)
    job.progress["current"] = right
    b = _scan(right, recursive, job)
    rows: List[Dict[str, Any]] = []
    counts = {k: 0 for k in ("same", "left", "right", "newer_left", "newer_right", "differ")}
    for rel in sorted(set(a) | set(b), key=lambda k: k.lower()):
        la, rb = a.get(rel), b.get(rel)
        if la and rb and la["d"] != rb["d"]:
            state = "differ"
        elif la and not rb:
            state = "left"
        elif rb and not la:
            state = "right"
        elif la["d"]:
            state = "same"
        else:
            dt = la["m"] - rb["m"]
            if la["s"] == rb["s"] and abs(dt) <= 2000:
                state = "same"
                if content:
                    job.progress["current"] = rel
                    if _sha(os.path.join(left, rel), job) != _sha(os.path.join(right, rel), job):
                        state = "differ"
            elif abs(dt) <= 2000:
                state = "differ"
            else:
                state = "newer_left" if dt > 0 else "newer_right"

        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent and state in ("left", "right") and rows and any(
                r["rel"] == parent and r["state"] == state for r in rows[-200:]):
            continue
        counts[state] += 1
        rows.append({"rel": rel, "d": bool((la or rb)["d"]), "state": state, "l": la, "r": rb})
    return {"left": left, "right": right, "rows": rows[:20000], "counts": counts, "truncated": len(rows) > 20000}

IMG_FORMATS = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF", "tiff": "TIFF"}

def imgconvert_job(job: Any) -> Dict[str, Any]:
    """``{paths, format: png|jpg|webp|bmp|gif|tiff|keep, max_side: 0|px, quality: 1..100, subfolder: str}``."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise FsError("no_pillow") from exc
    args = job.args
    paths = [clean_path(p) for p in (args.get("paths") or [])[:5000]]
    fmt = str(args.get("format") or "keep").lower()
    if fmt != "keep" and fmt not in IMG_FORMATS:
        raise FsError("bad_format", fmt)
    max_side = max(0, min(20000, int(args.get("max_side") or 0)))
    quality = max(1, min(100, int(args.get("quality") or 88)))
    sub = str(args.get("subfolder") or "").strip()
    if sub and valid_name(sub):
        raise FsError("bad_name", sub)
    done: List[str] = []
    failed: List[Dict[str, str]] = []
    for index, src in enumerate(paths):
        if job.cancel.is_set():
            raise FsError("cancelled")
        job.progress.update(files=index, current=src, total=len(paths))
        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                stem, ext = os.path.splitext(os.path.basename(src))
                out_ext = ext.lower().lstrip(".") if fmt == "keep" else fmt
                out_ext = "jpg" if out_ext == "jpeg" else out_ext
                pil_fmt = IMG_FORMATS.get(out_ext) or (im.format or "PNG")
                if max_side and max(im.size) > max_side:
                    im.thumbnail((max_side, max_side), Image.LANCZOS)
                if pil_fmt in ("JPEG", "BMP") and im.mode not in ("RGB", "L"):
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.split()[-1])
                    im = bg
                folder = os.path.dirname(src)
                if sub:
                    folder = os.path.join(folder, sub)
                    os.makedirs(folder, exist_ok=True)
                name = f"{stem}.{out_ext}"
                if not sub and name.lower() == os.path.basename(src).lower():
                    name = f"{stem} ({max_side or 'copy'}).{out_ext}"
                target = os.path.join(folder, unique_name(folder, name))
                opts: Dict[str, Any] = {}
                if pil_fmt in ("JPEG", "WEBP"):
                    opts["quality"] = quality
                if pil_fmt == "PNG":
                    opts["optimize"] = True
                im.save(target, pil_fmt, **opts)
                done.append(target)
        except (OSError, ValueError, SyntaxError) as exc:
            failed.append({"path": src, "error": str(exc)[:200]})
    job.progress.update(files=len(paths), current="")
    return {"done": done, "failed": failed}

_CT = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

def _ooxml(parts: Dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text)
    return buf.getvalue()

def _docx() -> bytes:
    return _ooxml({
        "[Content_Types].xml": _CT + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": _CT + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": _CT + '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p/><w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>',
    })

def _xlsx() -> bytes:
    return _ooxml({
        "[Content_Types].xml": _CT + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": _CT + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": _CT + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": _CT + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": _CT + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>',
    })

def _pptx() -> bytes:
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ct = "application/vnd.openxmlformats-officedocument.presentationml"
    sp_tree = (f'<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
               f'<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
               f'</p:spTree></p:cSld>')
    theme = (_CT + f'<a:theme xmlns:a="{a}" name="Office"><a:themeElements>'
             '<a:clrScheme name="Office"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
             '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
             + "".join(f'<a:accent{i}><a:srgbClr val="4472C4"/></a:accent{i}>' for i in range(1, 7)) +
             '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
             '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
             '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
             '<a:fmtScheme name="Office"><a:fillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3 +
             '</a:fillStyleLst><a:lnStyleLst>' + '<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3 +
             '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3 +
             '</a:effectStyleLst><a:bgFillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3 +
             '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')
    return _ooxml({
        "[Content_Types].xml": _CT + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/ppt/presentation.xml" ContentType="{ct}.presentation.main+xml"/>'
        f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="{ct}.slideMaster+xml"/>'
        f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="{ct}.slideLayout+xml"/>'
        f'<Override PartName="/ppt/slides/slide1.xml" ContentType="{ct}.slide+xml"/>'
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>',
        "_rels/.rels": _CT + f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
        "ppt/presentation.xml": _CT + f'<p:presentation xmlns:a="{a}" xmlns:r="{r}" xmlns:p="{p}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
        '<p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
        "ppt/_rels/presentation.xml.rels": _CT + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        f'<Relationship Id="rId2" Type="{rel}/slide" Target="slides/slide1.xml"/>'
        f'<Relationship Id="rId3" Type="{rel}/theme" Target="theme/theme1.xml"/></Relationships>',
        "ppt/slideMasters/slideMaster1.xml": _CT + f'<p:sldMaster xmlns:a="{a}" xmlns:r="{r}" xmlns:p="{p}">{sp_tree}'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
        'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>',
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _CT + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        f'<Relationship Id="rId2" Type="{rel}/theme" Target="../theme/theme1.xml"/></Relationships>',
        "ppt/slideLayouts/slideLayout1.xml": _CT + f'<p:sldLayout xmlns:a="{a}" xmlns:r="{r}" xmlns:p="{p}" type="blank">{sp_tree}</p:sldLayout>',
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _CT + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>',
        "ppt/slides/slide1.xml": _CT + f'<p:sld xmlns:a="{a}" xmlns:r="{r}" xmlns:p="{p}">{sp_tree}</p:sld>',
        "ppt/slides/_rels/slide1.xml.rels": _CT + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>',
        "ppt/theme/theme1.xml": theme,
    })

_TEXT_TEMPLATES: Dict[str, str] = {
    "txt": "", "md": "# {title}\n\n", "csv": "name,value\n", "json": "{}\n",
    "html": '<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n<title>{title}</title>\n</head>\n<body>\n\n</body>\n</html>\n',
    "py": '"""{title}."""\n\n\ndef main() -> None:\n    pass\n\n\nif __name__ == "__main__":\n    main()\n',
    "js": '"use strict";\n\n', "ps1": "# {title}\n\n", "bat": "@echo off\r\nrem {title}\r\n\r\n", "sh": "#!/usr/bin/env bash\nset -euo pipefail\n\n",
    "css": "", "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">\n</svg>\n',
    "ini": "[section]\n", "yaml": "", "xml": '<?xml version="1.0" encoding="UTF-8"?>\n<root/>\n',
}
_BINARY_TEMPLATES: Dict[str, Callable[[], bytes]] = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}
TEMPLATE_KINDS = sorted(set(_TEXT_TEMPLATES) | set(_BINARY_TEMPLATES))

def new_document(parent: str, name: str) -> str:
    """Create *name* in *parent* from the template of its extension (unique name, like Explorer «Создать»)."""
    parent = clean_path(parent)
    if not os.path.isdir(parent):
        raise FsError("not_a_directory")
    error = valid_name(name)
    if error:
        raise FsError(error, name)
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    target = os.path.join(parent, unique_name(parent, name))
    title = os.path.splitext(os.path.basename(target))[0]
    if ext in _BINARY_TEMPLATES:
        data = _BINARY_TEMPLATES[ext]()
    else:
        text = _TEXT_TEMPLATES.get(ext, "").replace("{title}", title)
        data = text.encode("utf-8")
    with open(target, "xb") as fh:
        fh.write(data)
    return target

def _editor_candidates() -> List[Dict[str, str]]:
    local = os.environ.get("LOCALAPPDATA", "")
    pf = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]
    table = [
        ("vscode", "VS Code", ["code", "code.cmd"], [os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe")]
         + [os.path.join(p, "Microsoft VS Code", "Code.exe") for p in pf if p]),
        ("cursor", "Cursor", ["cursor"], [os.path.join(local, "Programs", "cursor", "Cursor.exe")]),
        ("vscodium", "VSCodium", ["codium"], [os.path.join(local, "Programs", "VSCodium", "VSCodium.exe")]),
        ("sublime", "Sublime Text", ["subl"], [os.path.join(p, "Sublime Text", "sublime_text.exe") for p in pf if p]),
        ("notepadpp", "Notepad++", ["notepad++"], [os.path.join(p, "Notepad++", "notepad++.exe") for p in pf if p]),
    ]
    found: List[Dict[str, str]] = []
    for ident, label, names, paths in table:
        exe = next((p for p in paths if p and os.path.isfile(p)), "") or next(
            (w for w in (shutil.which(n) for n in names) if w), "")
        if exe:
            found.append({"id": ident, "name": label, "exe": exe})
    return found

_EDITORS: Dict[str, Any] = {"at": 0.0, "list": []}

def editors() -> List[Dict[str, str]]:
    if time.time() - _EDITORS["at"] > 60:
        _EDITORS.update(at=time.time(), list=_editor_candidates())
    return _EDITORS["list"]

def open_in_editor(path: str, ident: str = "") -> str:
    path = clean_path(path)
    found = editors()
    choice = next((e for e in found if e["id"] == ident), found[0] if found else None)
    if not choice:
        raise FsError("no_editor")
    exe = choice["exe"]
    command = [exe, path]
    flags = 0x08000000 if IS_WIN else 0
    if IS_WIN and exe.lower().endswith((".cmd", ".bat")):
        command = ["cmd.exe", "/c", exe, path]
    subprocess.Popen(command, cwd=path if os.path.isdir(path) else os.path.dirname(path),  # noqa: S603
                     creationflags=flags, close_fds=True)
    return choice["name"]

def _recent_file():
    return files_dir() / "recent.json"

def note_recent(path: str) -> None:
    try:
        with _LOCK:
            items = [p for p in read_json(_recent_file(), []) if isinstance(p, dict)]
            key = os.path.normcase(path)
            items = [r for r in items if os.path.normcase(r.get("path", "")) != key]
            items.insert(0, {"path": path, "at": int(time.time() * 1000)})
            write_json(_recent_file(), items[:RECENT_MAX])
    except Exception:  # noqa: BLE001 — history must never break opening a file
        pass

def recent() -> List[Dict[str, Any]]:
    out = []
    for row in read_json(_recent_file(), []):
        if not isinstance(row, dict) or not row.get("path"):
            continue
        try:
            st = os.stat(row["path"])
        except OSError:
            continue
        out.append({"path": row["path"], "at": row.get("at", 0), "s": st.st_size, "m": int(st.st_mtime * 1000),
                    "d": os.path.isdir(row["path"])})
    return out

def _saved_file():
    return files_dir() / "saved_searches.json"

def saved_searches(update: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        items = [s for s in read_json(_saved_file(), []) if isinstance(s, dict) and s.get("query")]
        if update is not None:
            if update.get("remove"):
                items = [s for s in items if s.get("id") != update["remove"]]
            elif update.get("query"):
                entry = {"id": update.get("id") or hashlib.sha1(f"{update['query']}{time.time()}".encode()).hexdigest()[:10],
                         "name": str(update.get("name") or update["query"])[:80], "query": str(update["query"])[:500],
                         "root": str(update.get("root") or ""), "scope": "everywhere" if update.get("scope") == "everywhere" else "folder"}
                items = [s for s in items if s.get("id") != entry["id"]][:SAVED_MAX - 1] + [entry]
            write_json(_saved_file(), items)
        return items

def eject(path: str) -> None:
    if not IS_WIN:
        raise FsError("unavailable")
    drive = os.path.splitdrive(clean_path(path))[0]
    if not drive:
        raise FsError("not_a_drive")
    script = ("$d=(New-Object -ComObject Shell.Application).Namespace(17).ParseName('%s\\');"
              "if(-not $d){exit 2};$d.InvokeVerb('Eject')" % drive)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],  # noqa: S603
                            capture_output=True, timeout=20, creationflags=0x08000000)
    if result.returncode:
        raise FsError("eject_failed", drive)

def handle(op: str, method: str, body: Dict[str, Any], query: Dict[str, str]) -> Dict[str, Any]:
    if op == "editors":
        return {"ok": True, "editors": [{"id": e["id"], "name": e["name"]} for e in editors()]}
    if op == "editor":
        return {"ok": True, "editor": open_in_editor(body.get("path"), str(body.get("id") or ""))}
    if op == "newdoc":
        return {"ok": True, "path": new_document(body.get("parent"), str(body.get("name") or "").strip()),
                "kinds": TEMPLATE_KINDS}
    if op == "recent":
        if method == "POST" and body.get("clear"):
            write_json(_recent_file(), [])
        return {"ok": True, "items": recent()}
    if op == "saved":
        return {"ok": True, "items": saved_searches(body if method == "POST" else None)}
    if op == "eject":
        eject(body.get("path"))
        return {"ok": True}
    raise FileNotFoundError(op)

def register(jobs_cls: Any) -> None:
    """Add the job kinds of this module to ``jobs.EXTRA_RUNNERS`` (called by jobs on import)."""
    jobs_cls.update(compare=compare_job, imgconvert=imgconvert_job)

