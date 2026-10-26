# -*- coding: utf-8 -*-
"""MemuLearn ⇄ the model: prompts (``prompt(task, …)``) and the parsing of the answers (``apply(task, answer, …)``).

The module window asks the model itself (``Memu.request`` — the person's model choice, quotas and providers stay
MemuAI's), so this file never calls a network: it builds ``{instructions, text}`` and turns the JSON the model answers
into courses, lessons, cards and verdicts. Material from a document comes from ``app.bigdocs``: the outline sees the
section list and the beginning of the text; a lesson sees the chunks ``bigdocs.search`` finds for its title.

Tasks: ``outline`` (course tree), ``lesson`` (summary + cards of one lesson), ``exam`` (tickets), ``grade`` (check an
answer or code, mistakes, weak topics → cards).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .store import CLOZE_RE, LearnError, Store, clean

SOURCE_CHARS = 18000
LESSON_CHARS = 9000
CARD_RULES_RU = (
    "Карточки — атомарные: один факт или навык на карточку, вопрос однозначный, ответ короткий (до 2–3 строк). "
    "Типы: \"basic\" (front — вопрос, back — ответ) и \"cloze\" (front — фраза с пропусками {{c1::ответ}}, можно {{c2::…}}; back — пояснение или пусто). "
    "Формулы — KaTeX: $…$ в строке, $$…$$ отдельной строкой. Код — блоком ```язык\\n…\\n``` с переносами строк \\n. "
    "Смешивай типы: примерно треть cloze, задачи «что выведет код», определения, сравнения.")
CARD_RULES_EN = (
    "Cards are atomic: one fact or skill per card, an unambiguous question, a short answer (≤2–3 lines). "
    "Types: \"basic\" (front — question, back — answer) and \"cloze\" (front — a sentence with {{c1::answer}} deletions, {{c2::…}} allowed; back — a note or empty). "
    "Math is KaTeX: $…$ inline, $$…$$ on its own line. Code goes in a ```lang\\n…\\n``` block with \\n line breaks. "
    "Mix the types: about a third cloze, «what does this code print» tasks, definitions, comparisons.")

def _lang_line(lang: str) -> str:
    return "Answer in English." if lang == "en" else "Отвечай по-русски."

def _material(store: Store, course: Dict[str, Any], query: str = "", limit: int = SOURCE_CHARS) -> str:
    """Text of the course source for a prompt: a bigdocs document (sections + beginning, or chunks for ``query``)."""
    if course.get("source_kind") not in ("file", "note") or not course.get("source_ref"):
        return ""
    from .. import bigdocs
    doc = bigdocs.load_doc(course["source_ref"])
    if not doc:
        return ""
    if query:
        hits = bigdocs.search(doc, query, k=6)
        parts = []
        for hit in hits:
            text = str(hit.get("text") or "")
            if text:
                parts.append(text)
            if sum(len(p) for p in parts) > limit:
                break
        if parts:
            return "\n\n---\n\n".join(parts)[:limit]
    heads = [f"{'  ' * max(0, int(s.get('level') or 1) - 1)}- {s.get('title')}" for s in (doc.get("sections") or [])[:160]]
    body = bigdocs.full_text(doc, with_pages=False)
    head_text = ("Оглавление / Contents:\n" + "\n".join(heads) + "\n\n") if heads else ""
    return (head_text + body)[:limit]

def prompt(store: Store, task: str, args: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    en = lang == "en"
    if task == "outline":
        course = store.course(str(args.get("course_id") or ""))
        material = _material(store, course)
        depth = str(args.get("depth") or "normal")
        size = {"short": "3–4 модуля по 2–3 урока", "normal": "4–6 модулей по 3–4 урока", "deep": "6–9 модулей по 3–5 уроков"}.get(depth)
        size_en = {"short": "3–4 modules of 2–3 lessons", "normal": "4–6 modules of 3–4 lessons", "deep": "6–9 modules of 3–5 lessons"}.get(depth)
        level = clean(args.get("level"), 200)
        goal = clean(args.get("goal"), 600)
        instructions = (
            ("You are a methodologist designing a course. " if en else "Ты — методист, проектируешь учебный курс. ")
            + (f"Split the material into a tree: {size_en}, from basics to advanced; each lesson gets a 1–2 sentence brief of what it teaches. "
               if en else f"Разбей материал на дерево: {size}, от основ к сложному; у каждого урока — краткое описание в 1–2 предложения, чему он учит. ")
            + ('Reply with JSON only: {"title": "...", "description": "...", "modules": [{"title": "...", "lessons": [{"title": "...", "brief": "..."}]}]}. '
               if en else 'Ответь только JSON: {"title": "...", "description": "...", "modules": [{"title": "...", "lessons": [{"title": "...", "brief": "..."}]}]}. ')
            + _lang_line(lang))
        text = (f"Тема курса: {course['title']}\n" if not en else f"Course topic: {course['title']}\n")
        if course.get("description"):
            text += (f"Пожелания: {course['description']}\n" if not en else f"Notes: {course['description']}\n")
        if level:
            text += (f"Уровень ученика: {level}\n" if not en else f"Learner level: {level}\n")
        if goal:
            text += (f"Цель: {goal}\n" if not en else f"Goal: {goal}\n")
        if material:
            text += ("\nМатериал (курс строится строго по нему):\n" if not en else "\nSource material (build the course strictly from it):\n") + material
        return {"instructions": instructions, "text": text, "temperature": 0.4}

    if task == "lesson":
        node = store.node(str(args.get("lesson_id") or ""))
        course = store.course(node["course_id"])
        parent = store.one("SELECT title FROM nodes WHERE id=?", (node["parent_id"],)) if node["parent_id"] else None
        count = max(3, min(int(args.get("cards") or 8), 20))
        summary = (node.get("summary") or "").strip()
        if args.get("cards_only") and summary:

            instructions = (
                ("You are a teacher. Make flashcards from the lesson summary. " if en else "Ты — преподаватель. Составь карточки по конспекту урока. ")
                + (CARD_RULES_EN if en else CARD_RULES_RU)
                + (f' Exactly {count} cards. Reply with JSON only: {{"cards": [{{"type": "basic", "front": "...", "back": "..."}}]}}. '
                   if en else f' Ровно {count} карточек. Ответь только JSON: {{"cards": [{{"type": "basic", "front": "...", "back": "..."}}]}}. ')
                + _lang_line(lang))
            text = ((f"Курс: {course['title']}\nУрок: {node['title']}\n\nКонспект:\n" if not en
                     else f"Course: {course['title']}\nLesson: {node['title']}\n\nSummary:\n") + summary[:LESSON_CHARS])
            return {"instructions": instructions, "text": text, "temperature": 0.4}
        material = _material(store, course, node["title"] + " " + (node.get("brief") or ""), LESSON_CHARS)
        instructions = (
            ("You are a teacher. Write a lesson summary and flashcards. " if en else "Ты — преподаватель. Напиши конспект урока и карточки. ")
            + ("Summary: Markdown, 250–600 words, ## headings, short paragraphs, lists, a worked example; code in ``` blocks, math in KaTeX. "
               if en else "Конспект: Markdown, 250–600 слов, заголовки ##, короткие абзацы, списки, разобранный пример; код — в блоках ```, формулы — KaTeX. ")
            + (CARD_RULES_EN if en else CARD_RULES_RU)
            + (f' Exactly {count} cards. Reply with JSON only: {{"summary": "markdown", "cards": [{{"type": "basic", "front": "...", "back": "..."}}]}}. '
               if en else f' Ровно {count} карточек. Ответь только JSON: {{"summary": "markdown", "cards": [{{"type": "basic", "front": "...", "back": "..."}}]}}. ')
            + _lang_line(lang))
        text = (f"Курс: {course['title']}\n" + (f"Модуль: {parent['title']}\n" if parent else "") + f"Урок: {node['title']}\n"
                + (f"О чём урок: {node['brief']}\n" if node.get("brief") else "")) if not en else (
                f"Course: {course['title']}\n" + (f"Module: {parent['title']}\n" if parent else "") + f"Lesson: {node['title']}\n"
                + (f"About: {node['brief']}\n" if node.get("brief") else ""))
        if material:
            text += ("\nМатериал источника:\n" if not en else "\nSource material:\n") + material
        return {"instructions": instructions, "text": text, "temperature": 0.5}

    if task == "exam":
        course = store.course(str(args.get("course_id") or ""))
        count = max(1, min(int(args.get("count") or 5), 12))
        mode = str(args.get("mode") or "mixed")
        lessons = store.q("SELECT id, title, brief FROM nodes WHERE course_id=? AND kind='lesson' ORDER BY ord", (course["id"],))
        only = [x for x in (args.get("lesson_ids") or []) if isinstance(x, str)]
        if only:
            lessons = [x for x in lessons if x["id"] in only] or lessons
        kinds = {"theory": "только теоретические вопросы с развёрнутым ответом", "code": "только задачи на написание кода",
                 "mixed": "теория с развёрнутым ответом и задачи на код (если предмет допускает код)"}.get(mode, "")
        kinds_en = {"theory": "open theory questions only", "code": "coding tasks only",
                    "mixed": "open theory questions and coding tasks (when the subject allows code)"}.get(mode, "")
        instructions = (
            (f"You are an examiner. Make {count} exam tickets: {kinds_en}. Each ticket checks understanding, not memorised wording. "
             'For each give a rubric — the key points of a full answer. "kind" is "open" or "code"; "lesson_id" — the id of the lesson it checks. '
             'Reply with JSON only: {"questions": [{"kind": "open", "prompt": "markdown", "rubric": "...", "lesson_id": "..."}]}. '
             if en else
             f"Ты — экзаменатор. Составь {count} экзаменационных билетов: {kinds}. Билет проверяет понимание, а не заученные формулировки. "
             'Для каждого дай критерии (rubric) — ключевые пункты полного ответа. "kind" — "open" или "code"; "lesson_id" — id урока, который он проверяет. '
             'Ответь только JSON: {"questions": [{"kind": "open", "prompt": "markdown", "rubric": "...", "lesson_id": "..."}]}. ')
            + _lang_line(lang))
        text = (f"Курс: {course['title']}\nУроки:\n" if not en else f"Course: {course['title']}\nLessons:\n") + "\n".join(
            f"- id={x['id']}: {x['title']}" + (f" — {x['brief']}" if x.get("brief") else "") for x in lessons[:60])
        return {"instructions": instructions, "text": text, "temperature": 0.6}

    if task == "grade":
        exam = store.exam(str(args.get("exam_id") or ""))
        q = _question(exam, str(args.get("question_id") or ""))
        answer = clean(args.get("student_answer"), 20000)
        if not answer:
            raise LearnError("Ответ пустой", "The answer is empty")
        instructions = (
            ("You are a strict but kind examiner. Grade the student's answer against the rubric. For code: check correctness, edge cases, "
             "complexity and style; mentally run it on examples. score 0–100. List concrete mistakes (what is wrong → how it should be). "
             "weak_topics — short names of what to repeat. cards — 1–4 flashcards that fix exactly these gaps (none when the answer is perfect). "
             if en else
             "Ты — строгий, но доброжелательный экзаменатор. Оцени ответ студента по критериям. Для кода: проверь корректность, крайние случаи, "
             "сложность и стиль, мысленно прогони на примерах. score 0–100. Перечисли конкретные ошибки (что не так → как правильно). "
             "weak_topics — короткие названия того, что повторить. cards — 1–4 карточки, закрывающие именно эти пробелы (пусто, если ответ идеален). ")
            + (CARD_RULES_EN if en else CARD_RULES_RU)
            + (' Reply with JSON only: {"score": 0, "verdict": "one sentence", "feedback": "markdown", "mistakes": [{"what": "...", "fix": "..."}], '
               '"model_answer": "markdown", "weak_topics": ["..."], "cards": [{"type": "basic", "front": "...", "back": "..."}]}. ')
            + _lang_line(lang))
        text = (("Билет" if not en else "Ticket") + f" ({q.get('kind', 'open')}):\n{q.get('prompt', '')}\n\n"
                + ("Критерии" if not en else "Rubric") + f":\n{q.get('rubric', '')}\n\n"
                + ("Ответ студента" if not en else "Student's answer") + f":\n{answer}")
        return {"instructions": instructions, "text": text, "temperature": 0.2}
    raise LearnError("Неизвестная задача ИИ", "Unknown AI task")

def _question(exam: Dict[str, Any], qid: str) -> Dict[str, Any]:
    q = next((x for x in exam["questions"] if x.get("id") == qid), None)
    if q is None:
        raise LearnError("Билет не найден", "Ticket not found", "not_found")
    return q

def extract_json(text: str) -> Any:
    """The first JSON object in a model answer: fences, chatter and trailing commas are tolerated."""
    raw = str(text or "")
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    candidates = [fence.group(1)] if fence else []
    start = raw.find("{")
    if start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw[start:i + 1])
                    break
        else:
            candidates.append(raw[start:])
    for cand in candidates:
        for attempt in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                value = json.loads(attempt, strict=False)
                if isinstance(value, dict):
                    return value
            except ValueError:
                continue
    raise LearnError("Модель ответила не в том формате — попробуйте ещё раз или другую модель",
                     "The model replied in the wrong format — try again or another model", "bad_ai_answer")

def normalize_cards(items: Any, limit: int = 20) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        front, back = clean(item.get("front") or item.get("question") or item.get("q"), 8000), clean(item.get("back") or item.get("answer") or item.get("a"), 12000)
        kind = "cloze" if str(item.get("type") or item.get("kind") or "").lower() == "cloze" or CLOZE_RE.search(front) else "basic"
        if kind == "cloze" and not CLOZE_RE.search(front):
            kind = "basic"
        if not front or (kind == "basic" and not back):
            continue
        out.append({"kind": kind, "front": front, "back": back})
        if len(out) >= limit:
            break
    return out

def apply(store: Store, task: str, answer: str, args: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    data = extract_json(answer)
    if task == "outline":
        course = store.course(str(args.get("course_id") or ""))
        modules = [m for m in data.get("modules") or [] if isinstance(m, dict) and clean(m.get("title"), 300)]
        if not modules:
            raise LearnError("Модель не предложила ни одного модуля", "The model proposed no modules", "bad_ai_answer")
        if args.get("replace"):
            for row in store.q("SELECT id FROM nodes WHERE course_id=? AND parent_id=''", (course["id"],)):
                store.delete_node(row["id"])
        if clean(data.get("title"), 200) and args.get("rename", True):
            store.update_course(course["id"], {"title": data["title"]})
        if clean(data.get("description"), 2000):
            store.update_course(course["id"], {"description": data["description"]})
        for m in modules[:16]:
            mod = store.add_node(course["id"], m["title"], kind="module", brief=clean(m.get("brief"), 1200))
            for lesson in [x for x in m.get("lessons") or [] if isinstance(x, (dict, str))][:12]:
                title = lesson if isinstance(lesson, str) else lesson.get("title")
                if clean(title, 300):
                    store.add_node(course["id"], title, parent_id=mod["id"],
                                   brief="" if isinstance(lesson, str) else clean(lesson.get("brief") or lesson.get("summary"), 1200))
        return {"course": store.course(course["id"]), "tree": store.tree(course["id"])}

    if task == "lesson":
        node = store.node(str(args.get("lesson_id") or ""))
        summary = "" if args.get("cards_only") else clean(data.get("summary") or data.get("notes"), 60000)
        cards = normalize_cards(data.get("cards"))
        if not summary and not cards:
            raise LearnError("Модель ответила не в том формате — попробуйте ещё раз или другую модель",
                             "The model replied in the wrong format — try again or another model", "bad_ai_answer")
        if summary:
            store.update_node(node["id"], {"summary": summary})
        added = []
        for card in cards:
            added += store.add_note(node["course_id"], card["front"], card["back"], kind=card["kind"], lesson_id=node["id"], origin="ai")
        return {"lesson": store.node(node["id"]), "added": len(added)}

    if task == "exam":
        course = store.course(str(args.get("course_id") or ""))
        lesson_ids = {r["id"] for r in store.q("SELECT id FROM nodes WHERE course_id=?", (course["id"],))}
        questions = []
        for i, q in enumerate([x for x in data.get("questions") or [] if isinstance(x, dict)][:12]):
            text = clean(q.get("prompt") or q.get("question"), 6000)
            if not text:
                continue
            questions.append({"id": f"q{i + 1}", "kind": "code" if str(q.get("kind") or "") == "code" else "open", "prompt": text,
                              "rubric": clean(q.get("rubric"), 4000),
                              "lesson_id": q.get("lesson_id") if q.get("lesson_id") in lesson_ids else ""})
        if not questions:
            raise LearnError("Модель не составила билеты", "The model made no tickets", "bad_ai_answer")
        title = clean(args.get("title"), 200) or (("Экзамен · " if lang != "en" else "Exam · ") + course["title"])
        return {"exam": store.save_exam(course["id"], title, questions)}

    if task == "grade":
        exam = store.exam(str(args.get("exam_id") or ""))
        q = _question(exam, str(args.get("question_id") or ""))
        try:
            score = float(data.get("score"))
        except (TypeError, ValueError):
            score = 0.0
        mistakes = [{"what": clean(m.get("what"), 1000), "fix": clean(m.get("fix"), 1500)} for m in data.get("mistakes") or []
                    if isinstance(m, dict) and clean(m.get("what"), 1000)][:12]
        weak = [clean(x, 120) for x in data.get("weak_topics") or [] if clean(x, 120)][:8]
        result: Dict[str, Any] = {"score": max(0.0, min(100.0, score)), "verdict": clean(data.get("verdict"), 400),
                                  "feedback": clean(data.get("feedback"), 12000), "mistakes": mistakes,
                                  "model_answer": clean(data.get("model_answer"), 12000), "weak_topics": weak, "cards": 0}
        if args.get("make_cards", True):
            tags = " ".join("weak:" + re.sub(r"\s+", "_", w)[:40] for w in weak[:3])
            for card in normalize_cards(data.get("cards"), 4):
                result["cards"] += len(store.add_note(exam["course_id"], card["front"], card["back"], kind=card["kind"],
                                                      lesson_id=q.get("lesson_id") or "", tags=tags, origin="exam"))
        return {"exam": store.save_result(exam["id"], q["id"], str(args.get("student_answer") or ""), result), "result": result}
    raise LearnError("Неизвестная задача ИИ", "Unknown AI task")
