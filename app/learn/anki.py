# -*- coding: utf-8 -*-
"""MemuLearn ⇄ Anki and CSV.

* ``.apkg`` is a zip: ``collection.anki2`` (or ``collection.anki21``) — SQLite of the legacy schema 11 (``col`` with
  JSON ``models``/``decks``, ``notes`` with fields joined by ``\\x1f``, ``cards``) — plus ``media`` (JSON ``{"0": "a.png"}``)
  and the media files named ``0``, ``1``… Import reads the Basic-like note types (first field → question, second → answer,
  the rest → extra; a second template = the reversed card too) and Cloze (``type == 1``; ``Text`` + ``Back Extra``).
  Images become ``data:`` URLs inside the card (≤ 512 KB each), sounds a «🔊 name» mark. A package from Anki ≥ 2.1.50
  without «Support older Anki versions» keeps the real collection in ``collection.anki21b`` (zstd, new schema): read
  through the optional ``zstandard`` package when it is installed, otherwise a clear error.
* Export writes Basic and Cloze notes (the ``$…$``/``$$…$$`` math → MathJax ``\\(…\\)``/``\\[…\\]``), one deck per course,
  cards as new (Anki schedules them itself).
* CSV: ``question;answer;tags`` (the delimiter is detected; a header row is skipped); ``{{c1::…}}`` makes a Cloze card.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import mimetypes
import re
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .store import CLOZE_RE, LearnError, Store

MAX_APKG_BYTES = 200 * 1024 * 1024
MAX_IMAGE_BYTES = 512 * 1024
MAX_NOTES = 50000
_IMG_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']?([^"'\s>]+)["']?[^>]*>""", re.I)
_SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
_DATA_IMG_RE = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*["'])(data:image/([a-z+]+);base64,([A-Za-z0-9+/=]+))(["'])""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

def math_from_anki(text: str) -> str:
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: "$$" + m.group(1) + "$$", text, flags=re.S)
    return re.sub(r"\\\((.+?)\\\)", lambda m: "$" + m.group(1) + "$", text, flags=re.S)

def math_to_anki(text: str) -> str:
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: r"\[" + m.group(1) + r"\]", text, flags=re.S)
    return re.sub(r"(?<![\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)", lambda m: r"\(" + m.group(1) + r"\)", text)

def strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()

def _simplify_html(text: str) -> str:
    """Anki fields are HTML; keep it (the module sanitises on render) but turn the common wrappers into line breaks."""
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"</(div|p)>\s*<\1[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"^\s*<(div|p)[^>]*>|</(div|p)>\s*$", "", text, flags=re.I)
    text = text.replace("&nbsp;", " ")
    return text.strip()

def import_csv(store: Store, cid: str, text: str, lesson_id: str = "") -> Dict[str, Any]:
    text = str(text or "").lstrip("\ufeff")
    if not text.strip():
        raise LearnError("Файл пустой", "The file is empty")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    added = skipped = 0
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if not cells or not cells[0]:
            skipped += 1
            continue
        if i == 0 and cells[0].lower() in ("front", "question", "вопрос", "лицо", "лицевая сторона", "#separator:tab"):
            continue
        if cells[0].startswith("#"):
            continue
        front, back = cells[0], cells[1] if len(cells) > 1 else ""
        tags = cells[2] if len(cells) > 2 else ""
        kind = "cloze" if CLOZE_RE.search(front) else "basic"
        try:
            store.add_note(cid, math_from_anki(front), math_from_anki(back), kind=kind, lesson_id=lesson_id, tags=tags, origin="csv")
            added += 1
        except LearnError:
            skipped += 1
    return {"added": added, "skipped": skipped}

def export_csv(store: Store, cid: str) -> Dict[str, Any]:
    course = store.course(cid)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["front", "back", "tags"])
    seen = set()
    for card in store.cards(cid, limit=2000)["cards"] + store.cards(cid, limit=100000, offset=2000)["cards"]:
        key = (card["kind"], card["front"]) if card["kind"] == "cloze" else card["id"]
        if key in seen:
            continue
        seen.add(key)
        writer.writerow([card["front"], card["back"], card["tags"]])
    return {"name": _safe_name(course["title"]) + ".csv", "text": buf.getvalue(), "count": len(seen)}

def _open_collection(zf: zipfile.ZipFile, folder: Path) -> Path:
    names = set(zf.namelist())
    target = folder / "collection.sqlite"
    if "collection.anki21b" in names:
        try:
            import zstandard
            data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(zf.read("collection.anki21b"))).read()
            target.write_bytes(data)
            return target
        except ImportError:
            if "collection.anki21" not in names and _legacy_is_placeholder(zf, folder):
                raise LearnError("Колода в новом формате Anki. Экспортируйте её в Anki с галочкой «Поддержка старых версий Anki»",
                                 "This deck uses the new Anki format. Export it from Anki with «Support older Anki versions» ticked",
                                 "apkg_new_format")
    for name in ("collection.anki21", "collection.anki2"):
        if name in names:
            target.write_bytes(zf.read(name))
            return target
    raise LearnError("Это не колода Anki (.apkg)", "This is not an Anki deck (.apkg)", "apkg_invalid")

def _legacy_is_placeholder(zf: zipfile.ZipFile, folder: Path) -> bool:
    if "collection.anki2" not in zf.namelist():
        return True
    probe = folder / "probe.sqlite"
    probe.write_bytes(zf.read("collection.anki2"))
    try:
        con = sqlite3.connect(str(probe))
        rows = con.execute("SELECT flds FROM notes LIMIT 2").fetchall()
        con.close()
        return len(rows) <= 1 and any("update" in (r[0] or "").lower() for r in rows)
    except sqlite3.Error:
        return True

def _models(con: sqlite3.Connection) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, str]]:
    models: Dict[int, Dict[str, Any]] = {}
    decks: Dict[int, str] = {}
    try:
        row = con.execute("SELECT models, decks FROM col").fetchone()
        if row and row[0]:
            for key, m in json.loads(row[0]).items():
                models[int(key)] = {"type": int(m.get("type") or 0), "name": m.get("name") or "",
                                    "fields": [f.get("name") for f in sorted(m.get("flds") or [], key=lambda f: f.get("ord", 0))],
                                    "templates": len(m.get("tmpls") or [])}
        if row and row[1]:
            decks = {int(k): str(d.get("name") or "") for k, d in json.loads(row[1]).items()}
    except (sqlite3.Error, ValueError):
        pass
    if not models:
        try:
            for mid, name in con.execute("SELECT id, name FROM notetypes"):
                fields = [r[0] for r in con.execute("SELECT name FROM fields WHERE ntid=? ORDER BY ord", (mid,))]
                tmpls = con.execute("SELECT COUNT(*) FROM templates WHERE ntid=?", (mid,)).fetchone()[0]
                is_cloze = "cloze" in str(name).lower() or (fields and fields[0].lower() == "text" and tmpls == 1)
                models[int(mid)] = {"type": 1 if is_cloze else 0, "name": name, "fields": fields, "templates": tmpls}
            decks = {int(i): str(n).replace("\x1f", "::") for i, n in con.execute("SELECT id, name FROM decks")}
        except sqlite3.Error:
            pass
    return models, decks

def _inline_media(text: str, zf: zipfile.ZipFile, media: Dict[str, str], budget: List[int]) -> str:
    def image(m: "re.Match[str]") -> str:
        name = html.unescape(m.group(1))
        member = media.get(name)
        if member is None or name.startswith("data:"):
            return m.group(0)
        try:
            raw = zf.read(member)
        except KeyError:
            return m.group(0)
        mime = mimetypes.guess_type(name)[0] or "image/png"
        if len(raw) > MAX_IMAGE_BYTES or budget[0] < len(raw) or not mime.startswith("image/"):
            return f"[{html.escape(name)}]"
        budget[0] -= len(raw)
        return f'<img src="data:{mime};base64,{base64.b64encode(raw).decode()}" alt="{html.escape(name)}">'
    text = _IMG_RE.sub(image, text)
    return _SOUND_RE.sub(lambda m: "🔊 " + m.group(1), text)

def import_apkg(store: Store, raw: bytes, *, name: str = "", course_id: str = "", lang: str = "ru") -> Dict[str, Any]:
    if len(raw) > MAX_APKG_BYTES:
        raise LearnError("Файл больше 200 МБ", "The file is larger than 200 MB", "too_large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise LearnError("Это не колода Anki (.apkg)", "This is not an Anki deck (.apkg)", "apkg_invalid")
    with tempfile.TemporaryDirectory() as tmp:
        path = _open_collection(zf, Path(tmp))
        con = sqlite3.connect(str(path))
        try:
            models, decks = _models(con)
            media: Dict[str, str] = {}
            if "media" in zf.namelist():
                try:
                    media = {v: k for k, v in json.loads(zf.read("media").decode("utf-8") or "{}").items()}
                except (ValueError, UnicodeDecodeError):
                    media = {}
            notes = con.execute("SELECT id, mid, flds, tags FROM notes LIMIT ?", (MAX_NOTES,)).fetchall()
            first_deck = {nid: did for nid, did in con.execute("SELECT nid, MIN(did) FROM cards GROUP BY nid")}
        finally:
            con.close()
        deck_name = ""
        if first_deck:
            counts: Dict[int, int] = {}
            for did in first_deck.values():
                counts[did] = counts.get(did, 0) + 1
            deck_name = decks.get(max(counts, key=counts.get), "")
        title = (deck_name.split("::")[-1] if deck_name and deck_name != "Default" else "") or Path(name or "Anki").stem or "Anki"
        if course_id:
            store.course(course_id)
            cid = course_id
        else:
            cid = store.create_course(title, ("Импорт из Anki" if lang != "en" else "Imported from Anki"),
                                      source_kind="anki", source_name=Path(name or "deck.apkg").name, lang=lang)["id"]
        budget = [40 * 1024 * 1024]
        added = skipped = 0
        for _nid, mid, flds, tags in notes:
            model = models.get(int(mid)) or {"type": 0, "fields": [], "templates": 1}
            fields = [_inline_media(_simplify_html(math_from_anki(f)), zf, media, budget) for f in str(flds or "").split("\x1f")]
            if not fields or not strip_html(fields[0]) and "<img" not in fields[0]:
                skipped += 1
                continue
            tag_text = " ".join(str(tags or "").split())
            try:
                if model["type"] == 1:
                    store.add_note(cid, fields[0], "", kind="cloze", extra=fields[1] if len(fields) > 1 else "", tags=tag_text, origin="anki")
                else:
                    back = fields[1] if len(fields) > 1 else ""
                    extra = "\n".join(f for f in fields[2:] if strip_html(f))
                    store.add_card(cid, fields[0], back, extra=extra, tags=tag_text, origin="anki")
                    if model.get("templates", 1) >= 2 and back:
                        store.add_card(cid, back, fields[0], extra=extra, tags=tag_text, origin="anki", ord_=2)
                added += 1
            except LearnError:
                skipped += 1
    return {"course_id": cid, "title": store.course(cid)["title"], "notes": added, "skipped": skipped}

_SCHEMA_11 = """
CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null, scm integer not null, ver integer not null,
  dty integer not null, usn integer not null, ls integer not null, conf text not null, models text not null, decks text not null,
  dconf text not null, tags text not null);
CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null, mod integer not null, usn integer not null,
  tags text not null, flds text not null, sfld integer not null, csum integer not null, flags integer not null, data text not null);
CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null, ord integer not null, mod integer not null,
  usn integer not null, type integer not null, queue integer not null, due integer not null, ivl integer not null, factor integer not null,
  reps integer not null, lapses integer not null, left integer not null, odue integer not null, odid integer not null,
  flags integer not null, data text not null);
CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null, ease integer not null, ivl integer not null,
  lastIvl integer not null, factor integer not null, time integer not null, type integer not null);
CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
CREATE INDEX ix_notes_usn on notes (usn); CREATE INDEX ix_cards_usn on cards (usn); CREATE INDEX ix_revlog_usn on revlog (usn);
CREATE INDEX ix_cards_nid on cards (nid); CREATE INDEX ix_cards_sched on cards (did, queue, due); CREATE INDEX ix_revlog_cid on revlog (cid);
CREATE INDEX ix_notes_csum on notes (csum);
"""
_CSS = ".card { font-family: arial; font-size: 20px; text-align: center; color: black; background-color: white; }\n.cloze { font-weight: bold; color: blue; }"

def _field(name: str, ord_: int) -> Dict[str, Any]:
    return {"name": name, "ord": ord_, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []}

def _model(mid: int, did: int, now: int, cloze: bool) -> Dict[str, Any]:
    if cloze:
        tmpls = [{"name": "Cloze", "ord": 0, "qfmt": "{{cloze:Text}}", "afmt": "{{cloze:Text}}<br>\n{{Back Extra}}",
                  "did": None, "bqfmt": "", "bafmt": ""}]
        flds = [_field("Text", 0), _field("Back Extra", 1)]
    else:
        tmpls = [{"name": "Card 1", "ord": 0, "qfmt": "{{Front}}", "afmt": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}",
                  "did": None, "bqfmt": "", "bafmt": ""}]
        flds = [_field("Front", 0), _field("Back", 1)]
    return {"id": mid, "name": "Cloze (MemuLearn)" if cloze else "Basic (MemuLearn)", "type": 1 if cloze else 0, "mod": now,
            "usn": -1, "sortf": 0, "did": did, "tmpls": tmpls, "flds": flds, "css": _CSS, "tags": [], "vers": [],
            "latexPre": "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n\\usepackage[utf8]{inputenc}\n"
                        "\\usepackage{amssymb,amsmath}\n\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n",
            "latexPost": "\\end{document}", "req": [[0, "any", [0]]]}

def _deck(did: int, name: str, now: int) -> Dict[str, Any]:
    return {"id": did, "name": name, "mod": now, "usn": -1, "lrnToday": [0, 0], "revToday": [0, 0], "newToday": [0, 0],
            "timeToday": [0, 0], "collapsed": False, "browserCollapsed": False, "desc": "", "dyn": 0, "conf": 1,
            "extendNew": 0, "extendRev": 0}

_DCONF = {"1": {"id": 1, "name": "Default", "mod": 0, "usn": 0, "maxTaken": 60, "autoplay": True, "timer": 0, "replayq": True,
                "dyn": False, "new": {"delays": [1, 10], "ints": [1, 4, 7], "initialFactor": 2500, "order": 1, "perDay": 20,
                                      "bury": False},
                "rev": {"perDay": 200, "ease4": 1.3, "fuzz": 0.05, "maxIvl": 36500, "ivlFct": 1, "bury": False, "hardFactor": 1.2},
                "lapse": {"delays": [10], "mult": 0, "minInt": 1, "leechFails": 8, "leechAction": 1}}}

def _csum(text: str) -> int:
    return int(hashlib.sha1(strip_html(text).encode("utf-8")).hexdigest()[:8], 16)

def _safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "course").strip()[:80] or "course"

def export_apkg(store: Store, cid: str) -> Dict[str, Any]:
    course = store.course(cid)
    now = int(time.time())
    did, basic_mid, cloze_mid = now * 1000 + 1, now * 1000 + 2, now * 1000 + 3
    media: Dict[str, bytes] = {}

    def outline_media(text: str) -> str:
        def repl(m: "re.Match[str]") -> str:
            raw = base64.b64decode(m.group(4))
            name = f"memulearn-{hashlib.sha1(raw).hexdigest()[:12]}.{m.group(3).replace('svg+xml', 'svg').replace('jpeg', 'jpg')}"
            media[name] = raw
            return m.group(1) + name + m.group(5)
        return _DATA_IMG_RE.sub(repl, text)

    def field(text: str) -> str:
        return outline_media(math_to_anki(text or "")).replace("\n", "<br>")

    out = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "collection.anki2"
        con = sqlite3.connect(str(path))
        con.executescript(_SCHEMA_11)
        models = {str(basic_mid): _model(basic_mid, did, now, False), str(cloze_mid): _model(cloze_mid, did, now, True)}
        decks = {"1": _deck(1, "Default", now), str(did): _deck(did, course["title"], now)}
        conf = {"nextPos": 1, "estTimes": True, "activeDecks": [1], "sortType": "noteFld", "timeLim": 0, "sortBackwards": False,
                "addToCur": True, "curDeck": 1, "newBury": True, "newSpread": 0, "dueCounts": True, "curModel": str(basic_mid),
                "collapseTime": 1200}
        con.execute("INSERT INTO col VALUES(1,?,?,?,11,0,0,0,?,?,?,?,'{}')",
                    (now - now % 86400, now * 1000, now * 1000, json.dumps(conf), json.dumps(models), json.dumps(decks), json.dumps(_DCONF)))
        notes_seen: Dict[Tuple[str, str], int] = {}
        nid_seq, cid_seq, pos = now * 1000, now * 1000, 0
        for card in store.cards(cid, limit=2000)["cards"] + store.cards(cid, limit=100000, offset=2000)["cards"]:
            cloze = card["kind"] == "cloze"
            key = ("cloze", card["front"]) if cloze else ("basic", card["id"])
            nid = notes_seen.get(key)
            if nid is None:
                nid_seq += 1
                nid = notes_seen[key] = nid_seq
                flds = [field(card["front"]), field(card["extra"] if cloze else card["back"])]
                if cloze and card["back"]:
                    flds[1] = field("\n".join(x for x in (card["extra"], card["back"]) if x))
                con.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,0,'')",
                            (nid, uuid.uuid4().hex[:10], cloze_mid if cloze else basic_mid, now, -1,
                             (" " + " ".join(card["tags"].split()) + " ") if card["tags"].strip() else "",
                             "\x1f".join(flds), strip_html(flds[0])[:200], _csum(flds[0])))
            cid_seq += 1
            pos += 1
            con.execute("INSERT INTO cards VALUES(?,?,?,?,?,-1,0,0,?,0,0,0,0,0,0,0,0,'')",
                        (cid_seq, nid, did, max(0, int(card["ord"] or 1) - 1) if cloze else 0, now, pos))
        con.commit()
        con.close()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(path, "collection.anki2")
            index = {}
            for i, (name, raw) in enumerate(media.items()):
                zf.writestr(str(i), raw)
                index[str(i)] = name
            zf.writestr("media", json.dumps(index))
    data = out.getvalue()
    return {"name": _safe_name(course["title"]) + ".apkg", "data": base64.b64encode(data).decode(), "bytes": len(data),
            "notes": len(notes_seen)}
