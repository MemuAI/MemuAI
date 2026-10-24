# -*- coding: utf-8 -*-
"""MemuLearn storage: one SQLite file ``MEMUAI_DATA_DIR/learn/learn.sqlite``.

Tables: ``courses`` → ``nodes`` (a tree: module → lesson; a lesson keeps its summary in Markdown) → ``cards``
(Basic or Cloze, FSRS fields inline) → ``reviews`` (the log; ``prev`` keeps the card state before the answer, so the
last answer can be undone) and ``exams`` (tickets, answers and the AI verdicts). Settings live in ``meta``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import fsrs

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS courses (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '', source_kind TEXT DEFAULT 'topic',
  source_ref TEXT DEFAULT '', source_name TEXT DEFAULT '', lang TEXT DEFAULT 'ru', hue INTEGER DEFAULT 210,
  created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, course_id TEXT NOT NULL, parent_id TEXT DEFAULT '', kind TEXT DEFAULT 'lesson',
  title TEXT NOT NULL, summary TEXT DEFAULT '', brief TEXT DEFAULT '', ord INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
  created REAL);
CREATE INDEX IF NOT EXISTS nodes_course ON nodes(course_id, ord);
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY, course_id TEXT NOT NULL, lesson_id TEXT DEFAULT '', kind TEXT DEFAULT 'basic',
  front TEXT NOT NULL, back TEXT DEFAULT '', extra TEXT DEFAULT '', ord INTEGER DEFAULT 1, tags TEXT DEFAULT '',
  origin TEXT DEFAULT '', suspended INTEGER DEFAULT 0, created REAL,
  state INTEGER DEFAULT 0, step INTEGER, stability REAL, difficulty REAL, due REAL DEFAULT 0, last_review REAL,
  reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS cards_due ON cards(suspended, due);
CREATE INDEX IF NOT EXISTS cards_course ON cards(course_id, lesson_id);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL, course_id TEXT DEFAULT '', rating INTEGER, at REAL,
  state INTEGER, interval REAL, duration_ms INTEGER DEFAULT 0, device TEXT DEFAULT '', prev TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS reviews_at ON reviews(at);
CREATE TABLE IF NOT EXISTS exams (
  id TEXT PRIMARY KEY, course_id TEXT NOT NULL, title TEXT DEFAULT '', questions TEXT DEFAULT '[]',
  answers TEXT DEFAULT '{}', results TEXT DEFAULT '{}', score REAL, created REAL, finished REAL);
"""

DEFAULT_SETTINGS: Dict[str, Any] = {
    "desired_retention": 0.9,   # FSRS target recall
    "new_per_day": 20,          # new cards introduced per day (all courses)
    "max_reviews": 200,         # reviews per day
    "fuzz": True,
    "seconds_per_card": 9,      # the «M min» estimate on the panel until there is own history
    "day_starts": 4,            # the learning day starts at 4:00 local time (as Anki)
}
CARD_KINDS = ("basic", "cloze")
CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.S)
_LOCK = threading.RLock()
_STORES: Dict[str, "Store"] = {}


class LearnError(ValueError):
    """A user-facing problem (bilingual)."""

    def __init__(self, ru: str, en: str = "", code: str = "bad_request") -> None:
        super().__init__(ru)
        self.ru, self.en, self.code = ru, en or ru, code

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else self.ru


def data_root() -> Path:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return Path(env) / "learn"
    from ..config import DATA_DIR
    return Path(DATA_DIR) / "learn"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def clean(value: Any, limit: int = 20000) -> str:
    return str(value if value is not None else "").replace("\x00", "").strip()[:limit]


def cloze_numbers(text: str) -> List[int]:
    return sorted({int(m.group(1)) for m in CLOZE_RE.finditer(text or "") if int(m.group(1)) > 0})


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "learn.sqlite"
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ------------------------------------------------------------------ helpers
    def q(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        with _LOCK:
            return [dict(r) for r in self._db.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def run(self, sql: str, params: Iterable[Any] = ()) -> None:
        with _LOCK:
            self._db.execute(sql, tuple(params))
            self._db.commit()

    def many(self, sql: str, rows: List[Iterable[Any]]) -> None:
        with _LOCK:
            self._db.executemany(sql, [tuple(r) for r in rows])
            self._db.commit()

    def close(self) -> None:
        with _LOCK:
            self._db.close()

    # ------------------------------------------------------------------ settings
    def settings(self) -> Dict[str, Any]:
        row = self.one("SELECT value FROM meta WHERE key='settings'")
        out = dict(DEFAULT_SETTINGS)
        if row:
            try:
                out.update({k: v for k, v in json.loads(row["value"]).items() if k in DEFAULT_SETTINGS})
            except ValueError:
                pass
        return out

    def set_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        cur = self.settings()
        for key, value in (patch or {}).items():
            if key == "desired_retention":
                cur[key] = round(min(0.97, max(0.75, float(value))), 3)
            elif key in ("new_per_day", "max_reviews"):
                cur[key] = int(min(9999, max(0, int(value))))
            elif key == "fuzz":
                cur[key] = bool(value)
            elif key == "day_starts":
                cur[key] = int(min(12, max(0, int(value))))
        self.run("INSERT OR REPLACE INTO meta(key, value) VALUES('settings', ?)", (json.dumps(cur),))
        return cur

    def scheduler(self) -> fsrs.Scheduler:
        s = self.settings()
        return fsrs.Scheduler(desired_retention=s["desired_retention"], enable_fuzzing=bool(s["fuzz"]))

    # ------------------------------------------------------------------ courses and the tree
    def create_course(self, title: str, description: str = "", *, source_kind: str = "topic", source_ref: str = "",
                      source_name: str = "", lang: str = "ru", hue: Optional[int] = None) -> Dict[str, Any]:
        title = clean(title, 200)
        if not title:
            raise LearnError("Название курса пустое", "The course title is empty")
        cid, now = new_id(), time.time()
        if hue is None:   # a calm spread of colours: the golden angle over the number of courses
            hue = (int(self.one("SELECT COUNT(*) AS n FROM courses")["n"]) * 137 + 168) % 360
        self.run("INSERT INTO courses(id,title,description,source_kind,source_ref,source_name,lang,hue,created,updated)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (cid, title, clean(description, 2000), source_kind, clean(source_ref, 500), clean(source_name, 300),
                  "en" if lang == "en" else "ru", int(hue) % 360, now, now))
        return self.course(cid)

    def course(self, cid: str) -> Dict[str, Any]:
        row = self.one("SELECT * FROM courses WHERE id=?", (cid,))
        if not row:
            raise LearnError("Курс не найден", "Course not found", "not_found")
        return row

    def update_course(self, cid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.course(cid)
        fields = {"title": 200, "description": 2000}
        for key, limit in fields.items():
            if key in patch and clean(patch[key], limit):
                self.run(f"UPDATE courses SET {key}=?, updated=? WHERE id=?", (clean(patch[key], limit), time.time(), cid))
        if "hue" in patch:
            self.run("UPDATE courses SET hue=? WHERE id=?", (int(patch["hue"]) % 360, cid))
        return self.course(cid)

    def delete_course(self, cid: str) -> Dict[str, Any]:
        self.course(cid)
        with _LOCK:
            for table, column in (("reviews", "course_id"), ("cards", "course_id"), ("nodes", "course_id"),
                                  ("exams", "course_id"), ("courses", "id")):
                self._db.execute(f"DELETE FROM {table} WHERE {column}=?", (cid,))
            self._db.commit()
        return {"ok": True}

    def touch(self, cid: str) -> None:
        self.run("UPDATE courses SET updated=? WHERE id=?", (time.time(), cid))

    def add_node(self, cid: str, title: str, *, parent_id: str = "", kind: str = "lesson", brief: str = "",
                 summary: str = "", ord_: Optional[int] = None) -> Dict[str, Any]:
        self.course(cid)
        title = clean(title, 300)
        if not title:
            raise LearnError("Пустое название", "The title is empty")
        if parent_id and not self.one("SELECT id FROM nodes WHERE id=? AND course_id=?", (parent_id, cid)):
            raise LearnError("Раздел не найден", "Section not found", "not_found")
        if ord_ is None:
            row = self.one("SELECT COALESCE(MAX(ord), -1) + 1 AS n FROM nodes WHERE course_id=? AND parent_id=?", (cid, parent_id))
            ord_ = int(row["n"]) if row else 0
        nid = new_id()
        self.run("INSERT INTO nodes(id,course_id,parent_id,kind,title,summary,brief,ord,created) VALUES(?,?,?,?,?,?,?,?,?)",
                 (nid, cid, parent_id, "module" if kind == "module" else "lesson", title, clean(summary, 60000),
                  clean(brief, 1200), ord_, time.time()))
        self.touch(cid)
        return self.node(nid)

    def node(self, nid: str) -> Dict[str, Any]:
        row = self.one("SELECT * FROM nodes WHERE id=?", (nid,))
        if not row:
            raise LearnError("Урок не найден", "Lesson not found", "not_found")
        return row

    def update_node(self, nid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        row = self.node(nid)
        if clean(patch.get("title"), 300):
            self.run("UPDATE nodes SET title=? WHERE id=?", (clean(patch["title"], 300), nid))
        if "summary" in patch:
            self.run("UPDATE nodes SET summary=? WHERE id=?", (clean(patch["summary"], 60000), nid))
        if "brief" in patch:
            self.run("UPDATE nodes SET brief=? WHERE id=?", (clean(patch["brief"], 1200), nid))
        if "done" in patch:
            self.run("UPDATE nodes SET done=? WHERE id=?", (1 if patch["done"] else 0, nid))
        self.touch(row["course_id"])
        return self.node(nid)

    def delete_node(self, nid: str) -> Dict[str, Any]:
        row = self.node(nid)
        ids = [nid] + [r["id"] for r in self.q("SELECT id FROM nodes WHERE parent_id=?", (nid,))]
        with _LOCK:
            for x in ids:
                self._db.execute("DELETE FROM reviews WHERE card_id IN (SELECT id FROM cards WHERE lesson_id=?)", (x,))
                self._db.execute("DELETE FROM cards WHERE lesson_id=?", (x,))
                self._db.execute("DELETE FROM nodes WHERE id=?", (x,))
            self._db.commit()
        self.touch(row["course_id"])
        return {"ok": True}

    def tree(self, cid: str) -> List[Dict[str, Any]]:
        nodes = self.q("SELECT id,parent_id,kind,title,brief,ord,done,LENGTH(summary) AS summary_len FROM nodes"
                       " WHERE course_id=? ORDER BY ord, created", (cid,))
        counts = {r["lesson_id"]: r for r in self.q(
            "SELECT lesson_id, COUNT(*) AS cards, SUM(CASE WHEN state=0 THEN 1 ELSE 0 END) AS new,"
            " SUM(CASE WHEN state>0 AND due<=? AND suspended=0 THEN 1 ELSE 0 END) AS due FROM cards WHERE course_id=? GROUP BY lesson_id",
            (self.day_end(), cid))}
        by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for n in nodes:
            c = counts.get(n["id"]) or {}
            n.update(cards=int(c.get("cards") or 0), due=int(c.get("due") or 0), new=int(c.get("new") or 0), done=bool(n["done"]))
            by_parent.setdefault(n["parent_id"] or "", []).append(n)
        out = []
        for top in by_parent.get("", []):
            top["children"] = by_parent.get(top["id"], [])
            out.append(top)
        return out

    # ------------------------------------------------------------------ cards
    def add_card(self, cid: str, front: str, back: str = "", *, kind: str = "basic", lesson_id: str = "",
                 extra: str = "", tags: str = "", origin: str = "", ord_: int = 1, due: Optional[float] = None) -> Dict[str, Any]:
        self.course(cid)
        front, back = clean(front, 8000), clean(back, 12000)
        kind = kind if kind in CARD_KINDS else "basic"
        if not front:
            raise LearnError("Пустая карточка", "The card is empty")
        if kind == "cloze" and not cloze_numbers(front):
            raise LearnError("В тексте нет пропусков {{c1::…}}", "The text has no {{c1::…}} deletions")
        card_id = new_id()
        self.run("INSERT INTO cards(id,course_id,lesson_id,kind,front,back,extra,ord,tags,origin,created,due)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (card_id, cid, lesson_id or "", kind, front, back, clean(extra, 4000), int(ord_ or 1), clean(tags, 300),
                  clean(origin, 40), time.time(), time.time() if due is None else due))
        return self.card(card_id)

    def add_note(self, cid: str, front: str, back: str = "", *, kind: str = "basic", **kw: Any) -> List[Dict[str, Any]]:
        """A Cloze note becomes one card per deletion number (as Anki); Basic — one card."""
        if kind == "cloze":
            nums = cloze_numbers(front)
            if not nums:
                raise LearnError("В тексте нет пропусков {{c1::…}}", "The text has no {{c1::…}} deletions")
            return [self.add_card(cid, front, back, kind="cloze", ord_=n, **kw) for n in nums]
        return [self.add_card(cid, front, back, kind="basic", **kw)]

    def card(self, card_id: str) -> Dict[str, Any]:
        row = self.one("SELECT * FROM cards WHERE id=?", (card_id,))
        if not row:
            raise LearnError("Карточка не найдена", "Card not found", "not_found")
        return row

    def update_card(self, card_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        row = self.card(card_id)
        for key, limit in (("front", 8000), ("back", 12000), ("extra", 4000), ("tags", 300)):
            if key in patch:
                value = clean(patch[key], limit)
                if key == "front" and not value:
                    raise LearnError("Пустая карточка", "The card is empty")
                self.run(f"UPDATE cards SET {key}=? WHERE id=?", (value, card_id))
        if "suspended" in patch:
            self.run("UPDATE cards SET suspended=? WHERE id=?", (1 if patch["suspended"] else 0, card_id))
        if "lesson_id" in patch:
            self.run("UPDATE cards SET lesson_id=? WHERE id=?", (clean(patch["lesson_id"], 40), card_id))
        if patch.get("reset"):
            self.run("UPDATE cards SET state=0, step=0, stability=NULL, difficulty=NULL, due=?, last_review=NULL, reps=0, lapses=0"
                     " WHERE id=?", (time.time(), card_id))
        _ = row
        return self.card(card_id)

    def delete_card(self, card_id: str) -> Dict[str, Any]:
        self.card(card_id)
        with _LOCK:
            self._db.execute("DELETE FROM reviews WHERE card_id=?", (card_id,))
            self._db.execute("DELETE FROM cards WHERE id=?", (card_id,))
            self._db.commit()
        return {"ok": True}

    def cards(self, cid: str = "", lesson_id: str = "", query: str = "", limit: int = 500, offset: int = 0) -> Dict[str, Any]:
        where, params = ["1=1"], []
        if cid:
            where.append("course_id=?"); params.append(cid)
        if lesson_id:
            where.append("lesson_id=?"); params.append(lesson_id)
        if clean(query, 200):
            where.append("(front LIKE ? OR back LIKE ? OR tags LIKE ?)")
            params += [f"%{clean(query, 200)}%"] * 3
        sql = " AND ".join(where)
        total = self.one(f"SELECT COUNT(*) AS n FROM cards WHERE {sql}", params)["n"]
        rows = self.q(f"SELECT * FROM cards WHERE {sql} ORDER BY created, ord LIMIT ? OFFSET ?",
                      params + [max(1, min(int(limit), 2000)), max(0, int(offset))])
        return {"total": total, "cards": rows}

    # ------------------------------------------------------------------ the learning day
    def day_start(self, now: Optional[float] = None, hour: Optional[int] = None) -> float:
        now = time.time() if now is None else now
        hour = self.settings()["day_starts"] if hour is None else hour
        lt = time.localtime(now - hour * 3600)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) + hour * 3600

    def day_end(self, now: Optional[float] = None) -> float:
        return self.day_start(now) + fsrs.DAY

    # ------------------------------------------------------------------ review
    def _state(self, row: Dict[str, Any]) -> fsrs.CardState:
        return fsrs.CardState.from_dict(row)

    def preview(self, row: Dict[str, Any], now: float, lang: str = "ru", sched: Optional[fsrs.Scheduler] = None) -> List[Dict[str, Any]]:
        sched = sched or self.scheduler()
        ivl = sched.preview(self._state(row), now, row["id"])
        return [{"rating": r, "seconds": ivl[r], "label": fsrs.format_interval(ivl[r], lang)} for r in fsrs.RATINGS]

    def queue(self, cid: str = "", limit: int = 50, now: Optional[float] = None, lang: str = "ru") -> Dict[str, Any]:
        """Today's queue: learning cards due now, reviews due today, then new cards within today's allowance."""
        now = time.time() if now is None else now
        s = self.settings()
        day_end, day_start = self.day_end(now), self.day_start(now)
        scope, params = ("AND course_id=?", [cid]) if cid else ("", [])
        learning = self.q(f"SELECT * FROM cards WHERE suspended=0 AND state IN (1,3) AND due<=? {scope} ORDER BY due",
                          [now + 60 * 20] + params)
        reviews_done = self.one("SELECT COUNT(*) AS n FROM reviews WHERE at>=? AND state>=2", (day_start,))["n"]
        review_room = max(0, s["max_reviews"] - int(reviews_done))
        reviews = self.q(f"SELECT * FROM cards WHERE suspended=0 AND state=2 AND due<? {scope} ORDER BY due LIMIT ?",
                         [day_end] + params + [review_room])
        new_seen = self.one("SELECT COUNT(DISTINCT r.card_id) AS n FROM reviews r WHERE r.at>=? AND r.prev LIKE '%\"state\": 0%'",
                            (day_start,))["n"]
        new_room = max(0, s["new_per_day"] - int(new_seen))
        fresh = self.q(f"SELECT * FROM cards WHERE suspended=0 AND state=0 {scope} ORDER BY created, ord LIMIT ?",
                       params + [new_room])
        # Learning first only when already due; otherwise reviews → new → learning that comes due later.
        due_now = [c for c in learning if c["due"] <= now]
        later = [c for c in learning if c["due"] > now]
        ordered = due_now + reviews + fresh + later
        sched = self.scheduler()
        out = []
        for row in ordered[:max(1, min(int(limit), 500))]:
            row = dict(row)
            row["buttons"] = self.preview(row, now, lang, sched)
            out.append(row)
        counts = {"learning": len(learning), "review": len(reviews), "new": len(fresh)}
        counts["total"] = counts["learning"] + counts["review"] + counts["new"]
        return {"cards": out, "counts": counts, "now": now, "minutes": self.estimate_minutes(counts["total"])}

    def estimate_minutes(self, count: int) -> int:
        row = self.one("SELECT AVG(duration_ms) AS ms FROM (SELECT duration_ms FROM reviews WHERE duration_ms BETWEEN 1000 AND 120000"
                       " ORDER BY id DESC LIMIT 200)")
        per = (row["ms"] / 1000.0) if row and row["ms"] else float(self.settings()["seconds_per_card"])
        return int(round(count * per / 60.0)) if count else 0

    def answer(self, card_id: str, rating: int, *, now: Optional[float] = None, duration_ms: int = 0, device: str = "",
               lang: str = "ru") -> Dict[str, Any]:
        now = time.time() if now is None else now
        row = self.card(card_id)
        rating = int(rating)
        if rating not in fsrs.RATINGS:
            raise LearnError("Оценка — от 1 до 4", "The rating is 1..4")
        prev = self._state(row)
        new, interval = self.scheduler().review(prev, rating, now, card_id)
        with _LOCK:
            self._db.execute("UPDATE cards SET state=?, step=?, stability=?, difficulty=?, due=?, last_review=?, reps=?, lapses=?"
                             " WHERE id=?", (new.state, new.step, new.stability, new.difficulty, new.due, new.last_review,
                                              new.reps, new.lapses, card_id))
            self._db.execute("INSERT INTO reviews(card_id,course_id,rating,at,state,interval,duration_ms,device,prev)"
                             " VALUES(?,?,?,?,?,?,?,?,?)",
                             (card_id, row["course_id"], rating, now, prev.state, interval,
                              max(0, min(int(duration_ms or 0), 3600000)), clean(device, 40), json.dumps(prev.to_dict())))
            self._db.commit()
        out = self.card(card_id)
        out["interval"] = interval
        out["interval_label"] = fsrs.format_interval(interval, lang)
        return out

    def undo(self) -> Dict[str, Any]:
        last = self.one("SELECT * FROM reviews ORDER BY id DESC LIMIT 1")
        if not last or not last["prev"]:
            raise LearnError("Нечего отменять", "Nothing to undo", "not_found")
        prev = fsrs.CardState.from_dict(json.loads(last["prev"]))
        with _LOCK:
            self._db.execute("UPDATE cards SET state=?, step=?, stability=?, difficulty=?, due=?, last_review=?, reps=?, lapses=?"
                             " WHERE id=?", (prev.state, prev.step, prev.stability, prev.difficulty, prev.due, prev.last_review,
                                              prev.reps, prev.lapses, last["card_id"]))
            self._db.execute("DELETE FROM reviews WHERE id=?", (last["id"],))
            self._db.commit()
        return self.card(last["card_id"])

    # ------------------------------------------------------------------ overview and stats
    def course_counts(self, cid: str, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        row = self.one("SELECT COUNT(*) AS cards, SUM(CASE WHEN state=0 THEN 1 ELSE 0 END) AS new,"
                       " SUM(CASE WHEN state>0 AND due<? AND suspended=0 THEN 1 ELSE 0 END) AS due,"
                       " SUM(CASE WHEN state=2 AND stability>=21 THEN 1 ELSE 0 END) AS mature FROM cards WHERE course_id=?",
                       (self.day_end(now), cid)) or {}
        lessons = self.one("SELECT COUNT(*) AS n, SUM(done) AS done FROM nodes WHERE course_id=? AND kind='lesson'", (cid,)) or {}
        return {"cards": int(row.get("cards") or 0), "new": int(row.get("new") or 0), "due": int(row.get("due") or 0),
                "mature": int(row.get("mature") or 0), "lessons": int(lessons.get("n") or 0), "lessons_done": int(lessons.get("done") or 0)}

    def overview(self, lang: str = "ru") -> Dict[str, Any]:
        courses = []
        for c in self.q("SELECT * FROM courses ORDER BY updated DESC"):
            c.update(self.course_counts(c["id"]))
            courses.append(c)
        q = self.queue(limit=1, lang=lang)
        return {"courses": courses, "today": q["counts"], "minutes": q["minutes"], "streak": self.streak(),
                "reviewed_today": self.one("SELECT COUNT(*) AS n FROM reviews WHERE at>=?", (self.day_start(),))["n"],
                "settings": self.settings()}

    def streak(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        hour = self.settings()["day_starts"]
        days = {int(self.day_start(r["at"], hour)) for r in
                self.q("SELECT at FROM reviews WHERE at>=? ORDER BY at", (now - 400 * fsrs.DAY,))}
        day = int(self.day_start(now, hour))
        if day not in days:
            day = int(self.day_start(now - fsrs.DAY, hour))
        n = 0
        while day in days:
            n += 1
            day = int(self.day_start(day - 3600, hour))
        return n

    def stats(self, cid: str = "", days: int = 30, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        scope, params = ("AND course_id=?", [cid]) if cid else ("", [])
        start = self.day_start(now) - (days - 1) * fsrs.DAY
        reviews = self.q(f"SELECT rating, at, state FROM reviews WHERE at>=? {scope}", [start] + params)
        by_day = [0] * days
        hour = self.settings()["day_starts"]
        for r in reviews:
            idx = int((self.day_start(r["at"], hour) - start) // fsrs.DAY + 0.5)
            if 0 <= idx < days:
                by_day[idx] += 1
        mature = [r for r in reviews if r["state"] == fsrs.REVIEW]
        retention = (sum(1 for r in mature if r["rating"] > 1) / len(mature)) if mature else None
        forecast = [0] * 14
        today = self.day_start(now)
        for r in self.q(f"SELECT due FROM cards WHERE suspended=0 AND state>0 {scope}", params):
            idx = int(max(0.0, r["due"] - today) // fsrs.DAY)
            if idx < 14:
                forecast[idx] += 1
        states = {r["state"]: r["n"] for r in self.q(f"SELECT state, COUNT(*) AS n FROM cards WHERE 1=1 {scope} GROUP BY state", params)}
        return {"by_day": by_day, "forecast": forecast, "retention": retention, "reviews": len(reviews),
                "states": {"new": states.get(0, 0), "learning": states.get(1, 0) + states.get(3, 0), "review": states.get(2, 0)},
                "streak": self.streak(now)}

    # ------------------------------------------------------------------ exams
    def save_exam(self, cid: str, title: str, questions: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.course(cid)
        eid = new_id()
        self.run("INSERT INTO exams(id,course_id,title,questions,created) VALUES(?,?,?,?,?)",
                 (eid, cid, clean(title, 200), json.dumps(questions, ensure_ascii=False), time.time()))
        return self.exam(eid)

    def exam(self, eid: str) -> Dict[str, Any]:
        row = self.one("SELECT * FROM exams WHERE id=?", (eid,))
        if not row:
            raise LearnError("Экзамен не найден", "Exam not found", "not_found")
        for key in ("questions", "answers", "results"):
            try:
                row[key] = json.loads(row[key] or ("[]" if key == "questions" else "{}"))
            except ValueError:
                row[key] = [] if key == "questions" else {}
        return row

    def exams(self, cid: str) -> List[Dict[str, Any]]:
        return [{k: r[k] for k in ("id", "title", "score", "created", "finished")}
                for r in self.q("SELECT id,title,score,created,finished FROM exams WHERE course_id=? ORDER BY created DESC LIMIT 30", (cid,))]

    def save_result(self, eid: str, qid: str, answer: str, result: Dict[str, Any]) -> Dict[str, Any]:
        exam = self.exam(eid)
        exam["answers"][qid] = clean(answer, 20000)
        exam["results"][qid] = result
        scored = [float(r.get("score") or 0) for r in exam["results"].values() if isinstance(r, dict)]
        score = round(sum(scored) / len(exam["questions"]), 1) if exam["questions"] else None
        finished = time.time() if len(exam["results"]) >= len(exam["questions"]) else None
        self.run("UPDATE exams SET answers=?, results=?, score=?, finished=? WHERE id=?",
                 (json.dumps(exam["answers"], ensure_ascii=False), json.dumps(exam["results"], ensure_ascii=False),
                  score, finished, eid))
        return self.exam(eid)


def get_store(root: Optional[Path] = None) -> Store:
    path = str(root or data_root())
    with _LOCK:
        store = _STORES.get(path)
        if store is None:
            store = _STORES[path] = Store(Path(path))
        return store


def reset_stores() -> None:
    """Tests: forget cached connections (a new MEMUAI_DATA_DIR)."""
    with _LOCK:
        for store in _STORES.values():
            store.close()
        _STORES.clear()
