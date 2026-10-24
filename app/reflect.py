"""MemuAI Reflect (Round 27): a private look back at how the user works with AI.

A copy of Claude's «Reflect» for MemuAI. Settings → «Рефлексия» shows:

* an opening headline and paragraph about the user's real work over a period;
* «Ваше время с ИИ» — most active weekday, peak hour, total conversations, a
  daily activity chart and an estimate of the time spent (sessions split by an
  idle gap) — all computed here, deterministically, from message timestamps;
* «На что ушло время» — topics with a short description and a percentage;
* «Развитие навыков» — the 4D AI Fluency framework (Delegation, Description,
  Discernment, Diligence): an observation from the user's own chats plus one
  concrete next step inside MemuAI;
* a reflection question that changes every week.

The narrative parts come from the model the user picked, through the same
internal bridge that compresses long chats (``proxy_completion``). Only a
compact digest is sent: titles and short excerpts of the user's own messages,
never attachments or whole answers; big periods go map → reduce. The answer is
strict JSON, validated, retried with the reason, and cleaned of reasoning.

Privacy (as in Claude): temporary chats never reach this module (the window
never stores them; ``temp`` / ``temp-*`` rows are skipped anyway), files and
attachments are not read, sensitive or crisis conversations are never sent as
excerpts, never lead the report and never get a count or a percentage — when
distress shows, support resources are shown instead. Reports live only in
``memuai/reflect.json`` of the active profile; turning Reflect off deletes them.

«Время и фокус» (quiet hours and break reminders) is a separate feature that
also lives here: :class:`FocusTracker` counts continuous use across the main
window and Quick Ask, :func:`quiet_now` checks the user's quiet hours.
"""
import asyncio
import hashlib
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- preferences

PREF_DEFAULTS: Dict[str, Any] = {
    "reflect_enabled": True,
    "reflect_model": "",                 # "" = the default chat model
    "reflect_include_quick": True,       # Quick Ask chats (quick_chats.json)
    "reflect_include_telegram": True,    # Telegram chats stored with the regular ones
    "focus_quiet_enabled": False,
    "focus_quiet_days": [0, 1, 2, 3, 4, 5, 6],   # Monday = 0
    "focus_quiet_start": "22:00",
    "focus_quiet_end": "07:00",
    "focus_break_enabled": False,
    "focus_break_minutes": 60,
}

BREAK_MIN, BREAK_MAX = 10, 240
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def _hhmm(value: Any, default: str) -> str:
    match = _HHMM.match(str(value or "").strip())
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else default


def normalize_prefs(out: Dict[str, Any], raw: Any = None) -> Dict[str, Any]:
    """Validate the ``reflect_*`` / ``focus_*`` keys in place (called by pgdata)."""
    for key in ("reflect_enabled", "reflect_include_quick", "reflect_include_telegram",
                "focus_quiet_enabled", "focus_break_enabled"):
        out[key] = _bool(out.get(key), PREF_DEFAULTS[key])
    out["reflect_model"] = str(out.get("reflect_model") or "")[:300]
    days = out.get("focus_quiet_days")
    clean_days: List[int] = []
    if isinstance(days, (list, tuple)):
        for item in days:
            try:
                day = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6 and day not in clean_days:
                clean_days.append(day)
    else:
        clean_days = list(PREF_DEFAULTS["focus_quiet_days"])
    out["focus_quiet_days"] = sorted(clean_days)
    out["focus_quiet_start"] = _hhmm(out.get("focus_quiet_start"), PREF_DEFAULTS["focus_quiet_start"])
    out["focus_quiet_end"] = _hhmm(out.get("focus_quiet_end"), PREF_DEFAULTS["focus_quiet_end"])
    try:
        minutes = int(out.get("focus_break_minutes"))
    except (TypeError, ValueError, OverflowError):
        minutes = PREF_DEFAULTS["focus_break_minutes"]
    out["focus_break_minutes"] = max(BREAK_MIN, min(BREAK_MAX, minutes))
    return out


def _prefs() -> Dict[str, Any]:
    from .pgdata import load_preferences
    try:
        return load_preferences()
    except Exception:
        return dict(PREF_DEFAULTS)


# ---------------------------------------------------------------- periods

_NAIVE = timezone.utc     # a placeholder zone for calendar arithmetic on local wall-clock times

PERIODS = ("this_month", "last_month", "3m", "6m", "12m")
DEFAULT_PERIOD = "last_month"


def period_bounds(period: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """``[start, end)`` of a period in local time. «Прошлый месяц» = the previous calendar month,
    «Этот месяц» = from the 1st until now, «3/6/12 месяцев» = that many months back until now.

    Without an aware ``now`` the computer's local time is used and every bound gets the UTC offset of its own
    date (a period across a DST change starts at real local midnight, not an hour off)."""
    if now is None or now.tzinfo is None:
        start, end = period_bounds(period, (now or datetime.now()).replace(tzinfo=_NAIVE))
        return start.replace(tzinfo=None).astimezone(), end.replace(tzinfo=None).astimezone()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "this_month":
        return first, now
    if period == "last_month":
        prev = (first - timedelta(days=1)).replace(day=1)
        return prev, first
    months = {"3m": 3, "6m": 6, "12m": 12}.get(period)
    if not months:
        raise ValueError("unknown period")
    month = now.month - months
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(now.day, _days_in_month(year, month))
    return now.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0), now


def _days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return (nxt - timedelta(days=1)).day


# ---------------------------------------------------------------- conversations

def _text(content: Any) -> str:
    """Only the text of a message: attachments, images and file parts are never read."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text") and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def _ms(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if number != number or number <= 0:
        return 0
    if number < 10_000_000_000:          # seconds
        number *= 1000
    return int(number)


# Blocks MemuAI itself adds to user messages (attached file text, page text, tool output) are not the
# user's words: cut them before anything is counted or sent.
_INJECTED = re.compile(r"<(attached_file|file|document|page_content|tool_result|untrusted[\w-]*|selection)\b[^>]*>.*?</\1>",
                       re.S | re.I)


def _user_words(text: str) -> str:
    text = _INJECTED.sub(" ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def collect_conversations(prefs: Optional[Dict[str, Any]] = None, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every saved conversation of the active profile as ``{id, title, source, messages: [{role, text, ts}]}``.

    Skipped: temporary chats, internal rows (tool rounds, hidden instructions), empty messages; Telegram and
    Quick Ask chats only when the user left them included."""
    prefs = prefs if isinstance(prefs, dict) else _prefs()
    from .pgdata import load_chats
    out: List[Dict[str, Any]] = []
    try:
        chats = load_chats(profile_id)
    except Exception:
        chats = []
    for chat in chats:
        if not isinstance(chat, dict) or chat.get("temp") or str(chat.get("id") or "").startswith("temp-"):
            continue
        telegram = bool(chat.get("telegram")) or str(chat.get("id") or "").startswith("tg-")
        if telegram and not prefs.get("reflect_include_telegram", True):
            continue
        fallback = _ms(chat.get("created")) or _ms(chat.get("updated"))
        rows = []
        for message in chat.get("messages") or []:
            if not isinstance(message, dict) or message.get("internal") or message.get("role") not in ("user", "assistant"):
                continue
            if message.get("via") == "telegram" and not prefs.get("reflect_include_telegram", True):
                continue
            text = _text(message.get("content"))
            if message.get("role") == "user":
                text = _user_words(text)
            if not text.strip():
                continue
            rows.append({"role": message["role"], "text": text, "ts": _ms(message.get("createdAt")) or 0})
        if not rows:
            continue
        # Messages imported without times inherit the neighbour's (or the chat's) time.
        last = fallback
        for row in rows:
            if row["ts"]:
                last = row["ts"]
            else:
                row["ts"] = last
        if not any(row["ts"] for row in rows):
            continue
        # MemuCode chats of the window (mode "code") are coding sessions, not ordinary chats.
        source = "telegram" if telegram else (chat.get("mode") if chat.get("mode") in ("work", "code") else "chat")
        out.append({"id": str(chat.get("id")), "title": str(chat.get("title") or "")[:160], "source": source,
                    "messages": rows})
    if prefs.get("reflect_include_quick", True):
        try:
            from .quick_ask_api import load_quick_chats
            quick = load_quick_chats(profile_id)
        except Exception:
            quick = []
        for chat in quick:
            rows = []
            fallback = _ms(chat.get("created")) or _ms(chat.get("updated"))
            for turn in chat.get("turns") or []:
                if not isinstance(turn, dict):
                    continue
                ts = _ms(turn.get("t")) or fallback
                question = _user_words(str(turn.get("q") or ""))
                if question:
                    rows.append({"role": "user", "text": question, "ts": ts})
                answer = str(turn.get("a") or "")
                if answer.strip():
                    rows.append({"role": "assistant", "text": answer, "ts": ts})
            if rows and any(row["ts"] for row in rows):
                out.append({"id": "quick:" + str(chat.get("id")), "title": str(chat.get("title") or "")[:160],
                            "source": "quick", "messages": rows})
    return out


def in_period(conversations: Iterable[Dict[str, Any]], start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Conversations with at least one user message in ``[start, end)``; only those messages' turns are kept."""
    lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    result = []
    for conv in conversations:
        rows = [row for row in conv["messages"] if lo <= row["ts"] < hi]
        if any(row["role"] == "user" for row in rows):
            result.append({**conv, "messages": rows})
    return result


# ---------------------------------------------------------------- sensitive topics

# Level 2 (distress / crisis): never excerpted, never itemised, support resources are shown.
_CRISIS = re.compile(
    r"суицид\w*|самоуби\w*|покончить с собой|убить себя|не хочу (больше )?жить|хочу умереть|"
    r"порезать себя|самоповрежд\w*|\bsuicid\w*|kill myself|end my life|want to die|self[- ]?harm\w*|"
    r"\bдепресс\w*|\bdepress(ed|ion|ive)\b|паническ\w* атак\w*|panic attack\w*|\bтревожн\w* расстройств\w*|"
    r"anxiety disorder|насили\w*|\babuse[ds]?\b|\babusive\b|абьюз\w*|изнасил\w*|\brape[ds]?\b|\bгоре\b|\bgriev\w*|"
    r"\bумер(ла|ли)?\b|смерть (мамы|папы|отца|матери|друга|подруги|мужа|жены|ребёнка|ребенка)|passed away",
    re.I)
# Level 1 (personal / health): only at a high level, no excerpts or counts, no resources.
_SENSITIVE = re.compile(
    r"\bдиагноз\w*|\bdiagnos\w*|\bонколог\w*|\bрак(а|ом|е|у)?\b|\bcancer\b|\bопухол\w*|\btumou?r\w*|"
    r"\bберемен\w*|\bpregnan\w*|\bвич\b|\bhiv\b|\bпсихотерап\w*|\bпсихиатр\w*|\btherap(y|ist)\b|\bpsychiatr\w*|"
    r"\bразвод\w*|\bdivorc\w*|\bалкогол\w*|\balcohol\w*|\bнаркот\w*|\baddict\w*|\bпохорон\w*|\bfuneral\w*|"
    r"\bанализ(ы|ов) крови|\bblood test\w*|\bлекарств\w*|\bmedication\w*|\bантидепресс\w*|\bantidepress\w*|"
    r"\bтревог\w*|\banxiety\b|\bодиночеств\w*|\blonel\w*|\bсексуал\w*|\bsexual\w*",
    re.I)


def sensitivity(text: str) -> int:
    """0 — ordinary, 1 — personal/health, 2 — distress or crisis."""
    if not text:
        return 0
    if _CRISIS.search(text):
        return 2
    return 1 if _SENSITIVE.search(text) else 0


def conversation_sensitivity(conv: Dict[str, Any]) -> int:
    """Judged on the user's own words (an answer that lists hotlines must not flag a chat)."""
    return max([sensitivity(conv.get("title") or "")] +
               [sensitivity(row["text"][:4000]) for row in conv["messages"] if row["role"] == "user"] or [0])


# ---------------------------------------------------------------- deterministic statistics

IDLE_GAP_MIN = 20          # a pause longer than this starts a new session
SESSION_TAIL_MIN = 2       # reading the last answer of a session
SINGLE_SESSION_MIN = 1     # a lone message still took a moment


def sessions(timestamps: List[int], gap_min: int = IDLE_GAP_MIN) -> List[Tuple[int, int]]:
    """Merge message times into ``(start, end)`` sessions split by idle gaps."""
    stamps = sorted(t for t in timestamps if t)
    if not stamps:
        return []
    gap = gap_min * 60_000
    result = []
    begin = prev = stamps[0]
    for ts in stamps[1:]:
        if ts - prev > gap:
            result.append((begin, prev))
            begin = ts
        prev = ts
    result.append((begin, prev))
    return result


def session_minutes(span: Tuple[int, int]) -> float:
    begin, end = span
    if end <= begin:
        return float(SINGLE_SESSION_MIN)
    return (end - begin) / 60_000 + SESSION_TAIL_MIN


def compute_stats(conversations: List[Dict[str, Any]], start: datetime, end: datetime,
                  local: bool = False) -> Dict[str, Any]:
    """Counts, the daily series, the busiest weekday and hour and the time estimate for ``[start, end)``.

    ``conversations`` must already be cut to the period (:func:`in_period`). Times are bucketed in the
    timezone of ``start`` — or, with ``local``, in the computer's local time with each moment's own DST
    offset (``start`` of a local period carries only the offset of its first day). A conversation counts on
    every day it had a user message."""
    tz = None if local else start.tzinfo
    first_day = start.date()
    last_day = (end - timedelta(microseconds=1)).astimezone(tz).date() if end > start else first_day
    days = [(first_day + timedelta(days=i)) for i in range((last_day - first_day).days + 1)]
    index = {day: i for i, day in enumerate(days)}
    conv_per_day = [set() for _ in days]
    msgs_per_day = [0] * len(days)
    minutes_per_day = [0.0] * len(days)
    hours = [0] * 24
    weekdays = [0] * 7
    weekday_convs: List[set] = [set() for _ in range(7)]
    all_stamps: List[int] = []
    user_messages = 0
    for conv in conversations:
        for row in conv["messages"]:
            all_stamps.append(row["ts"])
            if row["role"] != "user":
                continue
            moment = datetime.fromtimestamp(row["ts"] / 1000, tz)
            pos = index.get(moment.date())
            if pos is None:
                continue
            user_messages += 1
            conv_per_day[pos].add(conv["id"])
            msgs_per_day[pos] += 1
            hours[moment.hour] += 1
            weekdays[moment.weekday()] += 1
            weekday_convs[moment.weekday()].add((conv["id"], moment.date()))
    spans = sessions(all_stamps)
    total_minutes = 0.0
    longest = 0.0
    for span in spans:
        minutes = session_minutes(span)
        total_minutes += minutes
        longest = max(longest, minutes)
        pos = index.get(datetime.fromtimestamp(span[0] / 1000, tz).date())
        if pos is not None:
            minutes_per_day[pos] += minutes
    series = [{"date": day.isoformat(), "conversations": len(conv_per_day[i]), "messages": msgs_per_day[i],
               "minutes": round(minutes_per_day[i], 1)} for i, day in enumerate(days)]
    active_days = sum(1 for item in series if item["messages"])
    busiest = max(series, key=lambda item: (item["conversations"], item["messages"]), default=None)
    top_weekday = None
    if user_messages:
        top_weekday = max(range(7), key=lambda d: (len(weekday_convs[d]), weekdays[d], -d))
    peak_hour = max(range(24), key=lambda h: (hours[h], -h)) if user_messages else None
    return {
        "conversations": len({conv["id"] for conv in conversations}),
        "user_messages": user_messages,
        "active_days": active_days,
        "days": len(days),
        "most_active_weekday": top_weekday,
        "busiest_day": busiest if busiest and busiest["messages"] else None,
        "peak_hour": peak_hour,
        "hours": hours,
        "weekdays": weekdays,
        "series": series,
        "time": {"minutes": round(total_minutes), "sessions": len(spans),
                 "average_session": round(total_minutes / len(spans)) if spans else 0,
                 "longest_session": round(longest), "idle_gap": IDLE_GAP_MIN},
        "sources": _source_counts(conversations),
    }


def _source_counts(conversations: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for conv in conversations:
        counts[conv["source"]] = counts.get(conv["source"], 0) + 1
    return counts


def fingerprint(conversations: List[Dict[str, Any]]) -> str:
    """Changes when a conversation of the period gets a new user message."""
    digest = hashlib.sha1()
    for conv in sorted(conversations, key=lambda c: c["id"]):
        users = [row["ts"] for row in conv["messages"] if row["role"] == "user"]
        digest.update(f"{conv['id']}:{len(users)}:{max(users) if users else 0};".encode())
    return digest.hexdigest()[:16]


# ---------------------------------------------------------------- behaviour signals (deterministic)

_CORRECTION = re.compile(r"\b(не так|неправильн\w*|ошибк\w*|ошибаешься|неверн\w*|переделай|исправь|"
                         r"на самом деле|это не то|wrong|incorrect|that's not|not what i|actually|redo|fix (it|this)|mistake)\b", re.I)
_VERIFY = re.compile(r"\b(источник\w*|ссылк\w*|провер\w*|уверен\w*|факт\w*|source[sd]?|cite|citation\w*|verify|"
                     r"double[- ]check|are you sure|evidence|пруф\w*)\b", re.I)
_DO_TASK = re.compile(r"^\s*(напиши|сделай|создай|составь|сгенерируй|переведи|перепиши|оформи|придумай|"
                      r"write|make|create|generate|draft|translate|rewrite|build|compose)\b", re.I)
_UNDERSTAND = re.compile(r"^\s*(объясни|почему|как работает|что такое|помоги понять|разбер\w*|"
                         r"explain|why|how does|what is|help me understand)\b", re.I)
_OWN_VOICE = re.compile(r"\b(моими словами|своими словами|мой стиль|мой черновик|мой текст|вот мой|"
                        r"my draft|my own words|in my voice|my style|here is my)\b", re.I)


def behaviour_signals(conversations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cheap, explainable signals for the 4D cards (the model turns them into observations)."""
    firsts, follow, corrections, verify, tasks, learn, own = [], 0, 0, 0, 0, 0, 0
    titles: Dict[str, int] = {}
    for conv in conversations:
        users = [row["text"] for row in conv["messages"] if row["role"] == "user"]
        if not users:
            continue
        firsts.append(len(users[0]))
        follow += max(0, len(users) - 1)
        corrections += sum(1 for text in users[1:] if _CORRECTION.search(text[:600]))
        verify += sum(1 for text in users if _VERIFY.search(text[:600]))
        tasks += 1 if _DO_TASK.search(users[0]) else 0
        learn += 1 if _UNDERSTAND.search(users[0]) else 0
        own += 1 if any(_OWN_VOICE.search(text[:2000]) for text in users) else 0
        key = " ".join(re.findall(r"\w{4,}", (conv.get("title") or "").lower())[:2])
        if key:
            titles[key] = titles.get(key, 0) + 1
    n = len(firsts) or 1
    firsts.sort()
    repeated = sorted(((k, v) for k, v in titles.items() if v >= 3), key=lambda kv: -kv[1])[:5]
    return {
        "conversations": len(firsts),
        "median_first_message_chars": firsts[len(firsts) // 2] if firsts else 0,
        "short_first_messages_share": round(sum(1 for x in firsts if x < 60) / n, 2),
        "follow_ups_per_conversation": round(follow / n, 1),
        "corrections_share": round(corrections / max(1, follow), 2),
        "verification_requests": verify,
        "task_requests_share": round(tasks / n, 2),
        "learning_requests_share": round(learn / n, 2),
        "own_draft_conversations": own,
        "recurring_title_themes": [k for k, _ in repeated],
        "work_conversations": sum(1 for c in conversations if c["source"] == "work"),
        "memucode_conversations": sum(1 for c in conversations if c["source"] == "code"),
    }


# ---------------------------------------------------------------- digest for the model

DIGEST_CHUNK_CHARS = 18_000     # one model call (≈6k tokens of digest)
DIGEST_MAX_CONVERSATIONS = 400
FIRST_CHARS, LATER_CHARS = 240, 140


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def digest_lines(conversations: List[Dict[str, Any]], tz: Any = None) -> Tuple[List[str], int]:
    """One compact line per ordinary conversation (newest first) and how many sensitive ones were withheld."""
    lines, withheld = [], 0
    ordered = sorted(conversations, key=lambda c: -max(row["ts"] for row in c["messages"]))
    for conv in ordered[:DIGEST_MAX_CONVERSATIONS]:
        if conversation_sensitivity(conv):
            withheld += 1
            continue
        users = [row for row in conv["messages"] if row["role"] == "user"]
        when = datetime.fromtimestamp(users[0]["ts"] / 1000, tz).strftime("%Y-%m-%d") if users else ""
        parts = [f"[{when} · {conv['source']} · {len(users)} msg]", _clip(conv.get("title") or "", 90) or "—",
                 "| first: " + _clip(users[0]["text"], FIRST_CHARS)]
        if len(users) > 1:
            parts.append("| later: " + _clip(users[-1]["text"], LATER_CHARS))
        lines.append(" ".join(parts))
    return lines, withheld


def chunk_lines(lines: List[str], limit: Optional[int] = None) -> List[List[str]]:
    limit = limit or DIGEST_CHUNK_CHARS
    chunks: List[List[str]] = [[]]
    size = 0
    for line in lines:
        if chunks[-1] and size + len(line) > limit:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += len(line) + 1
    return [chunk for chunk in chunks if chunk]


# ---------------------------------------------------------------- prompts

SKILLS = ("delegation", "description", "discernment", "diligence")
ACTIONS = ("project", "prompt", "skill", "memory", "agent", "none")

_SYSTEM = """You write the private «Reflect» report of MemuAI: a calm, specific look back at how ONE person used their AI assistant over a period. It reflects patterns without judgement so they can decide what, if anything, to change.

Rules:
- Write in {language}. Address the user as "you" ({you}). Be concrete: name the actual projects, subjects and kinds of tasks from the digest. No generic praise, no flattery, no moralising, no emoji.
- Use only what the digest and the numbers show. Never invent facts, names, counts or dates. The digest holds only excerpts of the user's own messages.
- The numbers (conversations, minutes, days, hours) are already shown next to your text: do not repeat them. The behaviour signals are rough hints for the skill cards — never quote their figures or percentages.
- Sensitive or personal conversations were withheld from the digest. Never lead with them, never give them a topic, a count or a percentage, never guess what they were about.
- MemuAI features you may suggest as next steps: a Work project (keeps files and context of ongoing work so it is not re-explained), a system prompt (standing instructions for every chat), a skill (a reusable playbook the assistant loads on demand), memory (facts about the user the assistant remembers), an agent («мемес» — an assistant persona with its own instructions).
- Output ONLY one JSON object, no markdown fences, no commentary."""

_FINAL_SCHEMA = """Return exactly this JSON shape:
{
  "headline": "short headline about this period, max 70 characters",
  "summary": "one paragraph, 2-4 sentences, max 520 characters, about the real work and themes",
  "topics": [ {"name": "2-4 words", "description": "max 110 characters", "percent": integer} ],
  "skills": {
    "delegation":  {"observation": "max 230 characters", "next_step": "max 170 characters", "action": "project|prompt|skill|memory|agent|none"},
    "description": {"observation": "...", "next_step": "...", "action": "..."},
    "discernment": {"observation": "...", "next_step": "...", "action": "..."},
    "diligence":   {"observation": "...", "next_step": "...", "action": "..."}
  }
}
topics: 2 to 6 items, largest first, percents are integers that add up to 100 (share of the conversations).
Delegation = what the user hands off and what they keep. Description = the context and goals they give. Discernment = how they weigh and correct what comes back. Diligence = responsibility for how AI output is used and represented.
Each observation must point at something visible in the digest or the signals; each next_step is one concrete action inside MemuAI (action names which feature it uses, "none" when it is a habit)."""

_MAP_SCHEMA = """This is part {part} of {parts} of the digest. Return exactly:
{"topics": [ {"name": "2-4 words", "description": "max 110 characters", "conversations": integer} ], "notes": ["max 4 short observations about how the user works with AI"]}
Up to 8 topics, "conversations" = how many digest lines belong to the topic."""


def _language(lang: str) -> Tuple[str, str]:
    return ("English", "you") if lang == "en" else ("Russian", "на «вы»")


def _period_label(period: str, start: datetime, end: datetime) -> str:
    return f"{period} ({start.strftime('%Y-%m-%d')} … {(end - timedelta(seconds=1)).strftime('%Y-%m-%d')})"


def _stats_for_model(stats: Dict[str, Any]) -> Dict[str, Any]:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return {"conversations": stats["conversations"], "your_messages": stats["user_messages"],
            "active_days": f"{stats['active_days']} of {stats['days']}",
            "most_active_weekday": names[stats["most_active_weekday"]] if stats["most_active_weekday"] is not None else None,
            "peak_hour": stats["peak_hour"], "estimated_minutes_with_ai": stats["time"]["minutes"],
            "sources": stats["sources"]}


def build_final_messages(lang: str, period_text: str, stats: Dict[str, Any], signals: Dict[str, Any],
                         digest: List[str], withheld: int, merged: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    language, you = _language(lang)
    body = [f"Period: {period_text}", "Numbers: " + json.dumps(_stats_for_model(stats), ensure_ascii=False),
            "Behaviour signals: " + json.dumps(signals, ensure_ascii=False)]
    if withheld:
        body.append("Some conversations of this period were personal or sensitive and were withheld (no details).")
    if merged is not None:
        body.append("Topics found across the whole period (merge similar ones, recompute shares): " +
                    json.dumps(merged["topics"], ensure_ascii=False))
        if merged["notes"]:
            body.append("Observations from the parts: " + json.dumps(merged["notes"], ensure_ascii=False))
        if digest:
            body.append("A sample of the newest conversations:\n" + "\n".join(digest))
    else:
        body.append("Digest (one line per conversation, newest first):\n" + "\n".join(digest))
    body.append(_FINAL_SCHEMA)
    return [{"role": "system", "content": _SYSTEM.format(language=language, you=you)},
            {"role": "user", "content": "\n\n".join(body)}]


def build_map_messages(lang: str, part: int, parts: int, lines: List[str]) -> List[Dict[str, str]]:
    language, you = _language(lang)
    return [{"role": "system", "content": _SYSTEM.format(language=language, you=you)},
            {"role": "user", "content": "Digest part:\n" + "\n".join(lines) + "\n\n" +
             _MAP_SCHEMA.replace("{part}", str(part)).replace("{parts}", str(parts))}]


# ---------------------------------------------------------------- strict JSON

class ReportError(Exception):
    """The model's answer is not a valid report (the message is shown to the model on retry)."""


_SAFETY = re.compile(r"<(ds_safety|think|thinking|reasoning)\b[^>]*>.*?</\1>", re.S | re.I)


def extract_json(text: str) -> Any:
    """The first complete JSON object in a model answer (fences, reasoning and chatter around it are ignored)."""
    raw = _SAFETY.sub("", str(text or ""))
    for tag in ("</think>", "</thinking>", "</reasoning>", "</ds_safety>"):
        if tag in raw:
            raw = raw.rsplit(tag, 1)[1]
    raw = re.sub(r"<ds_safety>.*$", "", raw, flags=re.S)     # an unterminated safety block at the end
    start = raw.find("{")
    while start != -1:
        depth, in_str, escape = 0, False, False
        for pos in range(start, len(raw)):
            ch = raw[pos]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:pos + 1])
                    except json.JSONDecodeError:
                        break
        start = raw.find("{", start + 1)
    raise ReportError("no JSON object found")


def _str(value: Any, limit: int, field: str, required: bool = True) -> str:
    if not isinstance(value, str) or not value.strip():
        if required:
            raise ReportError(f'"{field}" must be a non-empty string')
        return ""
    text = re.sub(r"\s+", " ", _SAFETY.sub("", value)).strip().strip('"')
    if len(text) > limit:
        cut = text[:limit]
        stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[: stop + 1] if stop > limit * 0.6 else cut.rstrip() + "…"
    return text


def percents(weights: List[float]) -> List[int]:
    """Integers that add up to exactly 100 (largest remainder), proportional to ``weights``."""
    total = sum(max(0.0, w) for w in weights)
    if total <= 0:
        weights = [1.0] * len(weights)
        total = float(len(weights))
    exact = [max(0.0, w) * 100 / total for w in weights]
    floors = [int(x) for x in exact]
    rest = 100 - sum(floors)
    for i in sorted(range(len(exact)), key=lambda i: -(exact[i] - floors[i]))[:rest]:
        floors[i] += 1
    return floors


def validate_final(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ReportError("the answer must be a JSON object")
    headline = _str(data.get("headline"), 90, "headline")
    summary = _str(data.get("summary"), 700, "summary")
    topics_raw = data.get("topics")
    if not isinstance(topics_raw, list) or not topics_raw:
        raise ReportError('"topics" must be a non-empty list')
    topics = []
    for item in topics_raw[:8]:
        if not isinstance(item, dict):
            raise ReportError("every topic must be an object")
        weight = item.get("percent", item.get("conversations"))
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            raise ReportError('every topic needs a numeric "percent"')
        topics.append({"name": _str(item.get("name"), 48, "topics[].name"),
                       "description": _str(item.get("description"), 140, "topics[].description", required=False),
                       "weight": max(0.0, weight)})
    skills_raw = data.get("skills")
    if not isinstance(skills_raw, dict):
        raise ReportError('"skills" must be an object with delegation, description, discernment, diligence')
    skills = {}
    for key in SKILLS:
        card = skills_raw.get(key) or skills_raw.get(key.capitalize())
        if not isinstance(card, dict):
            raise ReportError(f'"skills.{key}" is missing')
        action = str(card.get("action") or "none").strip().lower()
        skills[key] = {"observation": _str(card.get("observation"), 300, f"skills.{key}.observation"),
                       "next_step": _str(card.get("next_step"), 220, f"skills.{key}.next_step"),
                       "action": action if action in ACTIONS else "none"}
    return {"headline": headline, "summary": summary, "topics": topics, "skills": skills}


def validate_map(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        raise ReportError('the answer must be {"topics": [...], "notes": [...]}')
    topics = []
    for item in data["topics"][:10]:
        if not isinstance(item, dict):
            continue
        try:
            count = max(0.0, float(item.get("conversations", item.get("percent", 1))))
        except (TypeError, ValueError):
            count = 1.0
        name = _str(item.get("name"), 48, "topics[].name", required=False)
        if name:
            topics.append({"name": name, "description": _str(item.get("description"), 140, "d", required=False),
                           "conversations": count})
    if not topics:
        raise ReportError('"topics" is empty')
    notes = [_str(n, 200, "notes", required=False) for n in (data.get("notes") or [])[:4] if isinstance(n, str)]
    return {"topics": topics, "notes": [n for n in notes if n]}


# ---------------------------------------------------------------- privacy post-processing

def _sentences(text: str) -> List[str]:
    return [part for part in re.split(r"(?<=[.!?…])\s+", text.strip()) if part]


def apply_privacy(report: Dict[str, Any], lang: str) -> Dict[str, Any]:
    """Whatever the model wrote: sensitive topics leave the breakdown (shares are recomputed), a sensitive
    headline is replaced, sensitive sentences move to the end of the summary."""
    topics = [t for t in report["topics"] if not sensitivity(t["name"] + " " + t["description"])]
    if not topics:
        topics = [{"name": "Разное" if lang != "en" else "Various", "description": "", "weight": 1.0}]
    topics.sort(key=lambda t: -t["weight"])
    shares = percents([t["weight"] for t in topics])
    report["topics"] = [{"name": t["name"], "description": t["description"], "percent": p}
                        for t, p in zip(topics, shares) if p > 0] or [{"name": topics[0]["name"], "description": topics[0]["description"], "percent": 100}]
    if sensitivity(report["headline"]):
        top = report["topics"][0]["name"]
        report["headline"] = (f"Your period: {top} and more" if lang == "en" else f"Ваш период: {top} и не только")
    parts = _sentences(report["summary"])
    calm = [s for s in parts if not sensitivity(s)]
    personal = [s for s in parts if sensitivity(s)]
    report["summary"] = " ".join(calm + personal) if calm else report["summary"]
    for card in report["skills"].values():
        for key in ("observation", "next_step"):
            if sensitivity(card[key]) == 2:
                card[key] = card[key] if key == "next_step" else (
                    "Some conversations were personal; they are reflected only in general terms." if lang == "en"
                    else "Часть разговоров была личной — они учтены только в общих чертах.")
    return report


def merge_map_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Topics with the same (case-insensitive) name are added up; notes are concatenated."""
    merged: Dict[str, Dict[str, Any]] = {}
    notes: List[str] = []
    for result in results:
        for topic in result["topics"]:
            key = topic["name"].strip().lower()
            if key in merged:
                merged[key]["conversations"] += topic["conversations"]
            else:
                merged[key] = dict(topic)
        notes.extend(result.get("notes") or [])
    topics = sorted(merged.values(), key=lambda t: -t["conversations"])[:24]
    return {"topics": [{**t, "conversations": round(t["conversations"])} for t in topics], "notes": notes[:12]}


# ---------------------------------------------------------------- reflection questions

QUESTIONS = [
    ("Что одно вы хотите продолжать делать сами, даже если ИИ сделает быстрее?",
     "What's one thing you want to keep doing yourself, even if AI could do it faster?"),
    ("В какой момент за этот период ИИ сэкономил вам больше всего сил — и на что вы их потратили?",
     "When did AI save you the most effort this period — and what did you spend that effort on?"),
    ("Какой ответ ИИ вы приняли бы иначе, если бы проверили его сами?",
     "Which AI answer would you have taken differently if you had checked it yourself?"),
    ("Есть ли задача, которую вы отдаёте ИИ по привычке, хотя она вам нравится?",
     "Is there a task you hand to AI out of habit, even though you enjoy it?"),
    ("С кем вы бы обсудили то, что сейчас обсуждаете с ИИ, — и стоит ли?",
     "Who would you talk to about what you now discuss with AI — and would it be worth it?"),
    ("Чему вы научились сами благодаря разговорам с ИИ, а не просто получили ответ?",
     "What did you actually learn through talking with AI, rather than just getting an answer?"),
    ("Когда ИИ мешал вам думать, а не помогал?",
     "When did AI get in the way of your thinking instead of helping it?"),
    ("Какое время суток вы бы хотели оставить совсем без экранов?",
     "Which part of your day would you like to keep screen-free?"),
    ("Что из сделанного с ИИ вы бы с гордостью показали другим как свою работу?",
     "What that you made with AI would you proudly show others as your own work?"),
    ("Какой вопрос вы давно хотите задать себе, а не ИИ?",
     "What question have you been meaning to ask yourself rather than AI?"),
]


def question_for(when: Optional[date] = None, lang: str = "ru") -> Dict[str, Any]:
    """Changes every ISO week (the same for the whole week, so it can be thought over)."""
    when = when or date.today()
    year, week, _ = when.isocalendar()
    index = (year * 53 + week) % len(QUESTIONS)
    ru, en = QUESTIONS[index]
    return {"id": index, "text": en if lang == "en" else ru}


SUPPORT = {
    "ru": {"title": "Если сейчас тяжело",
           "text": "Часть ваших разговоров касалась непростых личных тем. Это нормально — и вы не обязаны справляться в одиночку: "
                   "поговорите с близким человеком или специалистом. В экстренной ситуации звоните 112.",
           "link": "https://findahelpline.com", "link_text": "Бесплатные линии помощи в вашей стране"},
    "en": {"title": "If things feel heavy",
           "text": "Some of your conversations touched on difficult personal things. That's okay, and you don't have to handle it alone: "
                   "talk to someone you trust or a professional. In an emergency call your local emergency number (112 in Europe, 911 in the US).",
           "link": "https://findahelpline.com", "link_text": "Free helplines in your country"},
}


# ---------------------------------------------------------------- cache and feedback (memuai/reflect.json)

def _store_path(profile_id: Optional[str] = None) -> Path:
    from .pgdata import MEMU_DIR, _profile_data_name
    return Path(MEMU_DIR) / _profile_data_name("reflect.json", profile_id)


def _valid_store(value: Any) -> bool:
    return isinstance(value, dict)


def load_store(profile_id: Optional[str] = None) -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    try:
        data = load_json_with_recovery(_store_path(profile_id), {}, validator=_valid_store)
    except (OSError, ValueError):
        data = {}
    data.setdefault("reports", {})
    data.setdefault("feedback", {})
    return data


def mutate_store(fn: Callable[[Dict[str, Any]], None], profile_id: Optional[str] = None) -> Dict[str, Any]:
    from .storage import mutate_json
    path = _store_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    def apply(value: Any):
        data = value if isinstance(value, dict) else {}
        data.setdefault("reports", {})
        data.setdefault("feedback", {})
        fn(data)
        return True, data
    return mutate_json(path, {}, apply, backup=False, validator=_valid_store, repair_invalid=True)


def cache_key(period: str, lang: str) -> str:
    return f"{period}:{'en' if lang == 'en' else 'ru'}"


def clear_store(profile_id: Optional[str] = None) -> None:
    path = _store_path(profile_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- generation

MIN_CONVERSATIONS = 3          # below this no model call: an honest «too little to reflect on»
MAX_ATTEMPTS = 3
Completion = Callable[[str, List[Dict[str, str]]], Awaitable[str]]


async def _ask_json(completion: Completion, model: str, messages: List[Dict[str, str]],
                    validate: Callable[[Any], Dict[str, Any]]) -> Dict[str, Any]:
    """One call with up to two retries; a retry tells the model what was wrong with its answer."""
    history = list(messages)
    last_error = ""
    for attempt in range(MAX_ATTEMPTS):
        answer = await completion(model, history)
        try:
            return validate(extract_json(answer))
        except ReportError as exc:
            last_error = str(exc)
            history = list(messages) + [
                {"role": "assistant", "content": str(answer or "")[:4000]},
                {"role": "user", "content": f"That answer was not valid ({last_error}). Reply again with ONLY the JSON object in the required shape."},
            ]
    raise ReportError(last_error or "invalid answer")


def snapshot(period: str, prefs: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
             profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Everything deterministic about a period: conversations, stats, signals, fingerprint, sensitivity."""
    prefs = prefs if isinstance(prefs, dict) else _prefs()
    start, end = period_bounds(period, now)
    local = now is None or now.tzinfo is None
    convs = in_period(collect_conversations(prefs, profile_id), start, end)
    levels = [conversation_sensitivity(conv) for conv in convs]
    return {"period": period, "start": start, "end": end, "conversations": convs,
            "tz": None if local else start.tzinfo,
            "stats": compute_stats(convs, start, end, local), "fingerprint": fingerprint(convs),
            "sensitive": max(levels or [0])}


async def generate_report(snap: Dict[str, Any], lang: str, model: str, completion: Completion,
                          progress: Optional[Callable[[str, int, int], None]] = None) -> Dict[str, Any]:
    """The narrative part of a report for a :func:`snapshot` (raises ``ReportError`` / provider errors)."""
    progress = progress or (lambda stage, done, total: None)
    convs, stats = snap["conversations"], snap["stats"]
    lines, withheld = digest_lines(convs, snap.get("tz"))
    signals = behaviour_signals([c for c in convs if not conversation_sensitivity(c)])
    period_text = _period_label(snap["period"], snap["start"], snap["end"])
    chunks = chunk_lines(lines)
    if len(chunks) <= 1:
        progress("writing", 0, 1)
        raw = await _ask_json(completion, model, build_final_messages(lang, period_text, stats, signals, lines, withheld),
                              validate_final)
        progress("writing", 1, 1)
    else:
        total = len(chunks) + 1
        results: List[Optional[Dict[str, Any]]] = [None] * len(chunks)
        done = 0
        progress("reading", 0, total)
        semaphore = asyncio.Semaphore(3)

        failures: List[ReportError] = []

        async def one(i: int, part: List[str]):
            nonlocal done
            async with semaphore:
                try:
                    results[i] = await _ask_json(completion, model,
                                                 build_map_messages(lang, i + 1, len(chunks), part), validate_map)
                except ReportError as exc:      # one unreadable part must not sink a year of conversations
                    failures.append(exc)
            done += 1
            progress("reading", done, total)
        tasks = [asyncio.ensure_future(one(i, part)) for i, part in enumerate(chunks)]
        try:
            await asyncio.gather(*tasks)
        finally:
            # One part failed (or the job was cancelled): the other parts must not keep calling the model.
            for task in tasks:
                task.cancel()
        if not any(results):
            raise failures[0] if failures else ReportError("invalid answer")
        merged = merge_map_results([r for r in results if r])
        sample = chunk_lines(lines, DIGEST_CHUNK_CHARS // 3)[0]
        progress("writing", total - 1, total)
        raw = await _ask_json(completion, model,
                              build_final_messages(lang, period_text, stats, signals, sample, withheld, merged),
                              validate_final)
        progress("writing", total, total)
    report = apply_privacy(raw, lang)
    return report


# ---------------------------------------------------------------- jobs

class Jobs:
    """At most one generation per (profile, period, language); the window polls ``status``."""

    def __init__(self):
        self.items: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.items.get(key)

    def start(self, key: str, runner: Callable[[Dict[str, Any]], Awaitable[None]]) -> Dict[str, Any]:
        job = self.items.get(key)
        if job and job["status"] == "running":
            return job
        job = {"id": uuid.uuid4().hex[:12], "status": "running", "stage": "reading", "done": 0, "total": 1,
               "started": time.time(), "error": ""}
        self.items[key] = job

        async def run():
            try:
                await runner(job)
                job["status"] = "done"
            except asyncio.CancelledError:
                job["status"] = "error"
                job["error"] = "cancelled"
                raise
            except Exception as exc:  # shown to the user, with a retry button
                job["status"] = "error"
                job["error"] = str(exc) or exc.__class__.__name__
        job["task"] = asyncio.create_task(run())
        return job


def friendly_error(error: str, lang: str) -> str:
    """A model/provider failure in plain words (the raw text stays in ``detail``)."""
    low = (error or "").lower()
    en = lang == "en"
    if "no json" in low or "must be" in low or "missing" in low or "invalid" in low or "not valid" in low:
        return ("The model answered in an unexpected format several times. Try again or pick another model."
                if en else "Модель несколько раз ответила не в том формате. Повторите или выберите другую модель.")
    if "too few" in low:
        return ("There are too few conversations in this period for a report."
                if en else "В этом периоде слишком мало разговоров для отчёта.")
    if "пуст" in low or "empty" in low:
        return "The model returned an empty answer. Try again." if en else "Модель вернула пустой ответ. Повторите."
    if "connect" in low or "proxy" in low or "timed out" in low or "timeout" in low or "httpx" in low:
        return ("MemuAI Proxy is not responding. Make sure it is running, then try again."
                if en else "MemuAI Proxy не отвечает. Проверьте, что он запущен, и повторите.")
    if "429" in low or "rate" in low or "limit" in low or "quota" in low:
        return ("The model's limit is used up for now. Try later or pick another model."
                if en else "У модели закончился лимит. Повторите позже или выберите другую модель.")
    if re.search(r"\b50[0-4]\b|unavailable|overloaded|bad gateway", low):
        return ("The model's service is temporarily unavailable. Try again in a minute or pick another model."
                if en else "Сервис модели временно недоступен. Повторите через минуту или выберите другую модель.")
    if "model" in low and ("not found" in low or "unknown" in low or "не найден" in low):
        return "This model is not available. Pick another one." if en else "Эта модель недоступна. Выберите другую."
    return ("The report could not be written. Try again or pick another model."
            if en else "Не удалось составить отчёт. Повторите или выберите другую модель.")


# ---------------------------------------------------------------- «Время и фокус»

def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def quiet_now(prefs: Dict[str, Any], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The quiet window the user is in right now (``{"until": "07:00", "ends_at": epoch_ms}``) or ``None``.

    A window that crosses midnight (22:00 → 07:00) belongs to the day it starts on."""
    if not prefs.get("focus_quiet_enabled"):
        return None
    days = set(prefs.get("focus_quiet_days") or [])
    start_s, end_s = prefs.get("focus_quiet_start") or "22:00", prefs.get("focus_quiet_end") or "07:00"
    start, end = _minutes(start_s), _minutes(end_s)
    if start == end or not days:
        return None
    now = now or datetime.now()
    current = now.hour * 60 + now.minute
    today = now.weekday()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if start < end:
        if today in days and start <= current < end:
            return {"until": end_s, "ends_at": int((midnight + timedelta(minutes=end)).timestamp() * 1000)}
        return None
    if current >= start and today in days:
        return {"until": end_s, "ends_at": int((midnight + timedelta(days=1, minutes=end)).timestamp() * 1000)}
    if current < end and (today - 1) % 7 in days:
        return {"until": end_s, "ends_at": int((midnight + timedelta(minutes=end)).timestamp() * 1000)}
    return None


def focus_store_path() -> Path:
    """``memuai/focus.json``: the day's time with MemuAI (one per computer, shared by every profile and client)."""
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR) / "focus.json"


_SHARED_TRACKER: Optional["FocusTracker"] = None


def shared_tracker() -> "FocusTracker":
    """The file-backed tracker of this process (MemuAI window routes and the Telegram bot use the same file)."""
    global _SHARED_TRACKER
    if _SHARED_TRACKER is None or _SHARED_TRACKER.path != focus_store_path():
        _SHARED_TRACKER = FocusTracker(path=focus_store_path())
    return _SHARED_TRACKER


class FocusTracker:
    """Daily time with MemuAI, shared by the main window, Quick Ask and the Telegram bot (as in Claude, the break
    reminder counts the day's time, not one stretch).

    Every interaction pings :meth:`activity`. The time between two pings counts when the gap is under
    ``IDLE_MIN`` (a longer pause is away time); a lone ping counts as ``PING_CREDIT`` seconds. The counter
    restarts at local midnight. Once the day's time reaches the user's threshold the next ping answers
    ``break_due`` (once — the window that got it shows it): «Через 10 минут» re-arms after ``SNOOZE_MIN``,
    «Сделаю перерыв» after another full threshold of use, «Скрыть» ends reminders for today.

    With ``path`` the state lives in a small JSON file (``memuai/focus.json``) updated under the cross-process
    lock, so the MemuAI window and the Telegram bot in MemuAI Proxy add up to one counter that survives a
    restart; without it (tests) it stays in memory."""

    IDLE_MIN = 10
    SNOOZE_MIN = 10
    PING_CREDIT = 60

    def __init__(self, clock: Callable[[], float] = time.time, path: Optional[Path] = None):
        self.clock = clock
        self.path = path
        self.state: Dict[str, Any] = {}

    @staticmethod
    def _today(now: float) -> str:
        return datetime.fromtimestamp(now).date().isoformat()

    def _update(self, fn: Callable[[Dict[str, Any]], Any]) -> Any:
        if self.path is None:
            return fn(self.state)
        from .storage import mutate_json
        result: List[Any] = []

        def apply(value: Any):
            data = value if isinstance(value, dict) else {}
            result.append(fn(data))
            return True, data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        mutate_json(self.path, {}, apply, backup=False, validator=lambda v: isinstance(v, dict), repair_invalid=True)
        return result[0]

    def _roll(self, data: Dict[str, Any], now: float) -> None:
        if data.get("day") != self._today(now):
            data.clear()
            data.update(day=self._today(now), used=0.0, last=0.0, snooze_until=0.0, rearm=0.0, dismissed=False)

    def activity(self, minutes: int, enabled: bool, now: Optional[float] = None) -> Dict[str, Any]:
        now = self.clock() if now is None else now

        def apply(data: Dict[str, Any]) -> Dict[str, Any]:
            self._roll(data, now)
            last = float(data.get("last") or 0)
            gap = now - last
            if last and 0 <= gap <= self.IDLE_MIN * 60:
                data["used"] = float(data.get("used") or 0) + gap
            elif not last or gap > self.IDLE_MIN * 60:
                data["used"] = float(data.get("used") or 0) + self.PING_CREDIT
            data["last"] = max(last, now)
            data["threshold"] = minutes * 60
            due_at = max(minutes * 60, float(data.get("rearm") or 0))
            due = bool(enabled and not data.get("dismissed") and data["used"] >= due_at
                       and now >= float(data.get("snooze_until") or 0))
            if due:
                # Shown once: the window that got it shows the reminder; the next ping waits for snooze/dismiss.
                data["snooze_until"] = now + self.SNOOZE_MIN * 60
            return {"minutes_today": int(data["used"] // 60), "break_due": due, "threshold": minutes}
        return self._update(apply)

    def action(self, kind: str, now: Optional[float] = None) -> Dict[str, Any]:
        now = self.clock() if now is None else now

        def apply(data: Dict[str, Any]) -> None:
            self._roll(data, now)
            if kind == "snooze":
                data["snooze_until"] = now + self.SNOOZE_MIN * 60
            elif kind == "break":
                # After a break the next reminder comes after another full threshold of use; the pause itself
                # never counts.
                data["rearm"] = float(data.get("used") or 0) + float(data.get("threshold") or 3600)
                data["snooze_until"] = 0.0
                data["last"] = 0.0
            else:
                data["dismissed"] = True        # «Скрыть»: no more reminders today
        self._update(apply)
        return {"ok": True}


# ---------------------------------------------------------------- HTTP routes

def register(app, completion: Completion) -> None:
    """``/api/reflect/*`` and ``/api/focus/*`` on the MemuAI server (``completion`` = ``proxy_completion``)."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    jobs = Jobs()
    tracker = shared_tracker()
    app.state.reflect_jobs = jobs
    app.state.focus_tracker = tracker
    app.state.reflect_completion = completion      # tests replace it with a scripted model

    def lang_of(value: str, prefs: Dict[str, Any]) -> str:
        value = value or prefs.get("language") or "ru"
        return "en" if value == "en" else "ru"

    def period_of(value: str) -> str:
        return value if value in PERIODS else DEFAULT_PERIOD

    def profile_key() -> str:
        try:
            from .pgdata import _profiles_state
            return str(_profiles_state().get("active_id") or "default")
        except Exception:
            return "default"

    def cancel_jobs() -> None:
        """Stop every report being written for the active profile (Reflect was turned off)."""
        for key in [k for k in jobs.items if k.startswith(profile_key() + ":")]:
            task = jobs.items[key].get("task")
            if task and not task.done():
                task.cancel()
            jobs.items.pop(key, None)

    def view(period: str, lang: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
        snap = snapshot(period, prefs)
        stats = snap["stats"]
        store = load_store()
        cached = store["reports"].get(cache_key(period, lang))
        out: Dict[str, Any] = {
            "enabled": True, "period": period, "lang": lang,
            "range": {"start": snap["start"].isoformat(), "end": snap["end"].isoformat()},
            "stats": stats, "fingerprint": snap["fingerprint"],
            "question": question_for(lang=lang), "min_conversations": MIN_CONVERSATIONS,
            "support": SUPPORT[lang] if snap["sensitive"] >= 2 else None,
            "state": "empty" if stats["conversations"] == 0 else ("few" if stats["conversations"] < MIN_CONVERSATIONS else "ready"),
            "report": None,
        }
        if cached and out["state"] == "ready":
            out["report"] = cached["report"]
            out["generated_at"] = cached.get("generated_at")
            out["model"] = cached.get("model") or ""
            out["stale"] = cached.get("fingerprint") != snap["fingerprint"]
            out["feedback"] = store["feedback"].get(cache_key(period, lang) + "@" + str(cached.get("generated_at")), "")
        return out

    def job_view(job: Optional[Dict[str, Any]], lang: str) -> Optional[Dict[str, Any]]:
        if not job:
            return None
        out = {key: job[key] for key in ("id", "status", "stage", "done", "total")}
        if job["status"] == "error":
            out["error"] = friendly_error(job["error"], lang)
            out["detail"] = job["error"][:400]
        return out

    @app.get("/api/reflect")
    async def reflect_get(period: str = DEFAULT_PERIOD, lang: str = ""):
        prefs = await asyncio.to_thread(_prefs)
        lang = lang_of(lang, prefs)
        if not prefs.get("reflect_enabled", True):
            # Turned off anywhere (this window, a synced profile, the Mini App): no report survives.
            cancel_jobs()
            await asyncio.to_thread(clear_store)
            return {"enabled": False, "lang": lang}
        period = period_of(period)
        try:
            out = await asyncio.to_thread(view, period, lang, prefs)
        except Exception as exc:
            return JSONResponse({"error": ("Could not read your chats: " if lang == "en" else "Не удалось прочитать чаты: ") + str(exc)}, 500)
        out["job"] = job_view(jobs.get(f"{profile_key()}:{cache_key(period, lang)}"), lang)
        return out

    @app.get("/api/reflect/status")
    async def reflect_status(period: str = DEFAULT_PERIOD, lang: str = ""):
        """Only the job: the window polls this while the model writes (the full GET re-reads every chat)."""
        prefs = await asyncio.to_thread(_prefs)
        lang = lang_of(lang, prefs)
        return {"job": job_view(jobs.get(f"{profile_key()}:{cache_key(period_of(period), lang)}"), lang)}

    @app.post("/api/reflect/generate")
    async def reflect_generate(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        prefs = await asyncio.to_thread(_prefs)
        lang = lang_of(str(body.get("lang") or ""), prefs)
        if not prefs.get("reflect_enabled", True):
            return JSONResponse({"error": "Reflect is turned off" if lang == "en" else "Рефлексия выключена"}, 409)
        period = period_of(str(body.get("period") or ""))
        model = str(body.get("model") or prefs.get("reflect_model") or prefs.get("default_model") or "").strip()[:300]
        if not model:
            return JSONResponse({"error": "Pick a model for the report" if lang == "en" else "Выберите модель для отчёта"}, 400)
        key = f"{profile_key()}:{cache_key(period, lang)}"
        profile_id = profile_key()

        async def runner(job: Dict[str, Any]):
            snap = await asyncio.to_thread(snapshot, period, prefs, None, profile_id)
            if snap["stats"]["conversations"] < MIN_CONVERSATIONS:
                raise ReportError("too few conversations")

            def progress(stage: str, done: int, total: int):
                job.update(stage=stage, done=done, total=total)
            report = await generate_report(snap, lang, model, app.state.reflect_completion, progress)
            stamp = int(time.time() * 1000)
            entry = {"report": report, "generated_at": stamp, "fingerprint": snap["fingerprint"], "model": model}

            def save(data: Dict[str, Any]):
                data["reports"][cache_key(period, lang)] = entry
            # Turned off while the model was writing (the Mini App, a synced profile): nothing is kept.
            if not (await asyncio.to_thread(_prefs)).get("reflect_enabled", True):
                raise ReportError("Reflect is turned off")
            await asyncio.to_thread(mutate_store, save, profile_id)
        job = jobs.start(key, runner)
        return {"job": job_view(job, lang)}

    @app.post("/api/reflect/feedback")
    async def reflect_feedback(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        period = period_of(str(body.get("period") or ""))
        lang = "en" if body.get("lang") == "en" else "ru"
        value = str(body.get("value") or "")
        if value not in ("up", "down", ""):
            return JSONResponse({"error": "value must be up, down or empty"}, 400)
        stamp = str(body.get("generated_at") or "")

        def save(data: Dict[str, Any]):
            key = cache_key(period, lang) + "@" + stamp
            if value:
                data["feedback"][key] = value
            else:
                data["feedback"].pop(key, None)
            # Only the latest 100 votes are kept.
            if len(data["feedback"]) > 100:
                for old in list(data["feedback"])[:-100]:
                    data["feedback"].pop(old, None)
        await asyncio.to_thread(mutate_store, save)
        return {"ok": True, "value": value}

    @app.post("/api/reflect/clear")
    async def reflect_clear():
        """Turning Reflect off deletes every saved report of the active profile."""
        cancel_jobs()
        await asyncio.to_thread(clear_store)
        return {"ok": True}

    @app.get("/api/focus/state")
    async def focus_state():
        prefs = await asyncio.to_thread(_prefs)
        return {"quiet": quiet_now(prefs), "break_enabled": bool(prefs.get("focus_break_enabled")),
                "break_minutes": prefs.get("focus_break_minutes")}

    @app.post("/api/focus/activity")
    async def focus_activity(request: Request):
        prefs = await asyncio.to_thread(_prefs)
        out = await asyncio.to_thread(tracker.activity, int(prefs.get("focus_break_minutes") or 60),
                                      bool(prefs.get("focus_break_enabled")))
        out["quiet"] = quiet_now(prefs)
        return out

    @app.post("/api/focus/break")
    async def focus_break(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        kind = str((body or {}).get("action") or "dismiss") if isinstance(body, dict) else "dismiss"
        if kind not in ("snooze", "dismiss", "break"):
            return JSONResponse({"error": "action must be snooze, dismiss or break"}, 400)
        return await asyncio.to_thread(tracker.action, kind)
