# -*- coding: utf-8 -*-
"""MemuUtils in the chat: the ``utils`` module commands (``module_command {module: "utils", command, args}``).

``run`` — run one utility on text and/or the files attached in the chat (the client passes the latest attachments to
``files: true`` commands); ``info`` — every option of one utility; ``save`` / ``show`` — copy the outputs of a run into
Downloads/MemuAI/MemuUtils and show them in the file manager (the card buttons). The skill notes are live: only the
utilities the person allowed for the AI (MemuUtils → AI access) are listed, so a short list costs few tokens.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
from typing import Any, Dict, List

from ..module_integration import CommandError, handler, skill_handler
from . import REGISTRY, RUN_RE, reveal, run_async, runs_dir, save_output, skill_text, tool_help
from .core import human_size

TEXT_FOR_AI = 12_000
TABLE_ROWS_FOR_AI = 40

@skill_handler("utils.skill")
def _skill(row: Dict[str, Any], en: bool) -> str:
    return skill_text("en")

def _pick_files(spec: str, files: List[Dict[str, Any]], en: bool) -> List[Dict[str, Any]]:
    """``file`` argument → entries for :func:`run_async`: attachments by name/number, ``all``, or ``out:`` references."""
    spec = str(spec or "").strip()
    if not spec:
        if not files:
            return []
        newest = min(int(f.get("turn") or 0) for f in files)
        return [{"name": f["name"], "data": f["data"]} for f in files if int(f.get("turn") or 0) == newest]
    if spec.lower() in ("all", "*", "все"):
        return [{"name": f["name"], "data": f["data"]} for f in files]
    out: List[Dict[str, Any]] = []
    for part in [p.strip().strip("«»\"'") for p in spec.split(",") if p.strip()]:
        if part.startswith(("out:", "up:")):
            out.append({"ref": part})
            continue
        low = part.lower()
        match = next((f for f in files if f["name"].lower() == low), None) or next((f for f in files if low in f["name"].lower()), None)
        if match is None and part.lstrip("#").isdigit() and 0 < int(part.lstrip("#")) <= len(files):
            match = files[int(part.lstrip("#")) - 1]
        if match is None:
            names = ", ".join(f["name"] for f in files[:12]) or ("none" if en else "нет")
            raise CommandError((f"No attached file “{part}”. Attached in this chat: {names}" if en
                                else f"Во вложениях чата нет файла «{part}». Есть: {names}"))
        out.append({"name": match["name"], "data": match["data"]})
    return out

def _thumb(path: Path) -> str:
    try:
        from PIL import Image
        with Image.open(path) as img:
            img.seek(0)
            frame = img.convert("RGB")
        frame.thumbnail((180, 180))
        buf = io.BytesIO()
        frame.save(buf, "JPEG", quality=72)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 - no thumbnail for an exotic picture
        return ""

def _meta_path(run: str) -> Path:
    return runs_dir() / run / "meta.json"

def _read_meta(run: str) -> Dict[str, Any]:
    if not RUN_RE.match(str(run or "")):
        raise CommandError("Bad run id / Неверный запуск")
    try:
        return json.loads(_meta_path(run).read_text("utf-8"))
    except (OSError, ValueError):
        return {"run": run}

def _write_meta(run: str, meta: Dict[str, Any]) -> None:
    try:
        _meta_path(run).write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    except OSError:
        pass

def _outputs(run: str) -> List[Path]:
    folder = runs_dir() / run / "out"
    return sorted((p for p in folder.glob("*") if p.is_file()), key=lambda p: p.name) if folder.is_dir() else []

def _card(meta: Dict[str, Any], tr, note: str = "") -> Dict[str, Any]:
    run = meta.get("run") or ""
    outs = _outputs(run) if run else []
    cover = ""
    for p in outs:
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp") and p.stat().st_size < 30 * 1024 * 1024:
            cover = _thumb(p)
            if cover:
                break
    items = [{"title": p.name, "subtitle": human_size(p.stat().st_size, tr("", "en") == "en"), "mono": (p.suffix.lstrip(".")[:4] or "•").upper(),
              "command": "show", "args": {"run": run, "name": p.name}} for p in outs[:6]]
    if outs:
        total = sum(p.stat().st_size for p in outs)
        subtitle = tr(f"Файлов: {len(outs)} · {human_size(total)}", f"Files: {len(outs)} · {human_size(total, True)}")
    else:
        subtitle = (meta.get("summary") or "")[:140]
    actions = []
    if outs:
        actions.append({"label": tr("Сохранить", "Save"), "command": "save", "args": {"run": run}})
        actions.append({"label": tr("Показать в папке", "Show in folder"), "command": "show", "args": {"run": run}})
    actions.append({"icon": "open", "label": tr("Открыть MemuUtils", "Open MemuUtils"), "open": True,
                    "detail": {"util": meta.get("util") or "", "run": run}})
    card = {"kind": "utils", "title": tr(meta.get("title") or "MemuUtils", meta.get("title_en") or meta.get("title") or "MemuUtils"),
            "subtitle": subtitle, "items": items, "actions": actions}
    if cover:
        card["cover"] = cover
    if note or meta.get("note"):
        card["note"] = (note or meta.get("note") or "")[:300]
    return card

def _for_ai(res: Dict[str, Any], en: bool) -> str:
    r = res.get("result") or {}
    lines = [f"{res.get('title') or res.get('util')}: done in {res.get('ms', 0)} ms (run {res.get('run')})."]
    text = str(r.get("text") or "")
    if text:
        cut = text[:TEXT_FOR_AI]
        lines.append("Result text" + (f" (first {TEXT_FOR_AI} of {len(text)} characters)" if len(text) > TEXT_FOR_AI else "")
                     + " — show it to the user (the card shows only its beginning):\n" + cut)
    if r.get("note"):
        lines.append("Note: " + str(r["note"]))
    for key, value in r.get("info") or []:
        lines.append(f"{key}: {value}")
    table = r.get("table") or {}
    if table.get("columns"):
        rows = table.get("rows") or []
        lines.append("Table (" + str(len(rows)) + " rows):")
        lines.append("\t".join(str(c) for c in table["columns"]))
        lines += ["\t".join(str(c) for c in row) for row in rows[:TABLE_ROWS_FOR_AI]]
        if len(rows) > TABLE_ROWS_FOR_AI:
            lines.append(f"… {len(rows) - TABLE_ROWS_FOR_AI} more rows")
    if r.get("swatches"):
        lines.append("Colors: " + ", ".join(r["swatches"]))
    if r.get("job"):
        lines.append(f"Background job started: {r['job'].get('title')}")
    outs = res.get("outputs") or []
    if outs:
        lines.append("Output files (shown in the card with Save / Show buttons; chain with file=\"<ref>\"):")
        lines += [f"- {o['name']} ({o.get('size_h') or o.get('size')}) ref={o['ref']}" for o in outs[:20]]
    return "\n".join(lines)

@handler("utils.run")
async def utils_run(args, ctx):
    en, tr = ctx["en"], ctx["tr"]
    util_id = str(args.get("util") or "").strip()
    util = REGISTRY.get(util_id)
    entries: List[Dict[str, Any]] = []
    if util is not None and (util.input in ("file", "files", "any") or args.get("file")):
        entries = _pick_files(str(args.get("file") or ""), ctx.get("files") or [], en)
        if util.input in ("file", "files") and not entries:
            return {"ok": False, "error": tr("Пользователь не прикрепил файл в этом чате — попросите прикрепить файл или откройте MemuUtils",
                                             "The user has not attached a file in this chat — ask them to attach it, or open MemuUtils")}
    res = await run_async(util_id, text=str(args.get("text") or ""), text2=str(args.get("text2") or ""), files=entries,
                          opts=args.get("opts") or {}, lang=ctx["lang"], source="chat", confirmed=bool(ctx.get("confirmed")))
    if res.get("needs_confirm"):
        title = (util.title_en if en else util.title) if util else util_id
        return {"ok": False, "needs_confirm": True, "module": "utils", "command": "run", "args": args, "title": title,
                "error": res.get("error") or tr("Нужно подтверждение пользователя", "The user has to confirm this")}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or tr("Утилита не сработала", "The utility failed")}
    r = res.get("result") or {}
    summary = str(r.get("text") or r.get("note") or "").strip().split("\n")[0]
    if not summary and r.get("info"):
        summary = " · ".join(f"{a}: {b}" for a, b in r["info"][:2])
    meta = {"run": res["run"], "util": util_id, "title": util.title if util else util_id, "title_en": util.title_en if util else util_id,
            "summary": summary[:200], "note": str(r.get("note") or "")[:300]}
    await asyncio.to_thread(_write_meta, res["run"], meta)
    card = await asyncio.to_thread(_card, meta, tr)
    if not res.get("outputs") and r.get("text"):
        card["note"] = str(r["text"])[:300]
    return {"ok": True, "text": _for_ai(res, en), "card": card}

@handler("utils.info")
async def utils_info(args, ctx):
    util_id = str(args.get("util") or "").strip()
    if util_id not in REGISTRY:
        close = [u for u in REGISTRY if util_id and util_id.split(".")[-1] in u][:8]
        raise CommandError(ctx["tr"](f"Нет утилиты «{util_id}»", f"No utility “{util_id}”") + (f". Similar: {', '.join(close)}" if close else ""))
    return {"text": tool_help(util_id, "en")}

def _save(run: str, name: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    saved = save_output(run, name)
    meta.setdefault("saved", {})
    if name:
        meta["saved"][name] = saved["path"]
    else:
        for path in saved["saved"]:
            meta["saved"][Path(path).name] = path
        meta["saved_folder"] = saved["folder"]
    _write_meta(run, meta)
    return saved

@handler("utils.save")
async def utils_save(args, ctx):
    tr = ctx["tr"]
    run, name = str(args.get("run") or ""), str(args.get("name") or "")
    meta = await asyncio.to_thread(_read_meta, run)
    try:
        saved = await asyncio.to_thread(_save, run, name, meta)
    except Exception as exc:  # noqa: BLE001 - UtilError and file errors read the same in the card
        text = exc.text(ctx["lang"]) if hasattr(exc, "text") else str(exc)
        return {"ok": False, "error": text}
    note = tr(f"Сохранено: {saved['path']}", f"Saved: {saved['path']}")
    return {"text": note, "card": await asyncio.to_thread(_card, meta, tr, note)}

@handler("utils.show")
async def utils_show(args, ctx):
    tr = ctx["tr"]
    run, name = str(args.get("run") or ""), str(args.get("name") or "")
    meta = await asyncio.to_thread(_read_meta, run)
    saved = meta.get("saved") or {}
    target = saved.get(name) if name else meta.get("saved_folder")
    if not target or not Path(target).exists():
        try:
            result = await asyncio.to_thread(_save, run, name, meta)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": exc.text(ctx["lang"]) if hasattr(exc, "text") else str(exc)}
        target = result["path"]
    shown = await asyncio.to_thread(reveal, str(target))
    note = tr(f"Сохранено: {target}", f"Saved: {target}") if shown else tr(f"Файл сохранён: {target} (окно папки открыть не удалось)",
                                                                          f"Saved: {target} (could not open the folder window)")
    return {"text": note, "card": await asyncio.to_thread(_card, meta, tr, note)}
