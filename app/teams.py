"""Profile-local team definitions; model IDs always come from the user's catalog.

Templates describe jobs, never fictitious models. A team is copied into a run so
editing its definition cannot change work that is already in progress.
"""
from __future__ import annotations

import copy
import re
import time
import uuid
from typing import Any

from . import pgdata
from .storage import load_json_with_recovery, mutate_json

MAX_TEAMS = 80
MAX_MEMBERS = 6
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
DEFAULT_BUDGET = {"max_calls": 1200, "max_parallel": 3, "max_rounds": 300,
                  "context_chars": 48000, "output_tokens": 6000}
ECONOMY_CAPS = {"context_chars": 32000, "output_tokens": 4000}
# Visual identity of a team: an icon from the app's SVG set and a palette accent.
TEAM_ICONS = ("users", "code", "search", "sparkle", "chart", "shield", "book", "globe", "target", "brain",
              "research", "terminal", "palette", "vote", "crown", "zap", "message", "flag", "database", "text")
TEAM_COLORS = ("", "coral", "blue", "green", "violet", "amber", "teal", "pink", "slate")
MAX_STARTERS = 6
STARTER_CHARS = 300

def _m(name: str, name_en: str, role: str, role_en: str, instructions: str = "", instructions_en: str = "",
       lead: bool = False) -> dict:
    row = {"name": name, "name_en": name_en, "role": role, "role_en": role_en}
    if instructions:
        row.update(instructions=instructions, instructions_en=instructions_en or instructions)
    if lead:
        row["is_lead"] = True
    return row

# Template categories shown as filter chips on the Team page.
PRESET_CATEGORIES = [
    {"id": "dev", "name": "Разработка", "name_en": "Development"},
    {"id": "research", "name": "Исследования", "name_en": "Research"},
    {"id": "content", "name": "Тексты", "name_en": "Writing"},
    {"id": "decide", "name": "Решения", "name_en": "Decisions"},
    {"id": "learn", "name": "Обучение", "name_en": "Learning"},
]

PRESETS = [
    {"id": "feature", "name": "Новая функция", "name_en": "New feature", "icon": "code", "category": "dev",
     "description": "Создание и проверка функции в готовом проекте.",
     "description_en": "Build and verify a feature in an existing project.",
     "starters": ["Добавь в проект экспорт данных в CSV и покрой его тестами",
                  "Сделай тёмную тему для страницы настроек",
                  "Спроектируй и реализуй API для заметок"],
     "starters_en": ["Add CSV export to the project and cover it with tests",
                     "Build a dark theme for the settings page",
                     "Design and implement an API for notes"],
     "members": [
         _m("Тимлид", "Team lead", "Архитектура, интеграция и итоговая проверка", "Architecture, integration and final review",
            "Разбей задачу на части, следи за совместимостью и проверь итог целиком.",
            "Split the work, keep the parts compatible and review the whole result.", lead=True),
         _m("Интерфейс", "Frontend", "Интерфейс, взаимодействие и доступность", "Frontend, interaction and accessibility"),
         _m("Логика", "Backend", "Серверная логика, данные и надёжность", "Backend, data and reliability"),
         _m("Проверка", "QA", "Воспроизведение, тесты и проверка требований", "QA: reproduce, test and verify acceptance",
            "Проверь каждое требование, перечисли найденные ошибки и шаги их воспроизведения.",
            "Check every requirement; list defects with steps to reproduce.")]},
    {"id": "review", "name": "Ревью кода", "name_en": "Code review", "icon": "shield", "category": "dev",
     "description": "Независимые взгляды на безопасность, скорость и качество кода.",
     "description_en": "Independent views on security, performance and code quality.",
     "starters": ["Проведи ревью изменений в этой папке перед релизом",
                  "Проверь модуль авторизации на уязвимости",
                  "Найди узкие места производительности в обработке файлов"],
     "starters_en": ["Review the changes in this folder before the release",
                     "Audit the authentication module for vulnerabilities",
                     "Find performance bottlenecks in the file processing"],
     "members": [
         _m("Ведущий ревьюер", "Lead reviewer", "Сводит замечания, расставляет приоритеты и выносит решение",
            "Merges findings, sets priorities and gives the verdict",
            "Итог — таблица замечаний: важность, файл, суть, предложение.",
            "Deliver a findings table: severity, file, issue, suggestion.", lead=True),
         _m("Безопасность", "Security", "Уязвимости, секреты и проверка входных данных",
            "Vulnerabilities, secrets and input validation"),
         _m("Производительность", "Performance", "Сложность, лишние запросы и утечки ресурсов",
            "Complexity, redundant calls and resource leaks"),
         _m("Тесты и стиль", "Tests and style", "Покрытие тестами, читаемость и соглашения проекта",
            "Test coverage, readability and project conventions")]},
    {"id": "bughunt", "name": "Поиск ошибки", "name_en": "Bug hunt", "icon": "target", "category": "dev",
     "description": "От симптома до причины, исправления и проверки на регрессии.",
     "description_en": "From the symptom to the root cause, a fix and a regression check.",
     "starters": ["Приложение иногда падает при сохранении — найди причину",
                  "Тесты проходят локально, но падают в CI — разберись",
                  "После обновления сломался импорт — найди и исправь"],
     "starters_en": ["The app sometimes crashes on save — find the cause",
                     "Tests pass locally but fail in CI — investigate",
                     "Import broke after the update — find and fix it"],
     "members": [
         _m("Детектив", "Detective", "Гипотезы, план поиска и итоговый вывод о причине",
            "Hypotheses, the search plan and the root-cause conclusion", lead=True),
         _m("Воспроизведение", "Reproduction", "Минимальный пример и точные шаги воспроизведения",
            "A minimal example and exact reproduction steps"),
         _m("Исправление", "Fix", "Исправление причины, а не симптома", "Fixing the cause, not the symptom"),
         _m("Регрессия", "Regression", "Проверка, что исправление ничего не сломало",
            "Checking that the fix breaks nothing else")]},
    {"id": "research", "name": "Исследование", "name_en": "Research", "icon": "search", "category": "research",
     "description": "Проверка источников, сравнение вариантов и ясные выводы.",
     "description_en": "Check sources, compare alternatives and draw clear conclusions.",
     "starters": ["Сравни три популярных подхода к хранению заметок и посоветуй один",
                  "Что известно о влиянии сна на память? Только проверенные источники",
                  "Собери обзор рынка умных колонок за последний год"],
     "starters_en": ["Compare three popular approaches to storing notes and recommend one",
                     "What is known about sleep and memory? Verified sources only",
                     "Write an overview of the smart speaker market for the last year"],
     "members": [
         _m("Аналитик", "Analyst", "План исследования, обобщение и проверка доказательств",
            "Lead: research plan, synthesis and evidence review", lead=True),
         _m("Исследователь", "Researcher", "Поиск и сравнение первоисточников", "Find and compare primary sources",
            "Для каждого факта укажи источник и дату.", "Cite a source and date for every claim."),
         _m("Критик", "Reviewer", "Независимая проверка фактов и предположений",
            "Independently verify claims and challenge assumptions")]},
    {"id": "data", "name": "Анализ данных", "name_en": "Data analysis", "icon": "chart", "category": "research",
     "description": "Вопрос к данным, расчёты, наглядные таблицы и честные ограничения.",
     "description_en": "A question for the data, calculations, clear tables and honest limits.",
     "starters": ["Проанализируй таблицу продаж и найди сезонность",
                  "Сравни результаты двух вариантов A/B-теста",
                  "Какие факторы сильнее всего влияют на отток клиентов?"],
     "starters_en": ["Analyse the sales table and find seasonality",
                     "Compare the results of the two A/B test variants",
                     "Which factors affect customer churn the most?"],
     "members": [
         _m("Аналитик данных", "Data analyst", "Формулирует вопрос, план анализа и выводы",
            "Frames the question, the analysis plan and conclusions", lead=True),
         _m("Статистик", "Statistician", "Расчёты, проверки значимости и ограничения данных",
            "Calculations, significance checks and data limitations"),
         _m("Визуализация", "Visualisation", "Таблицы и графики, которые отвечают на вопрос",
            "Tables and charts that answer the question"),
         _m("Скептик", "Sceptic", "Ищет смещения, ошибки и альтернативные объяснения",
            "Looks for bias, errors and alternative explanations")]},
    {"id": "creative", "name": "Творческая студия", "name_en": "Creative studio", "icon": "sparkle", "category": "content",
     "description": "От идеи и нескольких подходов до готового результата.",
     "description_en": "Explore directions and develop a finished creative result.",
     "starters": ["Придумай три концепции рассказа о космическом маяке и напиши лучший",
                  "Сочини слоган и описание для кофейни у моря",
                  "Напиши сценарий короткого ролика о пользе прогулок"],
     "starters_en": ["Come up with three story concepts about a space lighthouse and write the best one",
                     "Write a slogan and description for a seaside coffee shop",
                     "Write a script for a short video about the benefits of walks"],
     "members": [
         _m("Редактор", "Editor", "Творческое направление, целостность и редактура", "Lead: creative direction, cohesion and final editing", lead=True),
         _m("Автор", "Writer", "Разработка разных концепций и создание материала", "Develop distinct concepts and write the deliverable"),
         _m("Рецензент", "Critic", "Проверка ясности, оригинальности и соответствия задаче", "Check clarity, originality and fit to the brief")]},
    {"id": "content", "name": "Контент и маркетинг", "name_en": "Content & marketing", "icon": "message", "category": "content",
     "description": "Контент-план, тексты под каналы, SEO и вычитка в одном потоке.",
     "description_en": "A content plan, channel-ready copy, SEO and proofreading in one flow.",
     "starters": ["Составь контент-план на месяц для блога о путешествиях",
                  "Напиши серию из трёх писем для новых подписчиков",
                  "Подготовь статью о преимуществах удалённой работы под SEO"],
     "starters_en": ["Draft a one-month content plan for a travel blog",
                     "Write a three-email welcome series for new subscribers",
                     "Prepare an SEO article on the benefits of remote work"],
     "members": [
         _m("Контент-стратег", "Content strategist", "Аудитория, цель, структура и единый голос бренда",
            "Audience, goal, structure and one brand voice", lead=True),
         _m("Копирайтер", "Copywriter", "Заголовки, посты, письма и призывы к действию",
            "Headlines, posts, emails and calls to action"),
         _m("SEO-редактор", "SEO editor", "Ключевые запросы, структура и метаданные",
            "Keywords, structure and metadata"),
         _m("Корректор", "Proofreader", "Грамотность, факты и единый стиль", "Grammar, facts and a consistent style")]},
    {"id": "translate", "name": "Перевод и локализация", "name_en": "Translation & localization", "icon": "globe", "category": "content",
     "description": "Точный перевод, естественный язык и единая терминология.",
     "description_en": "Accurate translation, natural language and consistent terminology.",
     "starters": ["Переведи описание приложения на английский и адаптируй для США",
                  "Локализуй интерфейсные строки на немецкий с глоссарием",
                  "Проверь перевод договора на точность терминов"],
     "starters_en": ["Translate the app description into Russian and adapt it for the market",
                     "Localise the interface strings into German with a glossary",
                     "Check a contract translation for accurate terminology"],
     "members": [
         _m("Ведущий переводчик", "Lead translator", "Единая терминология и финальная редактура",
            "Consistent terminology and the final edit", lead=True),
         _m("Переводчик", "Translator", "Точный перевод с сохранением смысла и тона",
            "An accurate translation that keeps meaning and tone"),
         _m("Носитель языка", "Native speaker", "Естественность текста и культурные особенности",
            "Natural wording and cultural nuances"),
         _m("Терминолог", "Terminologist", "Глоссарий и единообразие терминов", "The glossary and consistent terms")]},
    {"id": "council", "name": "Совет экспертов", "name_en": "Expert council", "icon": "vote", "category": "decide",
     "description": "Аргументы «за» и «против», риски и взвешенное решение.",
     "description_en": "Arguments for and against, risks and a balanced decision.",
     "starters": ["Стоит ли переходить с Windows на Linux для работы?",
                  "Арендовать квартиру или брать ипотеку в моей ситуации?",
                  "Какой язык программирования выбрать для первого проекта?"],
     "starters_en": ["Should I switch from Windows to Linux for work?",
                     "Rent or take a mortgage in my situation?",
                     "Which programming language should I pick for a first project?"],
     "members": [
         _m("Модератор", "Moderator", "Формулирует вопрос, ведёт обсуждение и выносит решение",
            "Frames the question, runs the discussion and decides",
            "Итог — решение, главные доводы обеих сторон и условия, при которых решение поменяется.",
            "Deliver the decision, the key arguments of both sides and what would change it.", lead=True),
         _m("Сторонник", "Advocate", "Сильнейшие аргументы «за»", "The strongest arguments for"),
         _m("Скептик", "Sceptic", "Сильнейшие аргументы «против» и риски", "The strongest arguments against and the risks"),
         _m("Практик", "Pragmatist", "Стоимость, сроки и как это сделать на деле", "Cost, timing and how to actually do it")]},
    {"id": "business", "name": "Бизнес-план", "name_en": "Business plan", "icon": "flag", "category": "decide",
     "description": "Рынок, экономика, риски и план запуска идеи.",
     "description_en": "The market, unit economics, risks and a launch plan for an idea.",
     "starters": ["Оцени идею небольшой пекарни в спальном районе",
                  "Составь план запуска мобильного приложения для бега",
                  "Посчитай юнит-экономику подписки на доставку цветов"],
     "starters_en": ["Evaluate the idea of a small bakery in a residential area",
                     "Draft a launch plan for a running app",
                     "Work out the unit economics of a flower delivery subscription"],
     "members": [
         _m("Стратег", "Strategist", "Ценность, позиционирование и план запуска", "Value, positioning and the launch plan", lead=True),
         _m("Финансист", "Finance", "Юнит-экономика, расходы и прогноз", "Unit economics, costs and the forecast",
            "Покажи расчёты таблицей и явно отметь допущения.", "Show calculations as a table and state the assumptions."),
         _m("Маркетолог", "Marketing", "Рынок, конкуренты и каналы привлечения", "Market, competitors and acquisition channels"),
         _m("Аналитик рисков", "Risk analyst", "Операционные, финансовые и правовые риски (без замены юриста)",
            "Operational, financial and legal risks (not a substitute for a lawyer)")]},
    {"id": "tutor", "name": "Учебная группа", "name_en": "Study group", "icon": "book", "category": "learn",
     "description": "Понятное объяснение темы, примеры и проверка понимания.",
     "description_en": "A clear explanation of a topic, examples and a knowledge check.",
     "starters": ["Объясни основы квантовых вычислений с примерами",
                  "Подготовь меня к собеседованию по SQL",
                  "Разбери тему производных и дай 5 задач с решениями"],
     "starters_en": ["Explain the basics of quantum computing with examples",
                     "Prepare me for an SQL interview",
                     "Explain derivatives and give 5 problems with solutions"],
     "members": [
         _m("Методист", "Curriculum lead", "План обучения и проверка понимания", "The learning plan and understanding checks", lead=True),
         _m("Объясняющий", "Explainer", "Простые объяснения, аналогии и примеры", "Simple explanations, analogies and examples"),
         _m("Экзаменатор", "Examiner", "Вопросы, задачи и разбор типичных ошибок", "Questions, exercises and common mistakes")]},
]

def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]

def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default

def normalize(raw: Any, *, strict: bool = True) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Команда должна быть объектом / A team must be an object")
    if strict and len(str(raw.get("id") or "").strip()) > 80:
        raise ValueError("ID команды длиннее 80 символов / Team ID exceeds 80 characters")
    team_id = _text(raw.get("id"), 80) or "team_" + uuid.uuid4().hex[:16]
    if not _ID.fullmatch(team_id):
        raise ValueError("Некорректный ID команды / Invalid team ID")
    name = _text(raw.get("name"), 100)
    manager_model = _text(raw.get("manager_model"), 300)
    members = []
    used = {"manager"}
    source_members = raw.get("members")
    if strict and source_members is not None and not isinstance(source_members, list):
        raise ValueError("Участники должны быть списком / Members must be a list")
    if strict and isinstance(source_members, list) and len(source_members) > MAX_MEMBERS:
        raise ValueError(f"В команде не больше {MAX_MEMBERS} участников / At most {MAX_MEMBERS} members are allowed")
    for source in (source_members if isinstance(source_members, list) else [])[:MAX_MEMBERS]:
        if not isinstance(source, dict):
            if strict:
                raise ValueError("Участник должен быть объектом / A member must be an object")
            continue
        if strict and len(str(source.get("id") or "").strip()) > 80:
            raise ValueError("ID участника длиннее 80 символов / Member ID exceeds 80 characters")
        member_id = _text(source.get("id"), 80) or "member_" + uuid.uuid4().hex[:12]
        if not _ID.fullmatch(member_id) or member_id in used:
            raise ValueError("ID участников должны быть уникальны / Member IDs must be unique")
        used.add(member_id)
        member = {"id": member_id, "name": _text(source.get("name"), 80),
                  "model": _text(source.get("model"), 300),
                  "role": _text(source.get("role"), 1000),
                  "instructions": _text(source.get("instructions"), 8000),
                  "is_lead": source.get("is_lead") is True}
        if strict and (not member["model"] or not member["role"]):
            raise ValueError("Для каждого участника нужны модель и роль / Every member needs a model and role")
        member["name"] = member["name"] or member["role"][:60] or "Участник"
        members.append(member)
    if strict and (not name or not manager_model):
        raise ValueError("Нужны название и модель менеджера / Name and manager model are required")
    if sum(member["is_lead"] for member in members) > 1:
        raise ValueError("Назначьте одного тимлида / Select one team lead")
    budget = raw.get("budget") if isinstance(raw.get("budget"), dict) else {}
    icon = _text(raw.get("icon"), 40)
    color = _text(raw.get("color"), 20)
    starters: list[str] = []
    for item in (raw.get("starters") if isinstance(raw.get("starters"), list) else [])[:MAX_STARTERS * 2]:
        line = " ".join(str(item or "").split())[:STARTER_CHARS]
        if line and line not in starters:
            starters.append(line)
    return {"id": team_id, "name": name, "description": _text(raw.get("description"), 3000),
            "icon": icon if icon in TEAM_ICONS else "users", "color": color if color in TEAM_COLORS else "",
            "pinned": raw.get("pinned") is True, "starters": starters[:MAX_STARTERS],
            "run_mode": "economy" if raw.get("run_mode") == "economy" else "standard",
            "manager_approvals": raw.get("manager_approvals", True) is True,
            "manager_model": manager_model, "manager_instructions": _text(raw.get("manager_instructions"), 8000),
            "members": members, "budget": {
                "max_calls": _int(budget.get("max_calls"), 1200, 8, 10000),
                "max_parallel": _int(budget.get("max_parallel"), 3, 1, 4),
                "max_rounds": _int(budget.get("max_rounds"), 300, 3, 2000),
                "context_chars": _int(budget.get("context_chars"), 48000, 16000, 160000),
                "output_tokens": _int(budget.get("output_tokens"), 6000, 1000, 16000)},
            "created": _int(raw.get("created"), int(time.time() * 1000), 0, 10**15),
            "updated": int(time.time() * 1000) if strict else _int(raw.get("updated"), 0, 0, 10**15)}

def _path(profile_id: str = ""):
    return pgdata.MEMU_DIR / pgdata._profile_data_name("teams.json", _active_profile_id(profile_id))

def load(profile_id: str = "") -> list[dict]:
    rows = load_json_with_recovery(_path(profile_id), [], validator=lambda value: isinstance(value, list))
    result = []
    for row in rows[:MAX_TEAMS]:
        try:
            result.append(normalize(row, strict=False))
        except ValueError:
            continue
    return result

def save(raw: dict, profile_id: str) -> dict:
    team = normalize(raw)
    def update(rows):
        clean = [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]
        for idx, row in enumerate(clean):
            if row.get("id") == team["id"]:
                if "run_mode" not in raw and row.get("run_mode") == "economy":
                    team["run_mode"] = "economy"
                clean[idx] = team
                return True, clean
        if len(clean) >= MAX_TEAMS:
            raise ValueError("Достигнут лимит команд / Team limit reached")
        return True, clean + [team]
    mutate_json(_path(profile_id), [], update, validator=lambda value: isinstance(value, list), repair_invalid=True)
    return copy.deepcopy(team)

def delete(team_id: str, profile_id: str) -> None:
    def update(rows):
        rows = rows if isinstance(rows, list) else []
        return True, [row for row in rows if isinstance(row, dict) and row.get("id") != team_id]
    mutate_json(_path(profile_id), [], update, validator=lambda value: isinstance(value, list), repair_invalid=True)

def presets() -> list[dict]:
    return copy.deepcopy(PRESETS)

def preset_categories() -> list[dict]:
    return copy.deepcopy(PRESET_CATEGORIES)

def _active_profile_id(profile_id: str = "") -> str:
    if profile_id:
        return str(profile_id)
    try:
        return str(pgdata.load_profiles().get("active_id") or "default")
    except Exception:
        return "default"

def list_teams(profile_id: str = "") -> list[dict]:
    return load(_active_profile_id(profile_id))

def get_team(team_id: str, profile_id: str = "") -> dict | None:
    target = str(team_id or "").strip()
    if not target:
        return None
    for row in list_teams(profile_id):
        if str(row.get("id") or "") == target or str(row.get("name") or "").lower() == target.lower():
            return row
    return None

def _runs_path(profile_id: str = ""):
    return pgdata.MEMU_DIR / pgdata._profile_data_name("team-runs.json", _active_profile_id(profile_id))

def save_run(snapshot: dict, profile_id: str = "") -> None:
    if not isinstance(snapshot, dict) or not snapshot.get("id"):
        return
    path = _runs_path(profile_id)
    def apply(raw):
        rows = raw if isinstance(raw, list) else []
        rows = [row for row in rows if isinstance(row, dict) and row.get("id") != snapshot["id"]]
        return True, (rows + [snapshot])[-40:]
    mutate_json(path, [], apply, backup=False, validator=lambda value: isinstance(value, list), repair_invalid=True)


# ---------------------------------------------------------------------------
# Run history: compact summaries and per-team statistics for the Team page.
# The saved snapshots hold activity only (no prompts or private memory).
# ---------------------------------------------------------------------------

_DONE = ("completed", "completed_with_warnings", "done")

def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

def _tokens(usage: Any) -> int:
    usage = usage if isinstance(usage, dict) else {}
    total = _num(usage.get("total_tokens"))
    if not total:
        total = _num(usage.get("prompt_tokens")) + _num(usage.get("completion_tokens"))
    if not total:
        total = _num(usage.get("estimated_prompt_tokens")) + _num(usage.get("estimated_completion_tokens"))
    return int(total)

def run_summary(row: dict) -> dict:
    """A saved run without its event log and reports — for history lists."""
    tasks = [task for task in row.get("tasks") or [] if isinstance(task, dict)]
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    started, finished = int(_num(row.get("started"))), int(_num(row.get("finished")))
    return {
        "id": str(row.get("id") or ""), "team_id": str(row.get("team_id") or ""),
        "team_name": str(row.get("team_name") or "")[:100], "status": str(row.get("status") or ""),
        "phase": str(row.get("phase") or ""), "mode": str(row.get("mode") or "chat"),
        "run_mode": "economy" if row.get("run_mode") == "economy" else "standard",
        "source": str(row.get("source") or "chat"), "started": started, "finished": finished,
        "duration_ms": max(0, finished - started) if finished and started else 0,
        "calls": int(_num(row.get("calls"))), "max_calls": int(_num(row.get("max_calls"))),
        "tool_calls": int(_num(row.get("tool_calls"))), "tokens": _tokens(usage),
        "estimated": bool(_num(usage.get("estimated_calls"))), "cost": round(_num(usage.get("cost")), 6),
        "tasks": len(tasks), "tasks_done": sum(1 for task in tasks if task.get("status") in _DONE),
        "tasks_failed": sum(1 for task in tasks if task.get("status") in ("error", "failed")),
        "titles": [str(task.get("title") or "")[:120] for task in tasks[:4] if task.get("title")],
        "members": len([m for m in row.get("members") or [] if isinstance(m, dict) and m.get("id") != "manager"]),
    }

def load_runs(profile_id: str = "") -> list[dict]:
    rows = load_json_with_recovery(_runs_path(profile_id), [], validator=lambda value: isinstance(value, list))
    return [row for row in rows if isinstance(row, dict) and row.get("id")]

def run_history(profile_id: str = "", team_id: str = "", limit: int = 40) -> list[dict]:
    rows = [run_summary(row) for row in load_runs(profile_id)]
    if team_id:
        rows = [row for row in rows if row["team_id"] == team_id]
    rows.sort(key=lambda row: row["started"], reverse=True)
    return rows[:max(1, min(200, int(limit or 40)))]

def run_stats(profile_id: str = "") -> dict:
    """``{team_id: {runs, completed, failed, last_at, last_status, calls, tokens, avg_ms}}``."""
    stats: dict[str, dict] = {}
    for row in run_history(profile_id, limit=200):
        item = stats.setdefault(row["team_id"], {"runs": 0, "completed": 0, "failed": 0, "last_at": 0, "last_status": "",
                                                 "calls": 0, "tokens": 0, "avg_ms": 0, "_ms": 0, "_timed": 0})
        item["runs"] += 1
        if row["status"] in _DONE:
            item["completed"] += 1
        elif row["status"] in ("error", "failed", "limited", "interrupted"):
            item["failed"] += 1
        if row["started"] > item["last_at"]:
            item["last_at"], item["last_status"] = row["started"], row["status"]
        item["calls"] += row["calls"]
        item["tokens"] += row["tokens"]
        if row["duration_ms"]:
            item["_ms"] += row["duration_ms"]
            item["_timed"] += 1
    for item in stats.values():
        item["avg_ms"] = int(item.pop("_ms") / item["_timed"]) if item["_timed"] else 0
        item.pop("_timed", None)
    return stats

def delete_run(run_id: str, profile_id: str = "", team_id: str = "") -> int:
    """Remove one run (``run_id``) or every run of a team (``team_id``); returns how many were removed."""
    removed = [0]
    def apply(raw):
        rows = raw if isinstance(raw, list) else []
        keep = [row for row in rows if isinstance(row, dict)
                and not ((run_id and row.get("id") == run_id) or (team_id and row.get("team_id") == team_id))]
        removed[0] = len(rows) - len(keep)
        return bool(removed[0]), keep
    mutate_json(_runs_path(profile_id), [], apply, backup=False, validator=lambda value: isinstance(value, list),
                repair_invalid=True)
    return removed[0]
