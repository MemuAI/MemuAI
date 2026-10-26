"""Имена моделей Antigravity: как их называет Google и как их видит пользователь.

Google кодирует уровень размышлений прямо в id модели, но непоследовательно:

* ``gemini-pro-agent`` — это «Gemini 3.1 Pro (High)», а прежний ``gemini-3.1-pro-high``
  объявлен устаревшим: по id уровень не угадать;
* у Claude Opus id заканчивается на ``-thinking``, а у Sonnet нет — хотя обе
  модели в Antigravity называются «(Thinking)»;
* «tiered»-модели (Gemini 3.7 / 3.8 Flash) названия не имеют вовсе.

Каталог Proxy показывает единые публичные имена (``gemini-3.5-flash-high``,
``claude-sonnet-4-6-thinking`` …), а при запросе они переводятся обратно в id
Google. Старые id (в том числе id из найденных сервером списков) по-прежнему
принимаются. Модуль не ходит в сеть: только таблицы и чистые функции.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

LEVELS = ("high", "medium", "low")

class Model(NamedTuple):
    public: str
    upstream: str
    note: str
    seed: bool
    level: str = ""

CURATED: Tuple[Model, ...] = (
    Model("gemini-3.6-flash-high", "gemini-3.6-flash-high", "Gemini 3.6 Flash (High)", True),
    Model("gemini-3.6-flash-medium", "gemini-3.6-flash-medium", "Gemini 3.6 Flash (Medium)", True),
    Model("gemini-3.6-flash-low", "gemini-3.6-flash-low", "Gemini 3.6 Flash (Low)", True),
    Model("gemini-3.7-flash", "gemini-3.7-flash-tiered", "Gemini 3.7 Flash (auto thinking)", True),
    Model("gemini-3.7-flash-high", "gemini-3.7-flash-tiered", "Gemini 3.7 Flash (High)", True, "high"),
    Model("gemini-3.7-flash-medium", "gemini-3.7-flash-tiered", "Gemini 3.7 Flash (Medium)", True, "medium"),
    Model("gemini-3.7-flash-low", "gemini-3.7-flash-tiered", "Gemini 3.7 Flash (Low)", True, "low"),
    Model("gemini-3.8-flash", "gemini-3.8-flash-tiered", "Gemini 3.8 Flash (auto thinking)", True),
    Model("gemini-3.8-flash-high", "gemini-3.8-flash-tiered", "Gemini 3.8 Flash (High)", True, "high"),
    Model("gemini-3.8-flash-medium", "gemini-3.8-flash-tiered", "Gemini 3.8 Flash (Medium)", True, "medium"),
    Model("gemini-3.8-flash-low", "gemini-3.8-flash-tiered", "Gemini 3.8 Flash (Low)", True, "low"),
    Model("gemini-3.1-pro-high", "gemini-pro-agent", "Gemini 3.1 Pro (High)", True),
    Model("gemini-3.1-pro-low", "gemini-3.1-pro-low", "Gemini 3.1 Pro (Low)", True),
    Model("gemini-3-flash", "gemini-3-flash", "Gemini 3 Flash", True),
    Model("gemini-3.5-flash-lite", "gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite", False),
    Model("gemini-3.1-flash-lite", "gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite", False),
    Model("gemini-2.5-pro", "gemini-2.5-pro", "Gemini 2.5 Pro", False),
    Model("claude-opus-4-6-thinking", "claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)", True),
    Model("claude-sonnet-4-6-thinking", "claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)", True),
    Model("gpt-oss-120b-medium", "gpt-oss-120b-medium", "GPT-OSS 120B (Medium)", True),
)

HIDDEN_UPSTREAM = frozenset({
    "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash-thinking",
    "gemini-3.6-flash-tiered", "gemini-3.1-flash-image",
})

RETIRED_UPSTREAM = frozenset({"gemini-3-flash-agent", "gemini-3.5-flash-low", "gemini-3.5-flash-extra-low"})

THINKING_UPSTREAM = frozenset({"claude-opus-4-6-thinking", "claude-sonnet-4-6"})

DEPRECATED: Dict[str, str] = {"gemini-3.1-pro-high": "gemini-pro-agent"}

_UP_TO_PUBLIC: Dict[str, str] = {m.upstream: m.public for m in CURATED if m.upstream != m.public and not m.level}
_PUBLIC_TO_UP: Dict[str, str] = {m.public: m.upstream for m in CURATED if m.upstream != m.public}
_NOTES: Dict[str, str] = {m.public: m.note for m in CURATED}

_REQUEST_LEVEL: Dict[str, str] = {m.public: m.level for m in CURATED if m.level}

TIERED_UPSTREAM = frozenset(m.upstream for m in CURATED if m.level)

EFFORT_LEVELS: Dict[str, str] = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "max": "high"}

_learned_public_to_up: Dict[str, str] = {}
_learned_up_to_public: Dict[str, str] = {}
_learned_deprecated: Dict[str, str] = {}
_learned_thinking: set = set()
_learned_retired: set = set()
_learned_current: set = set()

_LEVEL_TAIL = re.compile(r"\s*\((high|medium|low|thinking)\)\s*$", re.I)

_RETIRED_TEXT = re.compile(r"\bis no longer available\b.*\b(switch|update|upgrade|use)\b", re.I | re.S)

def reset_learned() -> None:
    """Для тестов: забыть всё, что было выучено у сервера."""
    _learned_public_to_up.clear()
    _learned_up_to_public.clear()
    _learned_deprecated.clear()
    _learned_thinking.clear()
    _learned_retired.clear()
    _learned_current.clear()

def is_retired_text(text: str) -> bool:
    """Это готовая фраза «модель снята», а не ответ модели (короткий первый кусок потока)."""
    value = str(text or "")
    return len(value) < 600 and bool(_RETIRED_TEXT.search(value))

def mark_retired(upstream: str) -> None:
    """Запомнить: сервер сказал, что эта модель снята — список моделей больше её не предложит."""
    if upstream:
        _learned_retired.add(str(upstream))

def is_retired(upstream: str) -> bool:
    return str(upstream) in RETIRED_UPSTREAM or str(upstream) in _learned_retired

def is_internal(name: str) -> bool:
    """chat_*, tab_* и подобные — служебные модели IDE, не для чата."""
    return str(name or "").lower().startswith(("chat_", "tab_", "default", "ghost"))

def slug(text: str) -> str:
    """«Gemini 3.5 Flash Lite» → ``gemini-3.5-flash-lite`` (у Claude версия через дефис)."""
    value = re.sub(r"[^a-z0-9.]+", "-", str(text or "").lower()).strip("-")
    if value.startswith("claude"):
        value = value.replace(".", "-")
    return re.sub(r"-{2,}", "-", value)

def renamed(upstream: str) -> str:
    """Актуальный id Google вместо устаревшего (цепочка переименований без циклов)."""
    seen = set()
    name = str(upstream or "")
    while name not in seen:
        seen.add(name)

        if name in _learned_current and name not in _learned_deprecated:
            break
        nxt = _learned_deprecated.get(name) or DEPRECATED.get(name)
        if not nxt:
            break
        name = nxt
    return name

def upstream_id(model: str) -> str:
    """Любое имя (публичное, id Google, устаревшее) → id, который принимает Google."""
    name = str(model or "")
    mapped = _learned_public_to_up.get(name) or _PUBLIC_TO_UP.get(name)
    return renamed(mapped or name)

def public_id(upstream: str, display: str = "") -> str:
    """id Google → имя в каталоге. Без названия от сервера — по таблице, иначе как есть."""
    name = str(upstream or "")
    known = _learned_up_to_public.get(name) or _UP_TO_PUBLIC.get(name)
    if known:
        return known
    match = _LEVEL_TAIL.search(display or "")
    if not match:
        return name
    level = match.group(1).lower()
    if name.lower().endswith("-" + level):
        return name
    base = slug(display[:match.start()])
    return f"{base}-{level}" if base else name

def note_for(public: str) -> str:
    return _NOTES.get(public, "")

def remember_model(public: str, upstream: str, display: str = "") -> None:
    """Restore an exact server mapping, including from a persisted account snapshot."""
    _learned_current.add(upstream)
    _learned_public_to_up[public] = upstream
    _learned_up_to_public[upstream] = public
    if display.lower().endswith("(thinking)"):
        _learned_thinking.add(upstream)

def request_level(model: str, effort: str = "") -> str:
    """Уровень размышлений для ``thinkingConfig.thinkingLevel`` или «» — выбирает Google.

    ``gemini-3.8-flash-high`` всегда «high». У имени без уровня (``gemini-3.8-flash``,
    ``gemini-3.8-flash-tiered``) решает «глубина рассуждений» клиента, а без неё — Google.
    """
    name = str(model or "")
    fixed = _REQUEST_LEVEL.get(name)
    if fixed:
        return fixed
    if upstream_id(name) in TIERED_UPSTREAM:
        return EFFORT_LEVELS.get(str(effort or "").lower(), "")
    return ""

def seed_rows() -> List[Tuple[str, str]]:
    """[(публичный id, подпись)] — стартовый набор каталога."""
    return [(m.public, m.note) for m in CURATED if m.seed]

def level_variants() -> List[str]:
    """Имена с уровнем у «tiered»-моделей (добавлены во второй версии каталога)."""
    return [m.public for m in CURATED if m.level]

def is_thinking(upstream: str) -> bool:
    name = str(upstream or "")
    return name in THINKING_UPSTREAM or name in _learned_thinking or name.lower().endswith("-thinking")

def family_level(model: str) -> str:
    """«high» / «medium» / «low» / «thinking» / «» — что сказано в публичном имени."""
    tail = str(model or "").lower().rsplit("-", 1)[-1]
    return tail if tail in (*LEVELS, "thinking") else ""

class Discovered(NamedTuple):
    public: str
    upstream: str
    display: str
    available: bool = True

def picker_ids(data: Dict[str, Any]) -> Optional[set[str]]:
    """The server's agent picker, rather than its larger internal model registry.

    fetchAvailableModels also lists legacy, transcription and background models.
    Its ModelSort.groups.modelIds field is the selector used by Antigravity.
    Older responses without a selector still use the named model registry.
    """
    sorts = data.get("agentModelSorts")
    if not isinstance(sorts, list):
        return None
    groups = [group for row in sorts if isinstance(row, dict)
              for group in row.get("groups", []) if isinstance(group, dict)]
    ids = {name for group in groups for name in group.get("modelIds", [])
           if isinstance(name, str) and name}
    return ids or None

def from_server(data: Dict[str, Any]) -> List[Discovered]:
    """Модели из ответа ``fetchAvailableModels``, пригодные для каталога.

    Берутся модели с названием (как в списке IDE); устаревшие id, служебные,
    генерация картинок и дубликаты названий отбрасываются. Заодно запоминаются
    переименования и «думающие» модели — это нужно переводу имён при запросе.
    """
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return []
    deprecated = {str(old): str((row or {}).get("newModelId") or "") for old, row in
                  (data.get("deprecatedModelIds") or {}).items() if isinstance(row, dict)}
    for old, new in deprecated.items():
        if old and new and new != old:
            _learned_deprecated[old] = new
    images = {str(x) for x in (data.get("imageGenerationModelIds") or [])}
    titles = {m.upstream: m.note for m in CURATED if not m.level}

    picker = picker_ids(data)
    rows: List[Tuple[str, str, bool, bool]] = []
    for upstream, info in models.items():

        registry_flash = upstream == "gemini-3.8-flash-tiered"
        if (not isinstance(info, dict) or is_internal(upstream) or info.get("isInternal") is True
                or (picker is not None and upstream not in picker and not registry_flash)):
            continue

        display = str(info.get("displayName") or "").strip() or titles.get(str(upstream), "")
        if not display and picker is not None:
            display = str(upstream)
        if (not display or upstream in deprecated or upstream in images
                or upstream in HIDDEN_UPSTREAM or is_retired(upstream)):
            continue
        rows.append((str(upstream), display, bool(info.get("recommended")), info.get("disabled") is not True))

    groups: Dict[str, List[Tuple[str, str, bool, bool]]] = {}
    for row in rows:
        groups.setdefault(row[1] if picker is None else row[1] + "\0" + row[0], []).append(row)
    found: List[Discovered] = []
    taken: set = set()
    for items in groups.values():
        display = items[0][1]

        wanted = slug(_LEVEL_TAIL.sub("", display))
        items.sort(key=lambda r: (not r[3], r[0] != wanted, not r[2], r[0]))
        upstream = items[0][0]
        public = public_id(upstream, display)
        if public in taken:
            public = upstream
        taken.add(public)
        remember_model(public, upstream, display)
        found.append(Discovered(public, upstream, display, items[0][3]))

    listed = {item.upstream for item in found}
    for model in CURATED:
        if model.level and model.upstream in listed and model.public not in taken:
            taken.add(model.public)
            available = next(item.available for item in found if item.upstream == model.upstream)
            found.append(Discovered(model.public, model.upstream, model.note, available))

    order = {m.public: index for index, m in enumerate(CURATED)}
    found.sort(key=lambda item: (order.get(item.public, len(order)), item.public))
    return found

class CatalogChange(NamedTuple):
    renamed: int
    removed: int
    added: int
    noted: int = 0

_AUTO_NOTES = ("discovered", "найдено", "получено", "кастом", "импорт", "imported")

def _auto_note(note: Any) -> bool:
    text = str(note or "").strip().lower()
    return not text or text.startswith(_AUTO_NOTES)

def upgrade_catalog(entries: Dict[str, Any], make_entry, *, add_seeds: bool = True,
                    only_new: Optional[Iterable[str]] = None) -> CatalogChange:
    """Приводит записи каталога Antigravity к публичным именам. ``entries`` меняется на месте.

    * запись со старым id (``claude-sonnet-4-6``, ``gemini-3-flash-agent`` …)
      переименовывается, выбор «включена/выключена» и подпись сохраняются;
    * найденные когда-то дубликаты и служебные модели удаляются (вручную
      добавленные — остаются);
    * недостающие модели стартового набора добавляются.

    ``make_entry(public, note, template)`` создаёт запись каталога. ``only_new`` —
    имена, которые разрешено добавить: каталог, уже прошедший прошлое обновление,
    получает только новинки, а удалённое пользователем не возвращается.
    """
    allowed = None if only_new is None else set(only_new)
    renamed_count = removed = added = 0
    prefix = "antigravity"
    moves: List[Tuple[Any, str]] = []

    for entry in [e for e in list(entries.values()) if getattr(e, "provider", "") == prefix]:
        source = str(getattr(entry, "source", "") or "")
        if (entry.model in HIDDEN_UPSTREAM or is_retired(entry.model)) and source != "manual":
            entries.pop(entry.id, None)
            removed += 1
            continue
        target = public_id(str(entry.model))
        if target != entry.model:

            entries.pop(entry.id, None)
            moves.append((entry, target))
    for entry, target in moves:
        existing = entries.get(f"{prefix}-{target}")
        if existing is not None:
            if not entry.enabled:
                existing.enabled = False
            removed += 1
            continue
        note = str(entry.note or "")
        if _auto_note(note):
            note = note_for(target) or note
        entries[f"{prefix}-{target}"] = make_entry(target, note, entry)
        renamed_count += 1
    if add_seeds:
        for public, note in seed_rows():
            if f"{prefix}-{public}" not in entries and (allowed is None or public in allowed):
                entries[f"{prefix}-{public}"] = make_entry(public, note, None)
                added += 1
    noted = 0
    for entry in entries.values():
        if getattr(entry, "provider", "") == prefix and _auto_note(entry.note):
            title = note_for(str(entry.model))
            if title and entry.note != title:
                entry.note = title
                noted += 1
    return CatalogChange(renamed_count, removed, added, noted)

def learn_limits(data: Dict[str, Any], record) -> None:
    """Контекст и лимит ответа каждой модели — под обоими именами (id Google и публичным)."""
    models = data.get("models") if isinstance(data, dict) else None
    for upstream, info in (models or {}).items():
        if not isinstance(info, dict) or is_internal(upstream):
            continue
        names = {str(upstream)}
        names.add(public_id(str(upstream), str(info.get("displayName") or "")))
        names.update(m.public for m in CURATED if m.level and m.upstream == str(upstream))
        for name in names:
            record(name, info.get("maxTokens"), info.get("maxOutputTokens"))
