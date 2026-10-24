"""Входящие (Inbox) — единый хаб всего, что ждёт пользователя (Round 35).

Контракт для других подсистем (стабильный, не менять сигнатуры):

    from app import inbox
    item_id = inbox.post(kind, title, body="", source="dfy", ref={...},
                         actions=[{"id": "approve", "label": "Разрешить"}],
                         dedupe_key="dfy:task1:step3", severity="info|ask|warn")
    inbox.resolve("dfy:task1:step3", result="approved")   # по dedupe_key или id
    inbox.list(status="open")          # -> list[dict]
    inbox.mark_read(item_id)           # или список id, или all_items=True

Подсистема, которая хочет, чтобы кнопки во «Входящих» выполнялись её же
кодом, регистрирует обработчик:

    inbox.register_handler("dfy", fn)  # fn(item: dict, action_id: str, payload: dict) -> dict | None

Обработчик возвращает dict; если в нём {"resolve": True} (или обработчик
сам вызвал inbox.resolve) — элемент закрывается. Ошибки обработчика
пробрасываются в UI как {"ok": False, "error": ...}.

Хранение: <MEMUAI_DATA_DIR>/inbox/inbox.json (атомарная запись через
tmp + os.replace, потокобезопасно; при изменении файла другим процессом
(Proxy и окно MemuAI могут быть разными процессами) — перечитывается
по mtime). Лимиты: не больше MAX_ITEMS элементов, закрытые старше
RESOLVED_TTL_DAYS удаляются, открытые «info» старше INFO_TTL_DAYS тоже.

Живые обновления: subscribe(cb) — колбэк в этом процессе; version() —
монотонный счётчик (включает mtime файла), его опрашивает SSE-маршрут.
"""
from __future__ import annotations

import builtins
import inspect
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

KINDS_CONFIRM = {"approval", "confirm", "confirmation", "permission"}
KINDS_ASK = {"question", "ask", "input", "review"}
SEVERITIES = ("info", "ask", "warn")
GROUPS = ("ask", "confirm", "notify", "done")

MAX_ITEMS = 600
MAX_OPEN_PER_SOURCE = 200
RESOLVED_TTL_DAYS = 14
INFO_TTL_DAYS = 30
MAX_TITLE = 300
MAX_BODY = 8000

_lock = threading.RLock()
_items: List[Dict[str, Any]] = []
_loaded_path: Optional[str] = None
_loaded_mtime: float = -1.0
_version = 0
_subscribers: List[Callable[[str, Dict[str, Any]], None]] = []
_handlers: Dict[str, Callable[..., Any]] = {}


# ---------------------------------------------------------------- storage
def _data_dir() -> str:
    env = os.environ.get("MEMUAI_DATA_DIR") or os.environ.get("OMNIHUB_DATA_DIR")
    if env:
        return env
    try:
        from .config import DATA_DIR
        return DATA_DIR
    except Exception:  # pragma: no cover
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, "MemuAI")


def _path() -> str:
    return os.path.join(_data_dir(), "inbox", "inbox.json")


def _mtime(p: str) -> float:
    try:
        return os.path.getmtime(p)
    except OSError:
        return -1.0


def _load_locked() -> None:
    """(Пере)читать файл, если сменился путь или файл изменён извне."""
    global _items, _loaded_path, _loaded_mtime
    p = _path()
    mt = _mtime(p)
    if p == _loaded_path and mt == _loaded_mtime:
        return
    items: List[Dict[str, Any]] = []
    if mt >= 0:
        try:
            from .storage import load_json_with_recovery
            raw = load_json_with_recovery(p, {"version": 1, "items": []})
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            raw = raw.get("items") or []
        if isinstance(raw, builtins.list):
            items = [x for x in raw if isinstance(x, dict) and x.get("id")]
    _items = items
    _loaded_path = p
    _loaded_mtime = mt


def _txn(fn: Callable[[], Any]) -> Any:
    """Межпроцессная транзакция read-modify-write над списком элементов.

    fn() правит глобальный _items и возвращает (changed, result)."""
    global _loaded_path, _loaded_mtime
    from .storage import mutate_json
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    box: Dict[str, Any] = {}
    with _lock:
        def mutator(data: Any) -> Any:
            global _items
            if not isinstance(data, dict):
                data = {"version": 1, "items": []}
                replaced = True
            else:
                replaced = False
            raw = data.get("items")
            _items = [x for x in raw if isinstance(x, dict) and x.get("id")] \
                if isinstance(raw, builtins.list) else []
            changed, box["result"] = fn()
            data["version"] = 1
            data["items"] = _items
            if replaced:
                return True, data
            return bool(changed)
        mutate_json(p, {"version": 1, "items": []}, mutator, backup=False)
        _loaded_path = p
        _loaded_mtime = _mtime(p)
    return box.get("result")


def _bump(event: str, item: Optional[Dict[str, Any]] = None) -> None:
    global _version
    _version += 1
    for cb in builtins.list(_subscribers):
        try:
            cb(event, dict(item) if item else {})
        except Exception:
            pass


def _now() -> float:
    return time.time()


def _group_of(it: Dict[str, Any]) -> str:
    if it.get("status") != "open":
        return "done"
    g = it.get("group")
    if g in GROUPS:
        return g
    kind = str(it.get("kind") or "").lower()
    if kind in KINDS_CONFIRM:
        return "confirm"
    if kind in KINDS_ASK or it.get("severity") == "ask":
        return "ask"
    return "notify"


def _public(it: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(it)
    out["group"] = _group_of(it)
    snooze = float(it.get("snooze_until") or 0)
    out["snoozed"] = bool(snooze and snooze > _now() and it.get("status") == "open")
    return out


def _prune_locked() -> bool:
    now = _now()
    before = len(_items)
    keep: List[Dict[str, Any]] = []
    for it in _items:
        st = it.get("status")
        upd = float(it.get("updated") or it.get("created") or now)
        if st != "open" and now - upd > RESOLVED_TTL_DAYS * 86400:
            continue
        if st == "open" and it.get("severity") == "info" and _group_of(it) == "notify" \
                and now - upd > INFO_TTL_DAYS * 86400:
            continue
        keep.append(it)
    # cap per source for open items (старые info уходят первыми)
    by_src: Dict[str, int] = {}
    capped: List[Dict[str, Any]] = []
    for it in sorted(keep, key=lambda x: -float(x.get("updated") or 0)):
        if it.get("status") == "open":
            s = str(it.get("source") or "")
            by_src[s] = by_src.get(s, 0) + 1
            if by_src[s] > MAX_OPEN_PER_SOURCE and _group_of(it) == "notify":
                continue
        capped.append(it)
    if len(capped) > MAX_ITEMS:
        # сначала выкидываем закрытые, потом самые старые
        closed = [x for x in capped if x.get("status") != "open"]
        opened = [x for x in capped if x.get("status") == "open"]
        need = len(capped) - MAX_ITEMS
        closed = closed[:max(0, len(closed) - need)]
        capped = opened + closed
        capped.sort(key=lambda x: -float(x.get("updated") or 0))
        capped = capped[:MAX_ITEMS]
    capped.sort(key=lambda x: float(x.get("created") or 0))
    changed = len(capped) != before
    _items[:] = capped
    return changed


def _find_locked(key: str) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    for it in _items:
        if it.get("id") == key:
            return it
    # по dedupe_key: предпочитаем открытый, иначе последний
    found = None
    for it in _items:
        if it.get("dedupe_key") == key:
            if it.get("status") == "open":
                return it
            found = it
    return found


def _norm_actions(actions: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not actions:
        return out
    for a in actions:
        if isinstance(a, str):
            a = {"id": a, "label": a}
        if not isinstance(a, dict) or not a.get("id"):
            continue
        b = {"id": str(a["id"])[:64], "label": str(a.get("label") or a["id"])[:80]}
        for k in ("style", "url", "form", "placeholder", "kind", "confirm"):
            if k in a and a[k] is not None:
                b[k] = a[k]
        out.append(b)
    return out[:8]


# ---------------------------------------------------------------- API
def post(kind: str, title: str, body: str = "", source: str = "", ref: Any = None,
         actions: Optional[Iterable[Any]] = None, dedupe_key: Optional[str] = None,
         severity: str = "info", **extra: Any) -> str:
    """Добавить (или обновить по dedupe_key) элемент. Возвращает id.

    Если открытый элемент с тем же dedupe_key уже есть — он обновляется
    (title/body/actions/ref), становится непрочитанным, счётчик count++.
    Если он был закрыт — создаётся новый.
    Доп. ключи (**extra): group ("ask|confirm|notify"), url, icon, meta,
    ephemeral=True (после закрытия элемент удаляется, а не уходит в «Готово»).
    """
    sev = severity if severity in SEVERITIES else "info"
    now = _now()
    title = str(title or "").strip()[:MAX_TITLE] or str(kind or "")
    body = str(body or "")[:MAX_BODY]
    def fn():
        it = None
        if dedupe_key:
            for x in _items:
                if x.get("dedupe_key") == dedupe_key and x.get("status") == "open":
                    it = x
                    break
        event = "update"
        if it is None:
            it = {"id": "ib_" + uuid.uuid4().hex[:12], "created": now, "count": 0,
                  "status": "open", "read": False, "snooze_until": 0, "result": None}
            _items.append(it)
            event = "post"
        it.update({"kind": str(kind or "notice")[:40], "title": title, "body": body,
                   "source": str(source or "")[:40], "ref": ref,
                   "actions": _norm_actions(actions), "dedupe_key": dedupe_key or None,
                   "severity": sev, "updated": now, "read": False})
        it["count"] = int(it.get("count") or 0) + 1
        for k in ("group", "url", "icon", "meta", "ephemeral"):
            if k in extra and extra[k] is not None:
                it[k] = extra[k]
        _prune_locked()
        return True, (event, _public(it))
    event, out = _txn(fn)
    _bump(event, out)
    return out["id"]


def resolve(dedupe_key_or_id: str, result: Any = None, status: str = "resolved") -> bool:
    """Закрыть элемент(ы) по id или dedupe_key. True если что-то закрыто."""
    if not dedupe_key_or_id:
        return False
    st = status if status in ("resolved", "dismissed") else "resolved"
    if not _has_open(dedupe_key_or_id):
        return False

    def fn():
        changed = []
        for it in _items:
            if it.get("status") != "open":
                continue
            if it.get("id") == dedupe_key_or_id or it.get("dedupe_key") == dedupe_key_or_id:
                it["status"] = st
                it["result"] = result
                it["read"] = True
                it["updated"] = _now()
                changed.append(_public(it))
        # «эфемерные» (шаги управления компьютером) не копятся в «Готово»
        _items[:] = [x for x in _items if not (x.get("ephemeral") and x.get("status") != "open")]
        return bool(changed), changed
    changed = _txn(fn) or []
    for it in changed:
        _bump("resolve", it)
    return bool(changed)


def _has_open(key: str) -> bool:
    """Быстрая проверка без транзакции (resolve часто зовут «на всякий случай»)."""
    with _lock:
        _load_locked()
        return any(x.get("status") == "open" and (x.get("id") == key or x.get("dedupe_key") == key)
                   for x in _items)


def dismiss(dedupe_key_or_id: str) -> bool:
    return resolve(dedupe_key_or_id, result="dismissed", status="dismissed")


def get(dedupe_key_or_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        _load_locked()
        it = _find_locked(dedupe_key_or_id)
        return _public(it) if it else None


def is_open(dedupe_key_or_id: str) -> bool:
    it = get(dedupe_key_or_id)
    return bool(it and it.get("status") == "open")


def list(status: str = "all", source: Optional[str] = None, q: str = "",
         group: Optional[str] = None, include_snoozed: bool = True,
         limit: int = 500) -> List[Dict[str, Any]]:  # noqa: A001 - контракт
    """Элементы, новые сверху. status: open|done|all. q — поиск по тексту."""
    ql = (q or "").strip().lower()
    with _lock:
        _load_locked()
        rows = [_public(x) for x in _items]
    out = []
    for it in rows:
        if status == "open" and it.get("status") != "open":
            continue
        if status == "done" and it.get("status") == "open":
            continue
        if source and source != "all" and it.get("source") != source:
            continue
        if group and group != "all" and it.get("group") != group:
            continue
        if not include_snoozed and it.get("snoozed"):
            continue
        if ql:
            hay = " ".join(str(it.get(k) or "") for k in ("title", "body", "source", "kind")).lower()
            if ql not in hay:
                continue
        out.append(it)
    out.sort(key=lambda x: -float(x.get("updated") or 0))
    return out[:max(1, int(limit or 500))]


def mark_read(ids: Any = None, read: bool = True, all_items: bool = False) -> int:
    """Пометить прочитанными id/dedupe_key (строка или список) или всё."""
    if isinstance(ids, str):
        ids = [ids]
    keys = set(ids or [])

    def match(it):
        return all_items or it.get("id") in keys or (it.get("dedupe_key") and it.get("dedupe_key") in keys)
    with _lock:
        _load_locked()
        if not any(match(x) and bool(x.get("read")) != bool(read) for x in _items):
            return 0

    def fn():
        n = 0
        for it in _items:
            if match(it) and bool(it.get("read")) != bool(read):
                it["read"] = bool(read)
                n += 1
        return bool(n), n
    n = _txn(fn) or 0
    if n:
        _bump("read", {})
    return n


def snooze(dedupe_key_or_id: str, seconds: float) -> bool:
    def fn():
        it = _find_locked(dedupe_key_or_id)
        if not it or it.get("status") != "open":
            return False, None
        it["snooze_until"] = (_now() + max(0.0, float(seconds))) if seconds else 0
        it["read"] = True
        return True, _public(it)
    out = _txn(fn)
    if out:
        _bump("update", out)
    return bool(out)


def counts() -> Dict[str, Any]:
    """Сводка для бейджа: непрочитанные открытые (без отложенных)."""
    with _lock:
        _load_locked()
        rows = [_public(x) for x in _items]
    c = {"unread": 0, "open": 0, "ask": 0, "confirm": 0, "notify": 0, "done": 0,
         "warn": 0, "sources": {}}
    for it in rows:
        g = it["group"]
        if g == "done":
            c["done"] += 1
            continue
        if it.get("snoozed"):
            continue
        c["open"] += 1
        c[g] += 1
        if not it.get("read"):
            c["unread"] += 1
        if it.get("severity") == "warn":
            c["warn"] += 1
        s = it.get("source") or "other"
        c["sources"][s] = c["sources"].get(s, 0) + 1
    return c


def clear_done() -> int:
    def fn():
        before = len(_items)
        _items[:] = [x for x in _items if x.get("status") == "open"]
        n = before - len(_items)
        return bool(n), n
    n = _txn(fn) or 0
    if n:
        _bump("clear", {})
    return n


# ---------------------------------------------------------------- live
def version() -> str:
    """Меняется при любом изменении (в т.ч. из другого процесса)."""
    return f"{_version}:{_mtime(_path())}"


def subscribe(cb: Callable[[str, Dict[str, Any]], None]) -> Callable[[], None]:
    _subscribers.append(cb)

    def _unsub() -> None:
        try:
            _subscribers.remove(cb)
        except ValueError:
            pass
    return _unsub


# ---------------------------------------------------------------- actions
def register_handler(source: str, fn: Callable[..., Any]) -> None:
    """fn(item, action_id, payload) -> dict | None. Один обработчик на source."""
    _handlers[str(source)] = fn


def handler_for(source: str) -> Optional[Callable[..., Any]]:
    return _handlers.get(str(source or ""))


def _pre_action(dedupe_key_or_id: str, action_id: str) -> Any:
    """(item, handler) или готовый ответ dict."""
    it = get(dedupe_key_or_id)
    if not it:
        return {"ok": False, "error": "not_found"}
    if action_id in ("dismiss", "_dismiss"):
        dismiss(it["id"])
        return {"ok": True, "resolved": True}
    if action_id in ("ack", "_ack"):
        resolve(it["id"], result="ack")
        return {"ok": True, "resolved": True}
    if it.get("status") != "open":
        return {"ok": False, "error": "already_resolved", "item": it}
    act = next((a for a in it.get("actions") or [] if a.get("id") == action_id), None)
    fn = handler_for(it.get("source") or "")
    if act and (act.get("url") or act.get("kind") == "ui"):
        # кнопка только для интерфейса (открыть раздел/ссылку): выполняет UI
        mark_read(it["id"])
        return {"ok": True, "open": act.get("url") or "", "resolved": False}
    if fn is None:
        resolve(it["id"], result=action_id)
        return {"ok": True, "resolved": True}
    return it, fn


def _post_action(it: Dict[str, Any], action_id: str, res: Any) -> Dict[str, Any]:
    res = dict(res) if isinstance(res, dict) else {}
    if res.pop("resolve", False):
        resolve(it["id"], result=res.get("result", action_id))
    mark_read(it["id"])
    cur = get(it["id"]) or {}
    # The owner may close the item itself (e.g. DFY `_ask` finally → result="answered")
    # before we get here: keep the concrete button the user pressed as the result.
    if cur.get("status") == "resolved" and cur.get("result") in (None, "answered"):
        want = res.get("result", action_id)

        def fix():
            for x in _items:
                if x.get("id") == it["id"]:
                    x["result"] = want
                    return True, None
            return False, None
        _txn(fix)
        cur = get(it["id"]) or {}
    res.setdefault("ok", True)
    res["resolved"] = cur.get("status") != "open"
    return res


def run_action(dedupe_key_or_id: str, action_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Выполнить кнопку элемента через обработчик подсистемы-владельца (синхронно)."""
    pre = _pre_action(dedupe_key_or_id, action_id)
    if isinstance(pre, dict):
        return pre
    it, fn = pre
    try:
        res = fn(it, action_id, payload or {})
        if inspect.isawaitable(res):
            import asyncio
            res = asyncio.run(res)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:400] or type(e).__name__}
    return _post_action(it, action_id, res)


async def arun_action(dedupe_key_or_id: str, action_id: str,
                      payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """То же из asyncio: async-обработчики ждём, синхронные — в потоке."""
    import asyncio
    pre = await asyncio.to_thread(_pre_action, dedupe_key_or_id, action_id)
    if isinstance(pre, dict):
        return pre
    it, fn = pre
    try:
        if inspect.iscoroutinefunction(fn):
            res = await fn(it, action_id, payload or {})
        else:
            res = await asyncio.to_thread(fn, it, action_id, payload or {})
            if inspect.isawaitable(res):
                res = await res
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:400] or type(e).__name__}
    return await asyncio.to_thread(_post_action, it, action_id, res)


def _reset_for_tests() -> None:
    global _items, _loaded_path, _loaded_mtime, _version
    with _lock:
        _items = []
        _loaded_path = None
        _loaded_mtime = -1.0
        _version += 1
        _handlers.clear()
