"""Batch rename of MemuFiles: a pattern with tokens + find/replace + case, with a preview.

Pattern tokens (the pattern builds the name WITHOUT the extension; the
extension is kept unless ``keep_ext`` is off):

``{name}`` original name without extension, ``{ext}`` extension without the dot,
``{n}`` counter, ``{n:3}`` zero-padded counter, ``{parent}`` folder name,
``{date}`` modified date ``YYYY-MM-DD``, ``{time}`` ``HH-MM-SS``,
``{size}`` size in bytes; Round 39: ``{created}`` creation date, ``{exif}`` photo date taken
``YYYY-MM-DD HH-MM-SS`` (EXIF DateTimeOriginal, falls back to the modified time),
``{width}``/``{height}`` image size in px (Pillow; empty for non-images).

Then ``find`` → ``replace`` (plain or ``regex``), then ``case``:
``lower`` / ``upper`` / ``title`` / ``sentence`` / ``""``.
"""
from __future__ import annotations

import datetime
import os
import re
from typing import Any, Dict, List

from .fsops import norm, split_name, valid_name

TOKEN = re.compile(r"\{(name|ext|n|parent|date|time|size|created|exif|width|height)(?::(\d{1,2}))?\}")


def _apply_case(text: str, case: str) -> str:
    if case == "lower":
        return text.lower()
    if case == "upper":
        return text.upper()
    if case == "title":
        return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in text.split(" "))
    if case == "sentence":
        return text[:1].upper() + text[1:].lower()
    return text


def _image_info(path: str) -> Dict[str, Any]:
    """``{w, h, taken}`` of an image (Round 39); empty dict when it is not one or Pillow is missing."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            info: Dict[str, Any] = {"w": im.width, "h": im.height}
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)      # DateTimeOriginal, DateTime
            if raw:
                info["taken"] = datetime.datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
            return info
    except Exception:  # noqa: BLE001 — not an image / broken EXIF
        return {}


def new_name(path: str, index: int, rule: Dict[str, Any]) -> str:
    name = os.path.basename(path)
    is_dir = os.path.isdir(path)
    base, ext = (name, "") if is_dir else split_name(name)
    pattern = str(rule.get("pattern") or "{name}")
    start = int(rule.get("start") or 1) if str(rule.get("start", "")).lstrip("-").isdigit() else 1
    step = int(rule.get("step") or 1) if str(rule.get("step", "")).lstrip("-").isdigit() else 1
    counter = start + index * step
    try:
        st = os.stat(path)
        mtime = datetime.datetime.fromtimestamp(st.st_mtime)
        ctime = datetime.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime))
        size = st.st_size
    except OSError:
        mtime, size = datetime.datetime.now(), 0
        ctime = mtime
    image: Dict[str, Any] = {}
    if re.search(r"\{(exif|width|height)", pattern):
        image = _image_info(path)

    def token(match: "re.Match[str]") -> str:
        key, width = match.group(1), match.group(2)
        if key == "name":
            return base
        if key == "ext":
            return ext[1:]
        if key == "n":
            return str(counter).zfill(int(width)) if width else str(counter)
        if key == "parent":
            return os.path.basename(os.path.dirname(path))
        if key == "date":
            return mtime.strftime("%Y-%m-%d")
        if key == "time":
            return mtime.strftime("%H-%M-%S")
        if key == "size":
            return str(size)
        if key == "created":
            return ctime.strftime("%Y-%m-%d")
        if key == "exif":
            return (image.get("taken") or mtime).strftime("%Y-%m-%d %H-%M-%S")
        if key in ("width", "height"):
            return str(image.get(key[0], ""))
        return match.group(0)
    result = TOKEN.sub(token, pattern)
    find = str(rule.get("find") or "")
    if find:
        replace = str(rule.get("replace") or "")
        if rule.get("regex"):
            try:
                result = re.sub(find, replace, result, flags=0 if rule.get("match_case") else re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"regex: {exc}") from exc
        elif rule.get("match_case"):
            result = result.replace(find, replace)
        else:
            result = re.sub(re.escape(find), lambda _m: replace, result, flags=re.IGNORECASE)
    result = _apply_case(result, str(rule.get("case") or ""))
    if rule.get("trim", True):
        result = result.strip()
    if rule.get("keep_ext", True) and ext:
        new_ext = ext.lower() if rule.get("ext_case") == "lower" else ext.upper() if rule.get("ext_case") == "upper" else ext
        result += new_ext
    return result


def preview(paths: List[str], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``[{path, old, new, error}]`` — error: invalid name, duplicate, or an existing other file."""
    rows: List[Dict[str, Any]] = []
    sources = {norm(p) for p in paths}
    seen: Dict[str, int] = {}
    for index, path in enumerate(paths):
        old = os.path.basename(path)
        try:
            new = new_name(path, index, rule)
            error = valid_name(new) or ""
        except ValueError as exc:
            new, error = old, str(exc)
        target = os.path.join(os.path.dirname(path), new)
        key = norm(target)
        if not error:
            if key in seen:
                error = "duplicate"
                rows[seen[key]]["error"] = rows[seen[key]]["error"] or "duplicate"
            elif os.path.lexists(target) and key not in sources:
                error = "exists"
        seen[key] = index
        rows.append({"path": path, "old": old, "new": new, "error": error, "changed": new != old})
    return rows
