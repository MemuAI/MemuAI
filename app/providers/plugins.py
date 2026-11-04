"""Плагины провайдеров MemuAI Proxy: свои прокси без правок исходников.

Пользователь кладёт файл в папку плагинов — и провайдер появляется в списке
Proxy рядом со встроенными, с его полями аккаунта, моделями и проверкой:

* ``*.py`` — кодовый плагин: класс-наследник :class:`app.providers.base.Provider`
  (тот же интерфейс, что у встроенных провайдеров);
* ``*.yaml`` / ``*.yml`` / ``*.json`` — декларативный плагин: OpenAI-совместимый
  endpoint без кода (обобщение провайдера «Собственный API» в шаблон).

Где лежит папка плагинов
------------------------
``<data-dir>/providers`` (на Windows — ``%APPDATA%\\MemuAI\\providers``):
папка с данными пользователя, обновление MemuAI её не трогает. Переопределяется
переменной окружения ``MEMUAI_PROVIDERS_DIR`` (удобно для тестов и для
Proxy в отдельном каталоге данных).

Безопасность — честно
---------------------
Плагин выполняется **внутри процесса MemuAI Proxy с полными правами этого
процесса**: он может читать файлы, сеть и — теоретически — ключи других
аккаунтов из памяти и из ``accounts.json``. Песочницы здесь нет, и делать вид,
что она есть, мы не будем. Поэтому:

* плагин устанавливается только явно — пользователь сам кладёт файл в папку;
* админка показывает каждый плагин и его статус, а битый плагин — с текстом
  ошибки, а не молча;
* ставьте только код, которому доверяете, как доверяете любому исполняемому
  файлу на своём компьютере.

Путь к изоляции (на будущее, не реализовано): запускать ``chat_stream``
плагина в отдельном процессе с JSON-RPC поверх stdio и узким API хоста
(только HTTP через пул Proxy, только чтение своих полей аккаунта) — см.
раздел «Изоляция» в ``providers/README.md``.

Версионирование API плагинов
----------------------------
``PLUGIN_API`` — версия интерфейса, которую понимает эта сборка MemuAI
(сейчас 1). Плагин заявляет свою версию модульным ``PLUGIN_API = 1``.
Плагин с версией новее, чем у MemuAI, отклоняется с понятным сообщением
(«обновите MemuAI»); без версии — тоже отклоняется.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import types
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import DATA_DIR
from .base import Field, Provider

PLUGIN_API = 1
PLUGIN_API_MIN = 1
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
MAX_PLUGIN_FILE_BYTES = 512 * 1024
EXAMPLE_FILES = ("README.md", "kimi2api.py", "web_wrapper_template.py")


def plugins_dir() -> Path:
    """Папка плагинов: переопределение окружением или <data-dir>/providers."""
    override = os.environ.get("MEMUAI_PROVIDERS_DIR", "").strip()
    if override:
        return Path(override)
    return Path(DATA_DIR) / "providers"


def examples_dir() -> Path:
    """Примеры плагинов, поставляемые с MemuAI (только для чтения)."""
    return Path(__file__).resolve().parent.parent / "providers_user_examples"


def ensure_examples() -> List[str]:
    """При первом запуске кладёт примеры и README в папку плагинов.

    Копируются только отсутствующие файлы — правки пользователя никогда
    не перезаписываются. Под pytest не выполняется.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return []
    target = plugins_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        src = examples_dir()
        copied: List[str] = []
        for name in EXAMPLE_FILES:
            dst = target / name
            if not dst.exists() and (src / name).is_file():
                shutil.copyfile(src / name, dst)
                copied.append(name)
        return copied
    except Exception:
        return []


@dataclass
class PluginStatus:
    file: str
    id: str = ""
    title: str = ""
    kind: str = ""  # "code" | "declarative"
    status: str = "error"  # "loaded" | "error" | "disabled"
    error: str = ""
    plugin_api: Any = None
    version: str = ""  # PLUGIN_VERSION / spec["version"], если заявлена
    author: str = ""  # PLUGIN_AUTHOR / spec["author"], если заявлен

    @property
    def ok(self) -> bool:
        return self.status == "loaded"

    @property
    def providers(self) -> List[str]:
        return [self.id] if self.id else []

    def public_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "id": self.id, "title": self.title,
                "kind": self.kind, "status": self.status, "ok": self.ok,
                "providers": self.providers,
                "error": self.error, "plugin_api": self.plugin_api,
                "version": self.version, "author": self.author}


_LOADED_MODULES: Dict[str, str] = {}  # path -> sys.modules name (для перезагрузки)
_STATUS: List[PluginStatus] = []

ALLOWED_SUFFIXES = (".py", ".yaml", ".yml", ".json")
DISABLED_FILENAME = ".disabled.json"  # начинается с точки — загрузчик его пропускает


def plugin_status() -> List[Dict[str, Any]]:
    """Статус всех найденных файлов плагинов (загруженные и ошибочные)."""
    return [entry.public_dict() for entry in _STATUS]


# ---------------------------------------------------------------------------
# Управление плагинами из админки: отключение, установка, удаление, шаблоны.
# ---------------------------------------------------------------------------

def _disabled_path() -> Path:
    return plugins_dir() / DISABLED_FILENAME


def disabled_names() -> set:
    """Имена отключённых файлов (хранятся в .disabled.json в папке плагинов)."""
    try:
        data = json.loads(_disabled_path().read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(name) for name in data if isinstance(name, str)}
    except Exception:
        pass
    return set()


def _safe_plugin_filename(name: Any) -> Tuple[str, str]:
    """Проверка имени файла плагина от пользователя: только имя, без пути.

    Возвращает (имя, текст ошибки). Имена на ``.``/``_`` отклоняются:
    такие файлы загрузчик пропускает как черновики, и установленный
    плагин молча не появился бы в списке.
    """
    if not isinstance(name, str) or not name.strip():
        return "", "нужно имя файла"
    raw = name.strip()
    if raw != Path(raw).name or "/" in raw or "\\" in raw:
        return "", f"имя «{raw}» содержит путь — нужно только имя файла"
    if raw.startswith((".", "_")):
        return "", (f"имя «{raw}» начинается с точки или подчёркивания — "
                    "такие файлы загрузчик пропускает как черновики")
    if Path(raw).suffix.lower() not in ALLOWED_SUFFIXES:
        return "", (f"расширение «{Path(raw).suffix}» не подходит: "
                    "нужен .py, .yaml, .yml или .json")
    return raw, ""


def set_plugin_disabled(filename: str, disabled: bool) -> Tuple[bool, str]:
    """Включить/выключить плагин. Возвращает (изменилось ли, текст ошибки)."""
    name, error = _safe_plugin_filename(filename)
    if error:
        return False, error
    if not (plugins_dir() / name).is_file():
        return False, f"файл «{name}» не найден в папке плагинов"
    names = disabled_names()
    if disabled:
        if name in names:
            return False, ""
        names.add(name)
    else:
        if name not in names:
            return False, ""
        names.discard(name)
    try:
        _disabled_path().write_text(
            json.dumps(sorted(names), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return False, f"не удалось сохранить список отключённых: {exc}"
    return True, ""


def install_plugin(name: str, content: bytes) -> Tuple[str, str]:
    """Записать новый файл плагина. Возвращает (имя файла, текст ошибки)."""
    if len(content) > MAX_PLUGIN_FILE_BYTES:
        return "", f"файл больше {MAX_PLUGIN_FILE_BYTES // 1024} КБ"
    fname, error = _safe_plugin_filename(name)
    if error:
        return "", error
    target = plugins_dir() / fname
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return "", (f"файл «{fname}» уже есть — сначала удалите его "
                        "или выберите другое имя")
        target.write_bytes(content)
    except Exception as exc:  # noqa: BLE001
        return "", f"не удалось записать файл: {exc}"
    return fname, ""


def delete_plugin(filename: str) -> str:
    """Удалить файл плагина из папки. Возвращает текст ошибки или ''."""
    fname, error = _safe_plugin_filename(filename)
    if error:
        return error
    target = plugins_dir() / fname
    if not target.is_file():
        return f"файл «{fname}» не найден в папке плагинов"
    try:
        target.unlink()
    except Exception as exc:  # noqa: BLE001
        return f"не удалось удалить файл: {exc}"
    names = disabled_names()
    if fname in names:
        names.discard(fname)
        try:
            _disabled_path().write_text(
                json.dumps(sorted(names), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return ""


TEMPLATES = (
    {"id": "openai", "file": "openai_template.yaml", "kind": "declarative",
     "title": "OpenAI-совместимый API",
     "title_en": "OpenAI-compatible API",
     "description": "Любой сервис с /chat/completions — без кода: только адрес, ключ и список моделей.",
     "description_en": "Any service with /chat/completions — no code: just the URL, key and model list."},
    {"id": "kimi", "file": "kimi2api.py", "kind": "code",
     "title": "Kimi2API (экспериментальный)",
     "title_en": "Kimi2API (experimental)",
     "description": "Сайт Kimi как API: нужна кука kimi-auth из браузера. Эндпоинт не проверен живым аккаунтом.",
     "description_en": "The Kimi website as an API: needs the kimi-auth cookie from the browser. Not verified with a live account."},
    {"id": "web", "file": "web_wrapper_template.py", "kind": "code",
     "title": "Веб-обёртка (шаблон)",
     "title_en": "Web wrapper (template)",
     "description": "Заготовка для обёртки над сайтом: кука сессии, особые заголовки, SSE. Места правок помечены TODO.",
     "description_en": "Starter for wrapping a website: session cookie, custom headers, SSE. Edit points are marked TODO."},
    {"id": "empty", "file": "empty_template.py", "kind": "code",
     "title": "Пустой кодовый плагин",
     "title_en": "Empty code plugin",
     "description": "Минимальный скелет провайдера на Python: id, поля аккаунта, модели, chat_stream().",
     "description_en": "Minimal Python provider skeleton: id, account fields, models, chat_stream()."},
)


def template_list() -> List[Dict[str, Any]]:
    """Галерея шаблонов для админки (только метаданные, без содержимого)."""
    return [dict(entry) for entry in TEMPLATES]


def _free_filename(base: str, suffix: str) -> str:
    """Свободное имя файла в папке плагинов: base.suffix, base-2.suffix, ..."""
    candidate = f"{base}{suffix}"
    n = 2
    while (plugins_dir() / candidate).exists():
        candidate = f"{base}-{n}{suffix}"
        n += 1
    return candidate


def create_from_template(template_id: str, filename: str = "",
                         provider_id: str = "") -> Tuple[str, str]:
    """Создать плагин из шаблона галереи. Возвращает (имя файла, текст ошибки).

    Для декларативного шаблона ``openai`` подставляется id провайдера
    (по умолчанию ``my-api``); для кодовых шаблонов id зашит в файл —
    его правят после создания.
    """
    templates = {entry["id"]: entry for entry in TEMPLATES}
    entry = templates.get(str(template_id or "").strip())
    if entry is None:
        return "", f"шаблон «{template_id}» не найден"
    src = examples_dir() / entry["file"]
    if not src.is_file():
        return "", f"файл шаблона «{entry['file']}» не найден в поставке"
    try:
        text = src.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return "", f"шаблон не читается: {exc}"
    suffix = Path(entry["file"]).suffix
    if entry["id"] == "openai":
        pid = (provider_id or "my-api").strip()
        id_error = _check_plugin_id(pid, set())
        if id_error:
            return "", id_error
        text = re.sub(r"(?m)^id:.*$", f"id: {pid}", text, count=1)
        text = re.sub(r"(?m)^title:.*$", f"title: {pid}", text, count=1)
        base = pid
    else:
        base = Path(entry["file"]).stem
    fname = (filename or "").strip()
    if fname:
        fname, error = _safe_plugin_filename(fname)
        if error:
            return "", error
        if Path(fname).suffix.lower() != suffix.lower():
            return "", (f"имя «{fname}» должно заканчиваться на {suffix} "
                        f"(шаблон «{entry['id']}»)")
    else:
        fname = _free_filename(base, suffix)
    return install_plugin(fname, text.encode("utf-8"))


def _check_plugin_api(value: Any, where: str) -> Optional[str]:
    if value is None:
        return (f"{where}: не указана версия PLUGIN_API — добавьте строку "
                f"`PLUGIN_API = {PLUGIN_API}`")
    if not isinstance(value, int) or isinstance(value, bool):
        return f"{where}: PLUGIN_API должен быть целым числом, а не {value!r}"
    if value > PLUGIN_API:
        return (f"{where}: плагин требует PLUGIN_API {value}, а эта сборка "
                f"MemuAI понимает максимум {PLUGIN_API} — обновите MemuAI")
    if value < PLUGIN_API_MIN:
        return f"{where}: PLUGIN_API {value} слишком старый (минимум {PLUGIN_API_MIN})"
    return None


def _check_plugin_id(pid: Any, taken: set) -> Optional[str]:
    if not isinstance(pid, str) or not pid:
        return "у провайдера нет id (строка)"
    if not PLUGIN_ID_RE.match(pid):
        return (f"id «{pid}» недопустим: только латиница в нижнем регистре, "
                "цифры и дефис, начинается с буквы, до 32 символов")
    if pid in taken:
        return f"id «{pid}» уже занят (встроенный провайдер или другой плагин)"
    return None


def _import_plugin_module(path: Path) -> types.ModuleType:
    """Изолированный импорт файла плагина под уникальным именем модуля.

    Уникальное имя (путь + mtime) позволяет перезагрузить изменённый файл:
    старый модуль убирается из sys.modules, новый импортируется заново.
    """
    key = str(path)
    old = _LOADED_MODULES.pop(key, None)
    if old is not None:
        sys.modules.pop(old, None)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    name = f"memuai_provider_plugin_{digest}_{path.stat().st_mtime_ns}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"не удалось создать загрузчик для {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _LOADED_MODULES[key] = name
    return module


def _provider_classes(module: types.ModuleType) -> List[type]:
    """Классы-наследники Provider, определённые в самом файле плагина."""
    out: List[type] = []
    for _name, cls in inspect.getmembers(module, inspect.isclass):
        if cls is Provider:
            continue
        try:
            if not issubclass(cls, Provider):
                continue
        except Exception:
            continue
        if getattr(cls, "__module__", None) != module.__name__:
            continue  # импортированный класс, не определение плагина
        out.append(cls)
    return out


def _validate_provider_class(cls: type, taken: set) -> Tuple[Optional[Provider], Optional[str]]:
    pid = getattr(cls, "id", "")
    error = _check_plugin_id(pid, taken)
    if error:
        return None, error
    if getattr(cls, "chat_stream", None) is Provider.chat_stream:
        return None, f"провайдер «{pid}»: не реализован chat_stream()"
    try:
        instance = cls()
    except Exception as exc:  # noqa: BLE001
        return None, f"провайдер «{pid}»: не создаётся: {exc}"
    try:
        fields = instance.account_fields()
        if not isinstance(fields, list):
            return None, f"провайдер «{pid}»: account_fields() вернул не список"
    except Exception as exc:  # noqa: BLE001
        return None, f"провайдер «{pid}»: account_fields() упал: {exc}"
    try:
        models = instance.seed_models()
        if not isinstance(models, list):
            return None, f"провайдер «{pid}»: seed_models() вернул не список"
    except Exception as exc:  # noqa: BLE001
        return None, f"провайдер «{pid}»: seed_models() упал: {exc}"
    return instance, None


def load_code_plugin(path: Path, taken: set) -> Tuple[Dict[str, Provider], List[PluginStatus]]:
    """Загрузка одного .py-плагина. Ошибки — в статусе, не исключением."""
    statuses: List[PluginStatus] = []
    providers: Dict[str, Provider] = {}
    try:
        if path.stat().st_size > MAX_PLUGIN_FILE_BYTES:
            raise ValueError(f"файл больше {MAX_PLUGIN_FILE_BYTES // 1024} КБ")
        module = _import_plugin_module(path)
    except Exception as exc:  # noqa: BLE001
        statuses.append(PluginStatus(file=path.name, kind="code", status="error",
                                     error=f"не импортируется: {exc}"))
        return providers, statuses
    api_error = _check_plugin_api(getattr(module, "PLUGIN_API", None), path.name)
    if api_error:
        statuses.append(PluginStatus(file=path.name, kind="code", status="error",
                                     error=api_error,
                                     plugin_api=getattr(module, "PLUGIN_API", None)))
        return providers, statuses
    version = str(getattr(module, "PLUGIN_VERSION", "") or "")[:32]
    author = str(getattr(module, "PLUGIN_AUTHOR", "") or "")[:80]
    classes = _provider_classes(module)
    if not classes:
        statuses.append(PluginStatus(file=path.name, kind="code", status="error",
                                     error="в файле нет класса-наследника Provider",
                                     plugin_api=getattr(module, "PLUGIN_API", None),
                                     version=version, author=author))
        return providers, statuses
    for cls in classes:
        instance, error = _validate_provider_class(cls, taken)
        if error:
            statuses.append(PluginStatus(file=path.name, kind="code", status="error",
                                         error=error,
                                         plugin_api=getattr(module, "PLUGIN_API", None),
                                         version=version, author=author))
            continue
        taken.add(instance.id)
        providers[instance.id] = instance
        statuses.append(PluginStatus(file=path.name, id=instance.id,
                                     title=instance.title or instance.id,
                                     kind="code", status="loaded",
                                     plugin_api=getattr(module, "PLUGIN_API", None),
                                     version=version, author=author))
    return providers, statuses


# ---------------------------------------------------------------------------
# Декларативные плагины (YAML/JSON): OpenAI-совместимый endpoint без кода.
# ---------------------------------------------------------------------------

DECLARATIVE_KINDS = ("openai-chat", "openai-responses")
DECLARATIVE_REQUIRED = ("id", "title", "kind", "base_url")


def _load_spec(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return None, f"не читается: {exc}"
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            return None, "YAML недоступен в этой сборке — используйте JSON"
        try:
            data = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            return None, f"некорректный YAML: {exc}"
    else:
        try:
            data = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            return None, f"некорректный JSON: {exc}"
    if not isinstance(data, dict):
        return None, "корень описания должен быть объектом"
    return data, None


def _validate_spec(data: Dict[str, Any]) -> Optional[str]:
    for key in DECLARATIVE_REQUIRED:
        if not str(data.get(key) or "").strip():
            return f"в описании нет обязательного поля «{key}»"
    if str(data.get("kind")) not in DECLARATIVE_KINDS:
        return (f"kind «{data.get('kind')}» неизвестен, допустимо: "
                + ", ".join(DECLARATIVE_KINDS))
    auth = str(data.get("auth", "bearer") or "bearer").strip().lower()
    if auth not in ("bearer", "header", "none"):
        return f"auth «{auth}» неизвестен, допустимо: bearer, header, none"
    if auth == "header" and not str(data.get("auth_header") or "").strip():
        return "для auth: header нужен auth_header (например, X-Api-Key)"
    models = data.get("models")
    if models is not None:
        if not isinstance(models, list) or not models or \
                any(not isinstance(m, str) or not m.strip() for m in models):
            return "models должен быть непустым списком строк"
    headers = data.get("extra_headers")
    if headers is not None and (not isinstance(headers, dict) or
                                any(not isinstance(k, str) or not isinstance(v, str)
                                    for k, v in headers.items())):
        return "extra_headers должен быть объектом строка → строка"
    allow_private = bool(data.get("allow_private_network", False))
    from ..media_cache import validate_api_base_url
    error = validate_api_base_url(str(data["base_url"]).strip(),
                                  allow_private=allow_private)
    if error:
        return f"base_url отклонён: {error}"
    return None


class DeclarativeProvider(Provider):
    """OpenAI-совместимый провайдер из YAML/JSON-описания.

    Чат делегируется встроенному CustomProvider с синтетическим аккаунтом:
    один движок SSE/ретраев/ключей на всех, плагин описывает только endpoint.
    """

    def __init__(self, spec: Dict[str, Any]) -> None:
        self._spec = spec
        self.id = str(spec["id"]).strip()
        self.title = str(spec["title"]).strip()
        self.icon = str(spec.get("icon") or "◆").strip()[:4] or "◆"
        self.description = str(spec.get("description") or "").strip()
        self.title_en = str(spec.get("title_en") or "").strip()
        self.description_en = str(spec.get("description_en") or "").strip()

    def account_fields(self) -> List[Field]:
        auth = str(self._spec.get("auth", "bearer") or "bearer").strip().lower()
        if auth == "none":
            return []
        return [Field("api_key", "API-ключ", "password", placeholder="sk-…",
                      label_en="API key")]

    def seed_models(self) -> List[Tuple[str, str]]:
        return [(str(m).strip(), "") for m in (self._spec.get("models") or [])]

    def _synthetic_account(self, account) -> Any:
        """Аккаунт для CustomProvider: поля из описания + ключ пользователя."""
        fields: Dict[str, Any] = {
            "base_url": str(self._spec["base_url"]).strip().rstrip("/"),
            "api_protocol": ("responses" if self._spec.get("kind") == "openai-responses"
                             else "chat"),
            "api_key": str((account.fields or {}).get("api_key", "") or ""),
            "allow_private_network": ("yes" if self._spec.get("allow_private_network")
                                      else "no"),
        }
        chat_path = str(self._spec.get("chat_path") or "").strip()
        if chat_path:
            fields["chat_endpoint"] = chat_path
        headers = dict(self._spec.get("extra_headers") or {})
        auth = str(self._spec.get("auth", "bearer") or "bearer").strip().lower()
        if auth == "header":
            headers[str(self._spec["auth_header"]).strip()] = "{api_key}"
        elif auth == "none":
            fields["api_key"] = ""
        if headers:
            fields["headers"] = json.dumps(headers, ensure_ascii=False)
        models = self._spec.get("models") or []
        if models:
            fields["models"] = ", ".join(str(m) for m in models)
        # id нужен CustomProvider на 429 (quota_planner.observe_headers).
        return types.SimpleNamespace(id=getattr(account, "id", ""),
                                     fields=fields, extra={},
                                     proxy=getattr(account, "proxy", ""))

    def _custom(self):
        from .custom import CustomProvider
        return CustomProvider()

    async def chat_stream(self, account, model: str, messages: List[Dict[str, Any]],
                          params: Dict[str, Any]):
        async for event in self._custom().chat_stream(
                self._synthetic_account(account), model, messages, params):
            yield event

    async def check(self, account):
        return await self._custom().check(self._synthetic_account(account))

    async def dynamic_models(self, account):
        return await self._custom().dynamic_models(self._synthetic_account(account))


def load_declarative_plugin(path: Path, taken: set) -> Tuple[Dict[str, Provider], List[PluginStatus]]:
    """Загрузка одного YAML/JSON-плагина. Ошибки — в статусе, не исключением."""
    providers: Dict[str, Provider] = {}
    data, error = _load_spec(path)
    if error:
        return providers, [PluginStatus(file=path.name, kind="declarative",
                                        status="error", error=error)]
    api_error = _check_plugin_api(data.get("plugin_api"), path.name)
    if api_error:
        return providers, [PluginStatus(file=path.name, kind="declarative",
                                        status="error", error=api_error,
                                        plugin_api=data.get("plugin_api"))]
    error = _validate_spec(data)
    if error:
        return providers, [PluginStatus(file=path.name, kind="declarative",
                                        status="error", error=error,
                                        plugin_api=data.get("plugin_api"))]
    id_error = _check_plugin_id(str(data["id"]).strip(), taken)
    if id_error:
        return providers, [PluginStatus(file=path.name, kind="declarative",
                                        status="error", error=id_error,
                                        plugin_api=data.get("plugin_api"))]
    try:
        instance = DeclarativeProvider(data)
        fields = instance.account_fields()
        if not isinstance(fields, list):
            raise ValueError("account_fields() вернул не список")
    except Exception as exc:  # noqa: BLE001
        return providers, [PluginStatus(file=path.name, kind="declarative",
                                        status="error", error=str(exc),
                                        plugin_api=data.get("plugin_api"))]
    taken.add(instance.id)
    providers[instance.id] = instance
    return providers, [PluginStatus(file=path.name, id=instance.id,
                                    title=instance.title or instance.id,
                                    kind="declarative", status="loaded",
                                    plugin_api=data.get("plugin_api"),
                                    version=str(data.get("version") or "")[:32],
                                    author=str(data.get("author") or "")[:80])]


def load_all(directory: Optional[Path] = None) -> Tuple[Dict[str, Provider], List[PluginStatus]]:
    """Загрузка всех плагинов из папки. Битый файл не роняет остальные.

    Возвращает (провайдеры, статусы всех файлов). Статусы запоминаются
    для ``plugin_status()`` (админка).
    """
    from . import RESERVED_PREFIXES, builtin_ids
    target = Path(directory) if directory is not None else plugins_dir()
    providers: Dict[str, Provider] = {}
    statuses: List[PluginStatus] = []
    taken = set(builtin_ids()) | set(RESERVED_PREFIXES) | {"custom"}
    disabled = disabled_names()
    try:
        files = sorted(target.iterdir()) if target.is_dir() else []
    except Exception:
        files = []
    for path in files:
        name = path.name
        if name.startswith((".", "_")) or not path.is_file():
            continue
        suffix = path.suffix.lower()
        if name in disabled:
            # Отключён из админки: не грузится, но виден в списке со статусом.
            statuses.append(PluginStatus(
                file=name, kind="code" if suffix == ".py" else "declarative",
                status="disabled"))
            continue
        try:
            if suffix == ".py":
                found, found_statuses = load_code_plugin(path, taken)
            elif suffix in (".yaml", ".yml", ".json"):
                found, found_statuses = load_declarative_plugin(path, taken)
            else:
                continue
        except Exception as exc:  # noqa: BLE001 — один файл никогда не роняет загрузку
            found, found_statuses = {}, [PluginStatus(
                file=name, kind="code" if suffix == ".py" else "declarative",
                status="error", error=f"неожиданная ошибка загрузки: {exc}")]
        providers.update(found)
        statuses.extend(found_statuses)
    global _STATUS
    _STATUS = statuses
    return providers, statuses
