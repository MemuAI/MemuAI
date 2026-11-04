"""Контекст агентной рабочей области: журнал разговора, эпизоды, сюжет и поиск по прошлому.

Глобальный чат компаньона живёт месяцами, поэтому модели нельзя отправлять всю историю.
Окно MemuAI держит в запросе только «окно» последних сообщений (по бюджету токенов, со
ступенчатым началом — префикс запроса остаётся неизменным много ходов подряд и попадает в
кэш провайдера), а всё, что ушло за окно, хранится здесь:

* ``msgs`` — текст каждого сообщения (SQLite ``thread.db`` в папке области) и индекс FTS5 по
  нормализованным основам слов (рус/англ): «купила», «покупку», «покупали» находят друг друга;
* ``episodes`` — сжатые моделью-суммаризатором куски ушедшей истории (L1, ~100–200 токенов);
* ``story`` — «сюжет»: старые эпизоды, свёрнутые в одну сводку (L2) с бюджетом токенов;
* ``vecs`` — необязательные эмбеддинги (модель из Proxy) для смыслового поиска; итог поиска —
  слияние BM25 и косинусной близости (reciprocal rank fusion) с лёгким бонусом за свежесть.

Сжатие идёт в фоне после ``ingest`` и никогда не задерживает ответ: пока эпизод не готов,
``context`` закрывает разрыв выжимкой из самих сообщений («мост»), поэтому информация не теряется.
"""

import array
import asyncio
import heapq
import json
import logging
import math
import re
import sqlite3
import threading
import time
from functools import lru_cache
from operator import itemgetter, mul
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence

log = logging.getLogger("memuai.space_context")

@lru_cache(maxsize=1)
def _vector_numpy():
    # Import only for large semantic searches. Importing NumPy reserves native memory even when no model is configured.
    try:
        import numpy
        return numpy
    except Exception:  # optional accelerator
        return None


VECTOR_ACCELERATE_AT = 2_000_000  # scalar products in the inner loop
VECTOR_BATCH_ROWS = 256
# F23: rows scanned by the BM25 fallback (no FTS5) before scoring stops.
# The old code did one fetchall() of up to 20 000 full normalized texts
# (tens-hundreds of MB) exactly when the DB was already degraded.
_BM25_FALLBACK_MAX_ROWS = 2000

ECONOMY_BUDGETS: Dict[str, Dict[str, int]] = {
    # window = tokens of verbatim recent history; story/episodes/recall = injected summaries
    "max": {"window": 2400, "story": 450, "episodes": 520, "recall_hits": 3, "recall_tokens": 360, "clip": 700},
    "balanced": {"window": 6500, "story": 900, "episodes": 1100, "recall_hits": 5, "recall_tokens": 760, "clip": 1600},
    "rich": {"window": 16000, "story": 1600, "episodes": 2400, "recall_hits": 8, "recall_tokens": 1500, "clip": 4000},
}
EP_MIN_MESSAGES = 8          # an episode is made from at least this many messages…
EP_MAX_MESSAGES = 40         # …and at most this many
EP_MAX_TOKENS = 6000         # input of one summarisation call
EP_UNROLLED_LIMIT = 6        # more unrolled episodes than this → roll the oldest into the story
TEXT_LIMIT = 60_000          # stored characters per message
SNIPPET = 320

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:['’][A-Za-z]+)?")
_CYR_RE = re.compile(r"[а-яё]")
_RU_REFLEXIVE = ("ся", "сь")
_RU_SUFFIXES = tuple(sorted({
    "иями", "ями", "ами", "иях", "ях", "ах", "ией", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие",
    "ого", "его", "ому", "ему", "ыми", "ими", "ую", "юю", "ом", "ем", "ам", "ям", "ов", "ев", "ей", "ешь",
    "ишь", "ете", "ите", "ет", "ит", "ут", "ют", "ат", "ят", "им", "ала", "ила", "ыла", "ела", "али",
    "или", "ыли", "ели", "ал", "ил", "ыл", "ел", "ать", "ять", "ить", "еть", "уть", "ть", "ла", "ли",
    "ло", "ость", "ости", "остью", "ение", "ения", "ений", "ению", "ением", "ании", "ание", "ания",
    "ство", "ства", "ству", "ством", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
}, key=len, reverse=True))
_EN_SUFFIXES = ("ingly", "edly", "ations", "ation", "ments", "ment", "ness", "ing", "ies", "ied", "ed", "es", "ly", "s")
STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж
вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без
будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один
почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над
больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда
лучше чуть том нельзя такой им более всегда конечно всю между это мне меня привет пожалуйста спасибо
the a an and or but if then of to in on at by for with from is are was were be been am it its this that
these those i you he she we they me my your our their do does did not no yes so as just about what which
who how when where why can could would should will shall may might hi hello thanks please ok okay
""".split())


def now_ms() -> int:
    return int(time.time() * 1000)


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if len(w) <= 3:
        return w
    if _CYR_RE.search(w):
        for suffix in _RU_REFLEXIVE:
            if w.endswith(suffix) and len(w) - 2 >= 4:
                w = w[:-2]
                break
        for suffix in _RU_SUFFIXES:
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[: -len(suffix)]
        return w
    for suffix in _EN_SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            base = w[: -len(suffix)]
            return base + "y" if suffix in ("ies", "ied") else base
    return w


def words(text: str) -> List[str]:
    return [m.group(0) for m in _WORD_RE.finditer(str(text or ""))]


def normalize(text: str) -> str:
    """Index form: lower-cased stems separated by spaces (stop words kept for phrase-like recall)."""
    return " ".join(stem(w) for w in words(text)[:6000])


def query_terms(text: str, limit: int = 16) -> List[str]:
    out: List[str] = []
    for w in words(text):
        lw = w.lower().replace("ё", "е")
        if lw in STOPWORDS or len(lw) < 2:
            continue
        s = stem(lw)
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        from .token_estimate import estimate
        return estimate(text)
    except Exception:  # pragma: no cover
        return max(1, len(text) // 3)


def clean_text(text: Any) -> str:
    """Message text without hidden protocol blocks, reasoning or giant data URLs."""
    value = str(text or "")
    value = re.sub(r"<(think|thinking|reasoning)\b[^>]*>[\s\S]*?</\1>", "", value, flags=re.I)
    value = re.sub(r"```[ \t]*memuai[-_a-z]*[\s\S]*?```", "", value, flags=re.I)
    value = re.sub(r"\[(?:TITLE|НАЗВАНИЕ|MEM|МЕМ):[^\]\n]*\]", "", value, flags=re.I)
    value = re.sub(r"data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]{64,}", "[data]", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()[:TEXT_LIMIT]


def first_sentence(text: str, limit: int = 160) -> str:
    flat = re.sub(r"\s+", " ", re.sub(r"```[\s\S]*?```", " [код] ", text or "")).strip()
    match = re.match(r"(.{20,}?[.!?…])(\s|$)", flat)
    out = match.group(1) if match else flat
    return out[:limit].rstrip() + ("…" if len(out) > limit else "")


def _fmt_day(ts: int) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(ts / 1000))


def _fmt_time(ts: int) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts / 1000))


def _pack(vector: Sequence[float]) -> bytes:
    norm = math.sqrt(sum(float(x) * float(x) for x in vector)) or 1.0
    return array.array("f", (float(x) / norm for x in vector)).tobytes()


def _unpack(blob: bytes) -> array.array:
    out = array.array("f")
    out.frombytes(blob)
    return out


class Thread:
    """One space's conversation log (thread-safe, one SQLite connection)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.fts = True
        with self.lock:
            c = self.conn
            try:
                c.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS msgs(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id TEXT NOT NULL, mid TEXT NOT NULL, seq INTEGER NOT NULL,
                  role TEXT NOT NULL, ts INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'chat',
                  text TEXT NOT NULL DEFAULT '', norm TEXT NOT NULL DEFAULT '', tokens INTEGER NOT NULL DEFAULT 0,
                  UNIQUE(chat_id, mid));
                CREATE INDEX IF NOT EXISTS msgs_seq ON msgs(chat_id, seq);
                CREATE TABLE IF NOT EXISTS episodes(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
                  seq_from INTEGER NOT NULL, seq_to INTEGER NOT NULL, ts_from INTEGER, ts_to INTEGER,
                  text TEXT NOT NULL, norm TEXT NOT NULL DEFAULT '', tokens INTEGER NOT NULL DEFAULT 0,
                  rolled INTEGER NOT NULL DEFAULT 0, model TEXT DEFAULT '', created INTEGER);
                CREATE INDEX IF NOT EXISTS episodes_seq ON episodes(chat_id, seq_from);
                CREATE TABLE IF NOT EXISTS story(
                  chat_id TEXT PRIMARY KEY, text TEXT NOT NULL, through_seq INTEGER NOT NULL DEFAULT 0,
                  tokens INTEGER NOT NULL DEFAULT 0, updated INTEGER, model TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS vecs(
                  kind TEXT NOT NULL, ref INTEGER NOT NULL, model TEXT NOT NULL, chat_id TEXT NOT NULL,
                  vec BLOB NOT NULL, PRIMARY KEY(kind, ref, model));
                CREATE INDEX IF NOT EXISTS vecs_chat ON vecs(chat_id, model);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS archive(
                  chat_id TEXT NOT NULL, mid TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
                  PRIMARY KEY(chat_id, mid));
                CREATE INDEX IF NOT EXISTS archive_seq ON archive(chat_id, seq);
            """)
            try:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS msgs_fts USING fts5("
                          "norm, content='msgs', content_rowid='id', tokenize='unicode61 remove_diacritics 2')")
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5("
                          "norm, content='episodes', content_rowid='id', tokenize='unicode61 remove_diacritics 2')")
            except sqlite3.OperationalError:
                self.fts = False
            c.commit()

    # ── writing ──────────────────────────────────────────────────────────────────────────────
    def _fts_put(self, table: str, rowid: int, norm: str, old_norm: Optional[str] = None) -> None:
        if not self.fts:
            return
        if old_norm is not None:
            self.conn.execute(f"INSERT INTO {table}_fts({table}_fts, rowid, norm) VALUES('delete', ?, ?)",
                              (rowid, old_norm))
        self.conn.execute(f"INSERT INTO {table}_fts(rowid, norm) VALUES(?, ?)", (rowid, norm))

    def ingest(self, chat_id: str, rows: Iterable[Dict[str, Any]], deleted: Iterable[str] = (),
               edited: Iterable[str] = ()) -> Dict[str, Any]:
        stored = 0
        edited = set(edited)
        with self.lock:
            c = self.conn
            covered = self.covered_through(chat_id)
            stale_summary = False
            for mid in deleted:
                old = c.execute("SELECT id, norm, seq FROM msgs WHERE chat_id=? AND mid=?", (chat_id, str(mid))).fetchone()
                if old:
                    stale_summary |= old["seq"] <= covered
                    if self.fts:
                        c.execute("INSERT INTO msgs_fts(msgs_fts, rowid, norm) VALUES('delete', ?, ?)", (old["id"], old["norm"]))
                    c.execute("DELETE FROM vecs WHERE kind='m' AND ref=?", (old["id"],))
                    c.execute("DELETE FROM msgs WHERE id=?", (old["id"],))
            for row in rows:
                mid = str(row.get("id") or "")[:80]
                role = str(row.get("role") or "")
                if not mid or role not in ("user", "assistant"):
                    continue
                try:
                    seq = int(row.get("seq"))
                except (TypeError, ValueError):
                    continue
                text = clean_text(row.get("text"))
                ts = int(row.get("ts") or 0) if str(row.get("ts") or "0").lstrip("-").isdigit() else 0
                mode = str(row.get("mode") or "chat")[:12]
                norm_text = normalize(text)
                old = c.execute("SELECT id, text, norm, seq FROM msgs WHERE chat_id=? AND mid=?", (chat_id, mid)).fetchone()
                if old:
                    if old["text"] == text and old["seq"] == seq:
                        continue
                    c.execute("UPDATE msgs SET seq=?, role=?, ts=?, mode=?, text=?, norm=?, tokens=? WHERE id=?",
                              (seq, role, ts, mode, text, norm_text, estimate_tokens(text), old["id"]))
                    self._fts_put("msgs", old["id"], norm_text, old["norm"])
                    c.execute("DELETE FROM vecs WHERE kind='m' AND ref=?", (old["id"],))
                else:
                    cur = c.execute("INSERT INTO msgs(chat_id, mid, seq, role, ts, mode, text, norm, tokens) "
                                    "VALUES(?,?,?,?,?,?,?,?,?)",
                                    (chat_id, mid, seq, role, ts, mode, text, norm_text, estimate_tokens(text)))
                    self._fts_put("msgs", int(cur.lastrowid), norm_text)
                if mid in edited:
                    stale_summary |= min(seq, old["seq"] if old else seq) <= covered
                stored += 1
            if stale_summary:
                self.reset_summaries(chat_id)
            else:
                c.commit()
            top = c.execute("SELECT MAX(seq) AS s, COUNT(*) AS n FROM msgs WHERE chat_id=?", (chat_id,)).fetchone()
        return {"stored": stored, "max_seq": top["s"] if top["s"] is not None else -1, "count": top["n"]}

    def forget_chat(self, chat_id: str) -> None:
        with self.lock:
            c = self.conn
            if self.fts:
                for row in c.execute("SELECT id, norm FROM msgs WHERE chat_id=?", (chat_id,)).fetchall():
                    c.execute("INSERT INTO msgs_fts(msgs_fts, rowid, norm) VALUES('delete', ?, ?)", (row["id"], row["norm"]))
                for row in c.execute("SELECT id, norm FROM episodes WHERE chat_id=?", (chat_id,)).fetchall():
                    c.execute("INSERT INTO episodes_fts(episodes_fts, rowid, norm) VALUES('delete', ?, ?)", (row["id"], row["norm"]))
            c.execute("DELETE FROM vecs WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM msgs WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM episodes WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM story WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM archive WHERE chat_id=?", (chat_id,))
            c.commit()

    # ── archive: full message objects moved out of a very long chat ─────────────────────────
    def archive_put(self, chat_id: str, items: Iterable[Dict[str, Any]]) -> int:
        """Store complete message objects (attachments included) that the window removed from the chat file."""
        stored = 0
        with self.lock:
            c = self.conn
            for item in items:
                mid = str(item.get("id") or "")[:80]
                message = item.get("message")
                try:
                    seq = int(item.get("seq"))
                except (TypeError, ValueError):
                    continue
                if not mid or not isinstance(message, dict):
                    continue
                c.execute("INSERT OR REPLACE INTO archive(chat_id, mid, seq, data) VALUES(?,?,?,?)",
                          (chat_id, mid, seq, json.dumps(message, ensure_ascii=False)))
                stored += 1
            c.commit()
        return stored

    def archive_get(self, chat_id: str, before_seq: Optional[int] = None, limit: int = 40) -> List[Dict[str, Any]]:
        """Archived messages before ``before_seq`` in chronological order (the newest ``limit`` of them)."""
        limit = max(1, min(200, int(limit)))
        with self.lock:
            if before_seq is None:
                rows = self.conn.execute("SELECT seq, data FROM archive WHERE chat_id=? ORDER BY seq DESC LIMIT ?",
                                         (chat_id, limit)).fetchall()
            else:
                rows = self.conn.execute("SELECT seq, data FROM archive WHERE chat_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                                         (chat_id, int(before_seq), limit)).fetchall()
        out = []
        for row in reversed(rows):
            try:
                message = json.loads(row["data"])
            except ValueError:
                continue
            if isinstance(message, dict):
                message["seq"] = int(row["seq"])
                out.append(message)
        return out

    def archive_count(self, chat_id: str) -> int:
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM archive WHERE chat_id=?", (chat_id,)).fetchone()
        return int(row["n"] or 0)

    def reset_summaries(self, chat_id: str) -> None:
        with self.lock:
            c = self.conn
            if self.fts:
                for row in c.execute("SELECT id, norm FROM episodes WHERE chat_id=?", (chat_id,)).fetchall():
                    c.execute("INSERT INTO episodes_fts(episodes_fts, rowid, norm) VALUES('delete', ?, ?)", (row["id"], row["norm"]))
            c.execute("DELETE FROM vecs WHERE kind='e' AND chat_id=?", (chat_id,))
            c.execute("DELETE FROM episodes WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM story WHERE chat_id=?", (chat_id,))
            c.commit()

    def add_episode(self, chat_id: str, seq_from: int, seq_to: int, ts_from: int, ts_to: int,
                    text: str, model: str = "") -> int:
        text = text.strip()[:4000]
        norm_text = normalize(text)
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO episodes(chat_id, seq_from, seq_to, ts_from, ts_to, text, norm, tokens, model, created) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (chat_id, seq_from, seq_to, ts_from, ts_to, text, norm_text, estimate_tokens(text), model, now_ms()))
            self._fts_put("episodes", int(cur.lastrowid), norm_text)
            self.conn.commit()
            return int(cur.lastrowid)

    def set_story(self, chat_id: str, text: str, through_seq: int, rolled_ids: Sequence[int], model: str = "") -> None:
        text = text.strip()[:12000]
        with self.lock:
            self.conn.execute("INSERT INTO story(chat_id, text, through_seq, tokens, updated, model) VALUES(?,?,?,?,?,?) "
                              "ON CONFLICT(chat_id) DO UPDATE SET text=excluded.text, through_seq=excluded.through_seq, "
                              "tokens=excluded.tokens, updated=excluded.updated, model=excluded.model",
                              (chat_id, text, through_seq, estimate_tokens(text), now_ms(), model))
            if rolled_ids:
                self.conn.executemany("UPDATE episodes SET rolled=1 WHERE id=?", [(int(i),) for i in rolled_ids])
            self.conn.commit()

    # ── reading ──────────────────────────────────────────────────────────────────────────────
    def messages(self, chat_id: str, before_seq: Optional[int] = None, limit: int = 50,
                 after_seq: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(400, int(limit)))
        with self.lock:
            if after_seq is not None:
                rows = self.conn.execute("SELECT mid, seq, role, ts, mode, text FROM msgs WHERE chat_id=? AND seq>? "
                                         "ORDER BY seq ASC LIMIT ?", (chat_id, int(after_seq), limit)).fetchall()
                return [dict(r) for r in rows]
            if before_seq is None:
                rows = self.conn.execute("SELECT mid, seq, role, ts, mode, text FROM msgs WHERE chat_id=? "
                                         "ORDER BY seq DESC LIMIT ?", (chat_id, limit)).fetchall()
            else:
                rows = self.conn.execute("SELECT mid, seq, role, ts, mode, text FROM msgs WHERE chat_id=? AND seq<? "
                                         "ORDER BY seq DESC LIMIT ?", (chat_id, int(before_seq), limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def story(self, chat_id: str) -> Dict[str, Any]:
        with self.lock:
            row = self.conn.execute("SELECT text, through_seq, tokens, updated, model FROM story WHERE chat_id=?",
                                    (chat_id,)).fetchone()
        return dict(row) if row else {"text": "", "through_seq": -1, "tokens": 0, "updated": 0, "model": ""}

    def episodes(self, chat_id: str, *, rolled: Optional[bool] = None) -> List[Dict[str, Any]]:
        sql = "SELECT id, seq_from, seq_to, ts_from, ts_to, text, tokens, rolled FROM episodes WHERE chat_id=?"
        args: List[Any] = [chat_id]
        if rolled is not None:
            sql += " AND rolled=?"
            args.append(1 if rolled else 0)
        with self.lock:
            rows = self.conn.execute(sql + " ORDER BY seq_from ASC", args).fetchall()
        return [dict(r) for r in rows]

    def covered_through(self, chat_id: str) -> int:
        with self.lock:
            row = self.conn.execute("SELECT MAX(seq_to) AS s FROM episodes WHERE chat_id=?", (chat_id,)).fetchone()
        return int(row["s"]) if row and row["s"] is not None else -1

    def range(self, chat_id: str, seq_from: int, seq_to: int, limit: int = 400) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT id, mid, seq, role, ts, mode, text, tokens FROM msgs WHERE chat_id=? "
                                     "AND seq>=? AND seq<=? ORDER BY seq ASC LIMIT ?",
                                     (chat_id, int(seq_from), int(seq_to), int(limit))).fetchall()
        return [dict(r) for r in rows]

    def stats(self, chat_id: str = "") -> Dict[str, Any]:
        where, args = ("WHERE chat_id=?", (chat_id,)) if chat_id else ("", ())
        with self.lock:
            m = self.conn.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(tokens),0) AS t, MAX(seq) AS s FROM msgs {where}", args).fetchone()
            e = self.conn.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(tokens),0) AS t FROM episodes {where}", args).fetchone()
            v = self.conn.execute(f"SELECT COUNT(*) AS n FROM vecs {where}", args).fetchone()
            a = self.conn.execute(f"SELECT COUNT(*) AS n FROM archive {where}", args).fetchone()
        out = {"messages": m["n"], "message_tokens": m["t"], "max_seq": m["s"] if m["s"] is not None else -1,
               "episodes": e["n"], "episode_tokens": e["t"], "vectors": v["n"], "archived": a["n"], "fts": self.fts}
        if chat_id:
            out["story_tokens"] = self.story(chat_id).get("tokens", 0)
            out["covered_through"] = self.covered_through(chat_id)
        return out

    # ── search ───────────────────────────────────────────────────────────────────────────────
    def _bm25(self, table: str, chat_id: str, terms: List[str], limit: int,
              before_seq: Optional[int]) -> List[tuple]:
        if not terms or limit <= 0:
            return []
        seq_col = "seq" if table == "msgs" else "seq_to"
        filters = []
        args: List[Any] = []
        if chat_id:
            filters.append("t.chat_id=?")
            args.append(chat_id)
        if before_seq is not None:
            filters.append(f"t.{seq_col}<?")
            args.append(int(before_seq))
        where = (" AND " + " AND ".join(filters)) if filters else ""
        with self.lock:
            if self.fts:
                match = " OR ".join('"' + t.replace('"', "") + '"*' for t in terms)
                try:
                    rows = self.conn.execute(
                        f"SELECT t.id AS id, bm25({table}_fts) AS score FROM {table}_fts JOIN {table} t ON t.id={table}_fts.rowid "
                        f"WHERE {table}_fts MATCH ?{where} ORDER BY score LIMIT ?",
                        [match, *args, limit]).fetchall()
                    return [(int(r["id"]), -float(r["score"])) for r in rows]
                except sqlite3.OperationalError:
                    pass
            # fallback: scan the most recent rows and score term overlap.
            # F23: stream in batches with a bounded top-k heap instead of one
            # fetchall() — memory stays independent of the history length.
            cursor = self.conn.execute(
                f"SELECT t.id AS id, t.norm AS norm FROM {table} t WHERE 1=1{where} "
                f"ORDER BY t.id DESC LIMIT {_BM25_FALLBACK_MAX_ROWS}",
                args)
            heap: List[tuple] = []
            while rows := cursor.fetchmany(512):
                for r in rows:
                    toks = r["norm"].split()
                    if not toks:
                        continue
                    hits = sum(1 for term in terms if any(tok.startswith(term) for tok in toks))
                    if not hits:
                        continue
                    item = (hits / math.log(3 + len(toks)), int(r["id"]))
                    if len(heap) < limit:
                        heapq.heappush(heap, item)
                    elif item > heap[0]:
                        heapq.heapreplace(heap, item)
            return sorted(((ref, score) for score, ref in heap),
                          key=lambda x: -x[1])[:limit]

    def _vector_rank(self, kind: str, chat_id: str, model: str, query_vec: Sequence[float], limit: int,
                     before_seq: Optional[int] = None) -> List[tuple]:
        q = _unpack(_pack(query_vec))
        if not q or limit <= 0:
            return []
        source = "FROM vecs AS v"
        if before_seq is not None:
            table, seq = ("msgs", "seq") if kind == "m" else ("episodes", "seq_to")
            source += f" JOIN {table} AS t ON t.id=v.ref AND t.{seq}<?"
        source += " WHERE v.kind=? AND v.model=?"
        params = (before_seq, kind, model) if before_seq is not None else (kind, model)
        if chat_id:
            source += " AND v.chat_id=?"
            params += (chat_id,)
        byte_count = q.itemsize * len(q)
        with self.lock:
            count = self.conn.execute("SELECT COUNT(*) " + source, params).fetchone()[0]
            np = _vector_numpy() if count * len(q) >= VECTOR_ACCELERATE_AT else None
            cursor = self.conn.execute("SELECT v.ref, v.vec " + source, params)
            # A bounded batch and top-k heap keep the working memory independent of the history length.
            def scores():
                query = np.asarray(q, dtype=np.float32) if np is not None else q
                while rows := cursor.fetchmany(VECTOR_BATCH_ROWS):
                    matching = [(row["ref"], row["vec"]) for row in rows if len(row["vec"]) == byte_count]
                    if np is not None and matching:
                        refs, blobs = zip(*matching)
                        matrix = np.vstack([np.frombuffer(blob, dtype=np.float32) for blob in blobs])
                        yield from zip(refs, map(float, matrix @ query))
                    else:
                        for ref, blob in matching:
                            yield ref, sum(map(mul, _unpack(blob), q))
            return heapq.nlargest(limit, scores(), key=itemgetter(1))

    def search(self, chat_id: str, query: str, *, limit: int = 8, before_seq: Optional[int] = None,
               query_vec: Optional[Sequence[float]] = None, vec_model: str = "",
               include_episodes: bool = True) -> List[Dict[str, Any]]:
        terms = query_terms(query)
        pool = max(limit * 4, 24)
        fused: Dict[tuple, float] = {}
        meta: Dict[tuple, Dict[str, Any]] = {}

        def add(kind: str, ranked: List[tuple], weight: float) -> None:
            for rank, (ref, _score) in enumerate(ranked):
                key = (kind, ref)
                fused[key] = fused.get(key, 0.0) + weight / (60 + rank)

        add("m", self._bm25("msgs", chat_id, terms, pool, before_seq), 1.0)
        if include_episodes:
            add("e", self._bm25("episodes", chat_id, terms, pool // 2, before_seq), 0.9)
        if query_vec is not None and vec_model:
            add("m", self._vector_rank("m", chat_id, vec_model, query_vec, pool, before_seq), 1.1)
            if include_episodes:
                add("e", self._vector_rank("e", chat_id, vec_model, query_vec, pool // 2, before_seq), 1.0)
        if not fused:
            return []
        msg_ids = [ref for kind, ref in fused if kind == "m"]
        ep_ids = [ref for kind, ref in fused if kind == "e"]
        with self.lock:
            if msg_ids:
                q = ",".join("?" * len(msg_ids))
                for r in self.conn.execute(f"SELECT id, chat_id, mid, seq, role, ts, mode, text FROM msgs WHERE id IN ({q})", msg_ids):
                    meta[("m", int(r["id"]))] = dict(r)
            if ep_ids:
                q = ",".join("?" * len(ep_ids))
                for r in self.conn.execute(f"SELECT id, chat_id, seq_from, seq_to, ts_from, ts_to, text FROM episodes WHERE id IN ({q})", ep_ids):
                    meta[("e", int(r["id"]))] = dict(r)
        newest = max((m.get("ts") or m.get("ts_to") or 0) for m in meta.values()) if meta else 0
        hits = []
        for key, score in fused.items():
            row = meta.get(key)
            if not row:
                continue
            if before_seq is not None and row["seq" if key[0] == "m" else "seq_to"] >= before_seq:
                continue
            ts = row.get("ts") or row.get("ts_to") or 0
            age_days = max(0.0, (newest - ts) / 86_400_000) if newest and ts else 0.0
            score *= 1.0 + 0.15 / (1.0 + age_days / 30.0)
            text = row["text"]
            hit = {"kind": "message" if key[0] == "m" else "episode", "chat_id": row["chat_id"], "score": round(score, 6),
                   "snippet": _snippet(text, terms), "ts": ts}
            if key[0] == "m":
                hit.update({"mid": row["mid"], "seq": row["seq"], "role": row["role"], "mode": row["mode"]})
            else:
                hit.update({"seq": row["seq_from"], "seq_to": row["seq_to"], "ts_from": row.get("ts_from") or 0})
            hits.append(hit)
        hits.sort(key=lambda h: -h["score"])
        return hits[:limit]

    # ── embeddings bookkeeping ───────────────────────────────────────────────────────────────
    def missing_vectors(self, chat_id: str, model: str, limit: int = 64) -> List[Dict[str, Any]]:
        with self.lock:
            msgs = self.conn.execute(
                "SELECT m.id AS ref, 'm' AS kind, m.text AS text FROM msgs m LEFT JOIN vecs v "
                "ON v.kind='m' AND v.ref=m.id AND v.model=? WHERE m.chat_id=? AND v.ref IS NULL "
                "AND length(m.text) >= 12 ORDER BY m.seq DESC LIMIT ?", (model, chat_id, limit)).fetchall()
            eps = self.conn.execute(
                "SELECT e.id AS ref, 'e' AS kind, e.text AS text FROM episodes e LEFT JOIN vecs v "
                "ON v.kind='e' AND v.ref=e.id AND v.model=? WHERE e.chat_id=? AND v.ref IS NULL "
                "ORDER BY e.id DESC LIMIT ?", (model, chat_id, max(4, limit // 4))).fetchall()
        return [dict(r) for r in list(eps) + list(msgs)]

    def put_vectors(self, chat_id: str, model: str, items: Sequence[Dict[str, Any]], vectors: Sequence[Sequence[float]]) -> int:
        n = 0
        with self.lock:
            for item, vec in zip(items, vectors):
                if not vec:
                    continue
                self.conn.execute("INSERT OR REPLACE INTO vecs(kind, ref, model, chat_id, vec) VALUES(?,?,?,?,?)",
                                  (item["kind"], int(item["ref"]), model, chat_id, _pack(vec)))
                n += 1
            self.conn.commit()
        return n

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.close()
            except Exception:
                pass


def _snippet(text: str, terms: List[str], size: int = SNIPPET) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= size:
        return flat
    low = flat.lower().replace("ё", "е")
    pos = -1
    for term in terms:
        pos = low.find(term)
        if pos >= 0:
            break
    if pos < 0:
        return flat[:size].rstrip() + "…"
    start = max(0, pos - size // 3)
    end = min(len(flat), start + size)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


# ── registry of open threads ─────────────────────────────────────────────────────────────────
_THREADS: Dict[str, Thread] = {}
_THREADS_LOCK = threading.Lock()


def thread_at(path: Path) -> Thread:
    key = str(Path(path).resolve())
    with _THREADS_LOCK:
        thread = _THREADS.get(key)
        if thread is None:
            thread = Thread(Path(path))
            _THREADS[key] = thread
        return thread


def thread_for(space_id: Optional[str] = None, profile_id: Optional[str] = None) -> Thread:
    from . import spaces
    with spaces._LOCK:
        if space_id and spaces.get_space(space_id, profile_id) is None:
            raise KeyError(space_id)
        return thread_at(spaces.space_dir(space_id, profile_id) / "thread.db")


def close_thread(path: Path) -> None:
    with _THREADS_LOCK:
        thread = _THREADS.pop(str(path.resolve()), None)
        if thread is not None:
            thread.close()


def close_all() -> None:
    with _THREADS_LOCK:
        for thread in _THREADS.values():
            thread.close()
        _THREADS.clear()


# ── summarisation ────────────────────────────────────────────────────────────────────────────
Completion = Callable[..., Awaitable[str]]


def _lang_of(rows: Sequence[Dict[str, Any]]) -> str:
    sample = " ".join(str(r.get("text") or "")[:200] for r in rows[-12:])
    cyr = len(re.findall(r"[А-Яа-яЁё]", sample))
    lat = len(re.findall(r"[A-Za-z]", sample))
    return "ru" if cyr >= lat * 0.6 else "en"


def _transcript(rows: Sequence[Dict[str, Any]], names: Dict[str, str], per_message: int = 1800) -> str:
    lines = []
    for r in rows:
        text = str(r.get("text") or "")
        text = re.sub(r"```[\s\S]{400,}?```", "```…код…```", text)
        if len(text) > per_message:
            text = text[: per_message * 2 // 3] + " … " + text[-per_message // 3:]
        mode = f" [{r.get('mode')}]" if r.get("mode") and r.get("mode") != "chat" else ""
        lines.append(f"[{_fmt_time(int(r.get('ts') or 0))}] {names.get(r['role'], r['role'])}{mode}: {text}")
    return "\n".join(lines)


EPISODE_PROMPT = (
    "You maintain the long-term memory of an AI companion. Compress the conversation slice below into ONE memory "
    "episode the companion will read later instead of the raw messages.\n"
    "Write in the conversation's language ({lang}). Output 3–8 terse lines, at most ~140 words, no preamble:\n"
    "- what was discussed or done and the outcome (decisions, results, files/projects touched in Work/Code mode);\n"
    "- facts about the user's life, people, plans, tastes and feelings that were expressed;\n"
    "- promises, unanswered questions and plans to follow up — prefix each such line with \"{open}\".\n"
    "Keep names, numbers and dates exact. Skip greetings and small talk unless it shows the user's mood. "
    "Never invent anything."
)
STORY_PROMPT = (
    "You maintain the long-term memory of an AI companion. Merge the existing STORY with the NEW EPISODES into an "
    "updated story of the whole relationship and conversation so far. Write in {lang}. At most {words} words. "
    "Organise chronologically by period (dates in brackets), keep durable facts about the user, important events, "
    "running jokes and ongoing threads; keep lines prefixed with \"{open}\" only while they are still unresolved. "
    "Drop small talk and anything superseded. Output only the story."
)


def extractive(rows: Sequence[Dict[str, Any]], names: Dict[str, str], budget_tokens: int) -> str:
    """Model-free fallback: one short line per message, newest first until the budget, then restored to time order."""
    out: List[str] = []
    used = 0
    for r in reversed(rows):
        text = first_sentence(str(r.get("text") or ""), 150)
        if not text:
            continue
        line = f"[{_fmt_day(int(r.get('ts') or 0))}] {names.get(r['role'], r['role'])}: {text}"
        cost = estimate_tokens(line)
        if used + cost > budget_tokens:
            break
        out.append(line)
        used += cost
    return "\n".join(reversed(out))


# F21: a single hung summary/embedding call must not wedge the maintainer
# job for its chat forever; 120 s is an idle/total bound per call, far above
# any legitimate summary/embedding latency.
_MAINTAINER_CALL_TIMEOUT = 120
_MAINTAINER_MAX_ERRORS = 100


class Maintainer:
    """Background summarisation + embeddings, one job per (thread, chat) at a time."""

    def __init__(self, completion: Optional[Completion], embed: Optional[Callable[[str, List[str]], Awaitable[List[List[float]]]]] = None):
        self.completion = completion
        self.embed = embed
        self.running: Dict[str, asyncio.Task] = {}
        self.errors: Dict[str, str] = {}

    def cancel(self, thread: Thread, chat_id: str) -> None:
        task = self.running.pop(f"{thread.path}|{chat_id}", None)
        if task and not task.done():
            task.cancel()

    def kick(self, thread: Thread, chat_id: str, *, window_start_seq: int, model: str, settings: Dict[str, Any],
             names: Dict[str, str]) -> bool:
        key = f"{thread.path}|{chat_id}"
        task = self.running.get(key)
        if task and not task.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self.running[key] = loop.create_task(self._run(key, thread, chat_id, window_start_seq, model, settings, names))
        return True

    async def _run(self, key: str, thread: Thread, chat_id: str, window_start_seq: int, model: str,
                   settings: Dict[str, Any], names: Dict[str, str]) -> None:
        try:
            await self.summarize(thread, chat_id, window_start_seq, model, settings, names)
            emb_model = str(settings.get("embedding_model") or "")
            if emb_model and self.embed:
                await self.embed_missing(thread, chat_id, emb_model)
            self.errors.pop(key, None)
        except Exception as exc:  # keep the chat working whatever the summariser does
            self.errors[key] = f"{type(exc).__name__}: {exc}"[:300]
            # F21: `errors` is surfaced via stats/context — never let it grow
            # without bound; drop the oldest entries first.
            while len(self.errors) > _MAINTAINER_MAX_ERRORS:
                self.errors.pop(next(iter(self.errors)))
            log.warning("space context maintenance failed: %s", exc)
        finally:
            # F21: a finished (or cancelled) task must not stay in `running`:
            # a stale entry made kick() return False for this chat forever.
            if self.running.get(key) is asyncio.current_task():
                self.running.pop(key, None)

    async def summarize(self, thread: Thread, chat_id: str, window_start_seq: int, model: str,
                        settings: Dict[str, Any], names: Dict[str, str], *, max_episodes: int = 4) -> int:
        made = 0
        summary_model = str(settings.get("summary_model") or model or "")
        budgets = ECONOMY_BUDGETS.get(str(settings.get("economy") or "balanced"), ECONOMY_BUDGETS["balanced"])
        for _ in range(max_episodes):
            covered = thread.covered_through(chat_id)
            pending = thread.range(chat_id, covered + 1, window_start_seq - 1, limit=EP_MAX_MESSAGES * 2)
            if len(pending) < EP_MIN_MESSAGES:
                break
            chunk: List[Dict[str, Any]] = []
            tokens = 0
            for row in pending:
                cost = min(int(row.get("tokens") or 0), 900)
                if chunk and (len(chunk) >= EP_MAX_MESSAGES or tokens + cost > EP_MAX_TOKENS):
                    break
                chunk.append(row)
                tokens += cost
            # never cut between a user message and its answer
            while len(chunk) > EP_MIN_MESSAGES and chunk[-1]["role"] == "user":
                chunk.pop()
            lang = _lang_of(chunk)
            text = ""
            if self.completion and summary_model:
                prompt = EPISODE_PROMPT.format(lang="Russian" if lang == "ru" else "English",
                                               open="Открыто:" if lang == "ru" else "Open:")
                story = thread.story(chat_id).get("text") or ""
                user = (f"<story_so_far>\n{story[-2500:]}\n</story_so_far>\n" if story else "") + \
                    f"<slice>\n{_transcript(chunk, names)}\n</slice>"
                try:
                    # F21: a hung summary model must not wedge this job.
                    text = await asyncio.wait_for(
                        self.completion(summary_model, [{"role": "system", "content": prompt},
                                                       {"role": "user", "content": user}],
                                        prompt_caching=False),
                        timeout=_MAINTAINER_CALL_TIMEOUT)
                except TypeError:
                    text = await asyncio.wait_for(
                        self.completion(summary_model, [{"role": "system", "content": prompt},
                                                       {"role": "user", "content": user}]),
                        timeout=_MAINTAINER_CALL_TIMEOUT)
                text = clean_text(text)
            if not text:
                text = extractive(chunk, names, 260)
                summary_model = summary_model or ""
            thread.add_episode(chat_id, chunk[0]["seq"], chunk[-1]["seq"], int(chunk[0].get("ts") or 0),
                               int(chunk[-1].get("ts") or 0), text, model=summary_model if text else "extractive")
            made += 1
        await self.roll_story(thread, chat_id, summary_model, budgets, names)
        return made

    async def roll_story(self, thread: Thread, chat_id: str, model: str, budgets: Dict[str, int],
                         names: Dict[str, str]) -> bool:
        unrolled = thread.episodes(chat_id, rolled=False)
        unrolled_tokens = sum(int(e.get("tokens") or 0) for e in unrolled)
        if len(unrolled) <= EP_UNROLLED_LIMIT and unrolled_tokens <= budgets["episodes"] * 1.6:
            return False
        keep = 2  # the newest episodes stay verbatim
        to_roll = unrolled[:-keep] if len(unrolled) > keep else []
        if not to_roll:
            return False
        story = thread.story(chat_id)
        lang = _lang_of([{"text": e["text"]} for e in to_roll])
        words_limit = max(120, int(budgets["story"] * 0.62))
        new_text = ""
        if self.completion and model:
            prompt = STORY_PROMPT.format(lang="Russian" if lang == "ru" else "English", words=words_limit,
                                         open="Открыто:" if lang == "ru" else "Open:")
            body = (f"<story>\n{story.get('text') or '(empty)'}\n</story>\n<new_episodes>\n" +
                    "\n\n".join(f"[{_fmt_day(int(e.get('ts_from') or 0))} – {_fmt_day(int(e.get('ts_to') or 0))}]\n{e['text']}"
                                for e in to_roll) + "\n</new_episodes>")
            try:
                # F21: same bound as in summarize().
                new_text = clean_text(await asyncio.wait_for(
                    self.completion(model, [{"role": "system", "content": prompt},
                                            {"role": "user", "content": body}]),
                    timeout=_MAINTAINER_CALL_TIMEOUT))
            except Exception as exc:
                log.info("story roll-up failed, keeping extractive story: %s", exc)
        if not new_text:
            merged = (story.get("text") or "").strip().split("\n")
            for e in to_roll:
                merged.append(f"[{_fmt_day(int(e.get('ts_from') or 0))}] " + first_sentence(e["text"], 220))
            new_text = "\n".join(line for line in merged if line.strip())
            while estimate_tokens(new_text) > budgets["story"] and "\n" in new_text:
                new_text = new_text.split("\n", 1)[1]
        thread.set_story(chat_id, new_text, int(to_roll[-1]["seq_to"]), [e["id"] for e in to_roll], model=model)
        return True

    async def embed_missing(self, thread: Thread, chat_id: str, model: str, *, rounds: int = 4) -> int:
        total = 0
        for _ in range(rounds):
            items = thread.missing_vectors(chat_id, model, limit=48)
            if not items:
                break
            texts = [str(i["text"])[:2000] for i in items]
            # F21: a hung embedding call must not wedge this job.
            vectors = await asyncio.wait_for(self.embed(model, texts),  # type: ignore[misc]
                                             timeout=_MAINTAINER_CALL_TIMEOUT)
            if not vectors or len(vectors) != len(items):
                break
            total += thread.put_vectors(chat_id, model, items, vectors)
        return total


def build_context(thread: Thread, chat_id: str, *, query: str, window_start_seq: int, economy: str,
                  names: Dict[str, str], query_vec: Optional[Sequence[float]] = None, vec_model: str = "",
                  recall: bool = True) -> Dict[str, Any]:
    budgets = ECONOMY_BUDGETS.get(economy, ECONOMY_BUDGETS["balanced"])
    story = thread.story(chat_id)
    story_text = str(story.get("text") or "")
    if story_text and estimate_tokens(story_text) > budgets["story"] * 1.25:
        lines = story_text.split("\n")
        while len(lines) > 1 and estimate_tokens("\n".join(lines)) > budgets["story"] * 1.25:
            lines.pop(0)
        story_text = "\n".join(lines)
    episodes = [e for e in thread.episodes(chat_id, rolled=False) if e["seq_from"] < window_start_seq]
    picked: List[Dict[str, Any]] = []
    used = 0
    for e in reversed(episodes):
        cost = int(e.get("tokens") or estimate_tokens(e["text"]))
        if picked and used + cost > budgets["episodes"]:
            break
        picked.append(e)
        used += cost
    picked.reverse()
    covered = thread.covered_through(chat_id)
    bridge_rows = thread.range(chat_id, covered + 1, window_start_seq - 1, limit=EP_MAX_MESSAGES * 3)
    bridge = extractive(bridge_rows, names, max(200, budgets["episodes"] // 2)) if bridge_rows else ""
    complete = not bridge_rows
    hits: List[Dict[str, Any]] = []
    if recall and query.strip() and window_start_seq > 0:
        raw_hits = thread.search(chat_id, query, limit=budgets["recall_hits"] * 2, before_seq=window_start_seq,
                                 query_vec=query_vec, vec_model=vec_model)
        spent = 0
        shown_eps = {e["id"] for e in picked}
        for hit in raw_hits:
            if hit["kind"] == "episode" and hit.get("seq") is not None and any(
                    e["seq_from"] == hit["seq"] for e in picked):
                continue
            cost = estimate_tokens(hit["snippet"])
            if hits and spent + cost > budgets["recall_tokens"]:
                break
            hits.append(hit)
            spent += cost
            if len(hits) >= budgets["recall_hits"]:
                break
        del shown_eps
    open_threads: List[str] = []
    for source in [story_text] + [e["text"] for e in picked]:
        for line in str(source or "").split("\n"):
            clean = line.strip(" -•\t")
            if re.match(r"^(Открыто|Open)\s*:", clean, flags=re.I):
                item = re.sub(r"^(Открыто|Open)\s*:\s*", "", clean, flags=re.I)
                if item and item not in open_threads:
                    open_threads.append(item[:200])
    return {
        "story": {"text": story_text, "through_seq": story.get("through_seq", -1)},
        "episodes": [{"seq_from": e["seq_from"], "seq_to": e["seq_to"], "ts_from": e.get("ts_from") or 0,
                      "ts_to": e.get("ts_to") or 0, "text": e["text"]} for e in picked],
        "bridge": bridge,
        "complete": complete,
        "recall": hits,
        "open_threads": open_threads[-6:],
        "budgets": budgets,
    }


# ── HTTP API ────────────────────────────────────────────────────────────────────────────────
def register(app, *, completion: Optional[Completion] = None, proxy_json=None) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from . import spaces

    async def embed(model: str, texts: List[str]) -> List[List[float]]:
        if proxy_json is None:
            return []
        status, data = await proxy_json("POST", "/api/admin/playground/embeddings",
                                        json.dumps({"model": model, "input": texts}, ensure_ascii=False).encode("utf-8"))
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(str((data or {}).get("error") if isinstance(data, dict) else data) or f"HTTP {status}")
        rows = sorted(data.get("data") or [], key=lambda r: int(r.get("index") or 0))
        return [list(r.get("embedding") or []) for r in rows]

    maintainer = Maintainer(completion, embed)
    app.state.space_context = maintainer
    query_cache: Dict[tuple, List[float]] = {}

    def err(message: str, status: int = 400):
        return JSONResponse({"error": message}, status_code=status)

    def _auth(request: Request) -> Optional[JSONResponse]:
        # F22: the same in-file check as /v1/chat/completions. The Playground
        # Host/Origin middleware is not a token auth, so a cross-site website
        # must not be able to create/delete spaces or wipe thread data.
        # Keyless loopback UI calls (no Origin, or same-authority Origin)
        # pass exactly like they do on /v1/chat/completions.
        from .config import config_store
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key")
        if not config_store.authorize_api_request(
            key, host=request.headers.get("host", ""),
            origin=request.headers.get("origin", ""),
            fetch_site=request.headers.get("sec-fetch-site", ""),
            peer=request.client.host if request.client else "",
        ):
            return JSONResponse(status_code=401, content={"error": "Неверный API-ключ хаба."})
        return None

    async def body_of(request: Request) -> Dict[str, Any]:
        # Malformed JSON must be a 400, not a silent default (same fix as
        # spaces.py: previously any parse error became {} and the call
        # proceeded with empty data). Empty body is still {}.
        try:
            raw = await request.body()
        except Exception:
            return {}
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            raise ValueError("Некорректный JSON")
        return data if isinstance(data, dict) else {}

    def chat_id_of(value: Any) -> str:
        chat_id = str(value or "")[:120]
        return chat_id if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", chat_id) else ""

    def settings_of() -> Dict[str, Any]:
        item = spaces.active_space()
        out = dict(spaces.CONTEXT_DEFAULTS)
        out.update(item.get("context") or {})
        return out

    def names_of(body: Dict[str, Any]) -> Dict[str, str]:
        item = spaces.active_space()
        comp = item.get("companion") or {}
        return {"user": str(body.get("user_name") or "User")[:40] or "User",
                "assistant": str(comp.get("name") or body.get("assistant_name") or "AI")[:40]}

    async def query_vector(model: str, text: str) -> Optional[List[float]]:
        key = (model, text[:2000])
        if key in query_cache:
            return query_cache[key]
        try:
            vectors = await embed(model, [text[:2000]])
        except Exception as exc:
            log.info("query embedding failed: %s", exc)
            return None
        vec = vectors[0] if vectors else None
        if vec:
            if len(query_cache) > 64:
                query_cache.pop(next(iter(query_cache)))
            query_cache[key] = vec
        return vec

    @app.post("/api/space/thread/ingest")
    async def thread_ingest(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        chat_id = chat_id_of(body.get("chat_id"))
        if not chat_id:
            return err("chat_id required")
        rows = body.get("messages") if isinstance(body.get("messages"), list) else []
        def message_ids(field: str) -> List[str]:
            ids = body.get(field)
            return [str(x)[:80] for x in ids[:500] if isinstance(x, (str, int))] if isinstance(ids, list) else []

        deleted = message_ids("deleted")
        edited = message_ids("edited")
        space_id = spaces.active_space_id()
        if body.get("space_id", space_id) != space_id:
            return err("workspace changed", 409)
        thread = thread_for(space_id)
        settings, names = settings_of(), names_of(body)
        if edited or deleted:
            maintainer.cancel(thread, chat_id)
        result = await asyncio.to_thread(thread.ingest, chat_id, [r for r in rows[:600] if isinstance(r, dict)], deleted, edited)
        try:
            window_start = int(body.get("window_start_seq"))
        except (TypeError, ValueError):
            window_start = -1
        if window_start > 0:
            maintainer.kick(thread, chat_id, window_start_seq=window_start, model=str(body.get("model") or ""),
                            settings=settings, names=names)
        elif settings.get("embedding_model"):
            maintainer.kick(thread, chat_id, window_start_seq=0, model="", settings=settings, names=names)
        result["ok"] = True
        return result

    @app.post("/api/space/thread/context")
    async def thread_context(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        chat_id = chat_id_of(body.get("chat_id"))
        if not chat_id:
            return err("chat_id required")
        settings = settings_of()
        economy = str(body.get("economy") or settings.get("economy") or "balanced")
        try:
            window_start = int(body.get("window_start_seq") or 0)
        except (TypeError, ValueError):
            window_start = 0
        query = str(body.get("query") or "")[:4000]
        vec = None
        vec_model = str(settings.get("embedding_model") or "")
        if vec_model and query.strip() and settings.get("recall", True) and window_start > 0:
            vec = await query_vector(vec_model, query)
        thread = thread_for()
        out = await asyncio.to_thread(build_context, thread, chat_id, query=query, window_start_seq=window_start,
                                      economy=economy, names=names_of(body), query_vec=vec,
                                      vec_model=vec_model if vec else "", recall=bool(settings.get("recall", True)))
        out["vector"] = bool(vec)
        out["error"] = maintainer.errors.get(f"{thread.path}|{chat_id}", "")
        return out

    @app.post("/api/space/thread/search")
    async def thread_search(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        chat_id = chat_id_of(body.get("chat_id")) if body.get("chat_id") else ""
        query = str(body.get("query") or "").strip()[:2000]
        if not query:
            return {"hits": []}
        settings = settings_of()
        vec_model = str(settings.get("embedding_model") or "")
        vec = await query_vector(vec_model, query) if vec_model else None
        before = body.get("before_seq")
        try:
            before_seq = int(before) if before is not None else None
        except (TypeError, ValueError):
            before_seq = None
        try:
            limit = max(1, min(50, int(body.get("limit") or 12)))
        except (TypeError, ValueError):
            limit = 12
        hits = await asyncio.to_thread(thread_for().search, chat_id, query, limit=limit, before_seq=before_seq,
                                       query_vec=vec, vec_model=vec_model if vec else "",
                                       include_episodes=bool(body.get("episodes", True)))
        return {"hits": hits, "vector": bool(vec)}

    @app.get("/api/space/thread/messages")
    async def thread_messages(request: Request, chat_id: str = "", before_seq: Optional[int] = None, after_seq: Optional[int] = None,
                              limit: int = 50):
        denied = _auth(request)
        if denied is not None:
            return denied
        chat = chat_id_of(chat_id)
        if not chat:
            return err("chat_id required")
        rows = await asyncio.to_thread(thread_for().messages, chat, before_seq, limit, after_seq)
        return {"messages": rows, "has_more": bool(rows) and len(rows) >= max(1, min(400, int(limit)))}

    @app.post("/api/space/thread/archive")
    async def thread_archive_put(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        chat_id = chat_id_of(body.get("chat_id"))
        if not chat_id:
            return err("chat_id required")
        items = [r for r in (body.get("messages") or [])[:1000] if isinstance(r, dict)]
        stored = await asyncio.to_thread(thread_for().archive_put, chat_id, items)
        return {"ok": True, "stored": stored}

    @app.get("/api/space/thread/archive")
    async def thread_archive_get(request: Request, chat_id: str = "", before_seq: Optional[int] = None, limit: int = 40):
        denied = _auth(request)
        if denied is not None:
            return denied
        chat = chat_id_of(chat_id)
        if not chat:
            return err("chat_id required")
        thread = thread_for()
        rows = await asyncio.to_thread(thread.archive_get, chat, before_seq, limit)
        total = await asyncio.to_thread(thread.archive_count, chat)
        first = rows[0]["seq"] if rows else None
        more = False
        if first is not None:
            older = await asyncio.to_thread(thread.archive_get, chat, first, 1)
            more = bool(older)
        return {"messages": rows, "has_more": more, "total": total}

    @app.get("/api/space/thread/stats")
    async def thread_stats(request: Request, chat_id: str = ""):
        denied = _auth(request)
        if denied is not None:
            return denied
        chat = chat_id_of(chat_id) if chat_id else ""
        thread = thread_for()
        out = await asyncio.to_thread(thread.stats, chat)
        out["settings"] = settings_of()
        out["error"] = maintainer.errors.get(f"{thread.path}|{chat}", "") if chat else ""
        out["busy"] = any(not t.done() for k, t in maintainer.running.items() if k.startswith(str(thread.path)))
        return out

    @app.post("/api/space/thread/forget")
    async def thread_forget(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        chat_id = chat_id_of(body.get("chat_id"))
        if not chat_id:
            return err("chat_id required")
        await asyncio.to_thread(thread_for().forget_chat, chat_id)
        return {"ok": True}

    @app.post("/api/space/thread/rebuild")
    async def thread_rebuild(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        chat_id = chat_id_of(body.get("chat_id"))
        if not chat_id:
            return err("chat_id required")
        thread = thread_for()
        if body.get("what") == "summaries":
            await asyncio.to_thread(thread.reset_summaries, chat_id)
        try:
            window_start = int(body.get("window_start_seq") or 0)
        except (TypeError, ValueError):
            window_start = 0
        started = maintainer.kick(thread, chat_id, window_start_seq=window_start, model=str(body.get("model") or ""),
                                  settings=settings_of(), names=names_of(body))
        return {"ok": True, "started": started}

    @app.post("/api/space/embeddings/test")
    async def embeddings_test(request: Request):
        denied = _auth(request)
        if denied is not None:
            return denied
        try:
            body = await body_of(request)
        except ValueError as exc:
            return err(str(exc))
        model = str(body.get("model") or "").strip()
        if not model:
            return err("model required")
        started = time.time()
        try:
            vectors = await embed(model, ["MemuAI embedding test", "проверка эмбеддингов"])
        except Exception as exc:
            return err(str(exc)[:400] or type(exc).__name__, 502)
        if not vectors or not vectors[0]:
            return err("empty embedding", 502)
        return {"ok": True, "dims": len(vectors[0]), "ms": int((time.time() - started) * 1000)}
