"""Synthetic MemuAI history for «Рефлексия» tests, screenshots and the live run (Round 27).

``make_history(now)`` returns ``(chats, quick_chats)`` shaped exactly like ``memuai/chats.json`` and
``memuai/quick_chats.json``: ~60 conversations over the last ~3 months with real-looking Russian topics,
plus the traps the privacy rules must catch — a temporary chat, an attachment with text, a crisis
conversation and a health one. Deterministic for a given ``seed``.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

TEMP_MARKER = "СЕКРЕТНЫЙ-ВРЕМЕННЫЙ-ЧАТ"
ATTACHMENT_MARKER = "ТЕКСТ-ВЛОЖЕНИЯ-НЕ-ЧИТАТЬ"
CRISIS_MARKER = "не хочу больше жить"
HEALTH_MARKER = "анализы крови"

# (title, first message, follow-ups, source, weight)
TOPICS: List[Tuple[str, str, List[str], str, int]] = [
    ("Ошибка в FastAPI эндпоинте", "У меня в FastAPI эндпоинт /api/reports возвращает 500 при пустом списке, вот трейсбек: KeyError 'items'. Проект MemuAI, Python 3.12.",
     ["Не так, ты предлагаешь менять модель, а ошибка в сериализации. Переделай.", "Теперь работает, напиши тест на этот случай."], "work", 6),
    ("Тесты для парсера SSE", "Напиши unittest для функции parse_sse_lines: пустые строки, heartbeat, [DONE], битый JSON.",
     ["Добавь случай с \\r\\n.", "Проверь, что тест реально падает на старой версии."], "work", 4),
    ("Рефакторинг storage.py", "Помоги разбить storage.py на модули, чтобы атомарная запись JSON жила отдельно. Вот текущая структура…",
     ["Объясни, почему ты выбрал такой порядок импорта."], "work", 3),
    ("Испанский: прошедшее время", "Объясни разницу между pretérito perfecto и indefinido простыми словами, с примерами из жизни.",
     ["Дай 10 предложений для перевода, ответы потом.", "Вот мои переводы, проверь и объясни ошибки."], "chat", 5),
    ("Поездка в Тбилиси", "Составь план на 5 дней в Тбилиси в ноябре: я люблю ходить пешком, вино и старые районы, бюджет средний.",
     ["Добавь однодневную поездку в Кахетию.", "Какие источники ты использовал для цен? Проверь, актуальны ли они."], "chat", 4),
    ("Письмо клиенту о сроках", "Напиши письмо клиенту: проект задерживается на неделю из-за интеграции с платёжкой, тон вежливый, без оправданий.",
     ["Вот мой черновик, перепиши его моими словами, но короче.", "Убери канцелярит."], "chat", 4),
    ("Бюджет на месяц", "Помоги составить таблицу бюджета: зарплата 180к, аренда 55к, хочу откладывать 20%.",
     ["Сделай формулы для Google Sheets."], "chat", 3),
    ("Ужин из того, что есть", "Что приготовить на ужин: курица, рис, брокколи, соевый соус, 30 минут.",
     ["А без духовки?"], "quick", 3),
    ("План тренировок", "Составь план тренировок 3 раза в неделю дома, есть гантели 2×10 кг, цель — спина и осанка.",
     ["Это не то, мне нужно без прыжков, соседи снизу."], "chat", 2),
    ("Перевести абзац", "Переведи на английский: «Мы благодарим вас за терпение и уже исправили проблему».", [], "quick", 3),
    ("Код-ревью pull request", "Посмотри мой diff: добавил кэш на lru_cache для load_prompts, нет ли гонки при записи?",
     ["Ты уверен? Дай ссылку на документацию про потокобезопасность lru_cache."], "work", 3),
    ("Telegram-бот: напоминания", "Как в боте сделать ежедневное напоминание в 9 утра по Москве?", ["Сделай короче."], "telegram", 2),
]


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def make_history(now: datetime | None = None, n: int = 60, seed: int = 7,
                 days: int = 95) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rng = random.Random(seed)
    now = (now or datetime.now()).astimezone()
    pool = [topic for topic in TOPICS for _ in range(topic[4])]
    chats: List[Dict[str, Any]] = []
    quick: List[Dict[str, Any]] = []
    for i in range(n):
        title, first, follow, source, _ = rng.choice(pool)
        day = now - timedelta(days=rng.randint(0, days))
        if rng.random() < 0.35:          # Tuesdays are the busiest
            day -= timedelta(days=(day.weekday() - 1) % 7)
        hour = rng.choice([10, 10, 11, 11, 11, 12, 14, 15, 21, 22, 22, 23])
        start = day.replace(hour=hour, minute=rng.randint(0, 59), second=0, microsecond=0)
        if start > now:
            start -= timedelta(days=1)
        messages = []
        moment = start
        texts = [first] + follow[: rng.randint(0, len(follow))]
        for text in texts:
            messages.append({"role": "user", "content": text, "createdAt": _ms(moment)})
            moment += timedelta(minutes=rng.randint(1, 4))
            messages.append({"role": "assistant", "content": f"Ответ модели на «{text[:40]}…». " * 3, "createdAt": _ms(moment),
                             "model": "demo-model"})
            moment += timedelta(minutes=rng.randint(2, 9))
        if source == "quick":
            turns = [{"q": m["content"], "a": messages[k + 1]["content"], "t": m["createdAt"], "model": "demo"}
                     for k, m in enumerate(messages) if m["role"] == "user"]
            quick.append({"id": f"q{i:04d}x", "title": title, "created": turns[0]["t"], "updated": turns[-1]["t"], "turns": turns})
            continue
        chat = {"id": f"chat-{i:04d}", "title": title, "messages": messages, "mode": "work" if source == "work" else "chat",
                "created": messages[0]["createdAt"], "updated": messages[-1]["createdAt"]}
        if source == "telegram":
            chat["id"] = f"tg-{i:04d}"
            chat["telegram"] = True
            for m in chat["messages"]:
                if m["role"] == "user":
                    m["via"] = "telegram"
        chats.append(chat)
    base = now - timedelta(days=5)
    # The traps.
    chats.append({"id": "temp-abc", "temp": True, "title": TEMP_MARKER, "updated": _ms(base),
                  "messages": [{"role": "user", "content": TEMP_MARKER + " расскажи", "createdAt": _ms(base)}]})
    chats.append({"id": "chat-attach", "title": "Разбор договора", "updated": _ms(base), "created": _ms(base),
                  "messages": [{"role": "user", "content": "Перескажи договор аренды коротко.", "createdAt": _ms(base),
                                "attachments": [{"name": "dogovor.pdf", "text": ATTACHMENT_MARKER}]},
                               {"role": "assistant", "content": "Договор на 11 месяцев, залог равен месячной оплате.", "createdAt": _ms(base) + 60_000}]})
    crisis = now - timedelta(days=3, hours=2)
    chats.append({"id": "chat-crisis", "title": "Тяжёлый вечер", "updated": _ms(crisis), "created": _ms(crisis),
                  "messages": [{"role": "user", "content": f"Мне очень плохо, кажется, я {CRISIS_MARKER}.", "createdAt": _ms(crisis)},
                               {"role": "assistant", "content": "Мне жаль, что вам так тяжело. Позвоните 112…", "createdAt": _ms(crisis) + 60_000}]})
    health = now - timedelta(days=9)
    chats.append({"id": "chat-health", "title": "Расшифровка", "updated": _ms(health), "created": _ms(health),
                  "messages": [{"role": "user", "content": f"Помоги понять мои {HEALTH_MARKER}: ферритин 8, гемоглобин 110.", "createdAt": _ms(health)}]})
    chats.sort(key=lambda c: -c["updated"])
    quick.sort(key=lambda c: -c["updated"])
    return chats, quick


def write_history(data_dir, now: datetime | None = None, **kwargs) -> Tuple[int, int]:
    """Write the history into ``<data_dir>/memuai`` (the layout ``MEMUAI_DATA_DIR`` expects)."""
    import json
    from pathlib import Path
    chats, quick = make_history(now, **kwargs)
    folder = Path(data_dir) / "memuai"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "chats.json").write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")
    (folder / "quick_chats.json").write_text(json.dumps(quick, ensure_ascii=False), encoding="utf-8")
    return len(chats), len(quick)
