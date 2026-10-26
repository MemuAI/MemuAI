"""Производители и обработчики «Входящих» (Round 35).

Здесь — тонкие адаптеры: подсистема в своём коде вызывает одну функцию
(``inbox_hooks.dfy_question(...)`` и т.п.), а кнопки во «Входящих»
выполняются ТЕМИ ЖЕ методами, что и в исходном интерфейсе
(DFYManager.answer, memory_v2.approve/reject, tgapp_code.send_to_terminal,
ComputerAutomation.approve). Ответ в любом месте закрывает элемент везде:
исходная подсистема сама зовёт resolve, когда вопрос снят.

Каждая функция-хук никогда не бросает исключений: «Входящие»
необязательны и не могут сломать DFY, планировщик или Proxy.

Источники (``source``): dfy, computer, memory, memucode, schedule, models,
modules, proxy, ui (+ любые, которые добавят другие зоны).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("memuai.inbox")

def _inbox():
    try:
        from . import inbox
        return inbox
    except Exception:  # pragma: no cover
        return None

_lang_cache = {"at": 0.0, "value": "ru"}

def _lang() -> str:
    """Язык интерфейса (кэш 10 с: один вопрос DFY строит до десятка подписей)."""
    now = time.monotonic()
    if now - _lang_cache["at"] < 10.0:
        return _lang_cache["value"]
    try:
        from .pgdata import load_preferences
        value = "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        value = "ru"
    _lang_cache.update(at=now, value=value)
    return value

def _t(ru: str, en: str) -> str:
    return en if _lang() == "en" else ru

def _short(text: Any, n: int = 160) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"

def _safe(fn):
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:  # noqa: BLE001
            log.debug("inbox hook %s failed", fn.__name__, exc_info=True)
            return None
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper

def _dfy_key(task_id: str, qid: str) -> str:
    return f"dfy:{task_id}:{qid}"

@_safe
def dfy_question(task: Any, question: Dict[str, Any]) -> None:
    """DFYManager._ask: задача ждёт ответа пользователя."""
    ib = _inbox()
    if ib is None or not question:
        return
    kind = str(question.get("kind") or "text")
    qid = str(question.get("id") or "")
    goal = _short(getattr(task, "goal", ""), 120)
    prompt = str(question.get("prompt") or question.get("why") or "").strip()
    ref = {"task_id": task.id, "question_id": qid, "chat_id": getattr(task, "chat_id", "") or "",
           "task_source": getattr(task, "source", "") or "", "qkind": kind}
    open_btn = {"id": "open", "label": _t("Открыть задачу", "Open task"), "kind": "ui"}
    body = prompt
    if question.get("amount"):
        body += ("\n" if body else "") + _t("Сумма: ", "Amount: ") + str(question.get("amount"))
    if question.get("url"):
        body += ("\n" if body else "") + str(question.get("url"))
    if kind in ("choice", "text") and len(" ".join(prompt.split())) <= 120:
        body = ""
    body = (body + "\n\n" + _t("Задача: ", "Task: ") + goal).strip() if goal else body
    actions: List[Dict[str, Any]]
    group = "ask"
    if kind == "confirm":
        title = _t("DFY просит подтверждения", "DFY asks for confirmation")
        actions = [{"id": "approve", "label": _t("Разрешить", "Allow"), "style": "primary"},
                   {"id": "deny", "label": _t("Отклонить", "Deny"), "style": "danger"}, open_btn]
        group = "confirm"
        ib_kind = "approval"
    elif kind == "choice":
        title = _t("DFY спрашивает: ", "DFY asks: ") + _short(prompt, 120)
        actions = [{"id": f"choice:{i}", "label": _short(c, 60)} for i, c in enumerate(question.get("choices") or [])][:6]
        actions += [{"id": "reply", "label": _t("Свой ответ", "Other answer"), "form": "text"}, open_btn]
        ib_kind = "question"
    elif kind == "text":
        title = _t("DFY спрашивает: ", "DFY asks: ") + _short(prompt, 120)
        actions = [{"id": "reply", "label": _t("Ответить", "Reply"), "form": "text", "style": "primary"},
                   {"id": "decline", "label": _t("Не отвечать", "Skip")}, open_btn]
        ib_kind = "question"
    elif kind == "code":
        title = _t("DFY ждёт код подтверждения", "DFY needs a confirmation code")
        if question.get("sent_to"):
            body = (_t("Код отправлен: ", "Code sent to: ") + str(question["sent_to"]) + "\n" + body).strip()
        actions = [{"id": "code", "label": _t("Ввести код", "Enter code"), "form": "code", "style": "primary"},
                   open_btn]
        ib_kind = "question"
    elif kind == "secret":
        title = _t("DFY нужны данные: ", "DFY needs data: ") + _short(question.get("label") or prompt, 80)

        actions = [{"id": "open", "label": _t("Заполнить форму", "Fill in the form"), "kind": "ui", "style": "primary"},
                   {"id": "decline", "label": _t("Отказать", "Decline")}]
        ib_kind = "question"
    elif kind == "takeover":
        title = _t("DFY просит перехватить управление браузером", "DFY asks you to take over the browser")
        actions = [{"id": "open", "label": _t("Перехватить", "Take over"), "kind": "ui", "style": "primary"},
                   {"id": "decline", "label": _t("Пропустить", "Skip")}]
        ib_kind = "question"
    elif kind == "stuck":
        title = _t("DFY застрял и ждёт решения", "DFY is stuck and waits for a decision")
        actions = [{"id": "stuck:continue", "label": _t("Продолжить", "Continue"), "style": "primary"},
                   {"id": "stuck:replan", "label": _t("Другой план", "Re-plan")},
                   {"id": "reply", "label": _t("Подсказать", "Give a hint"), "form": "text"},
                   {"id": "stuck:stop", "label": _t("Остановить", "Stop"), "style": "danger"}, open_btn]
        ib_kind = "question"
    else:
        title = _t("DFY ждёт ответа", "DFY is waiting for you")
        actions = [open_btn]
        ib_kind = "question"
    ib.post(ib_kind, title, body, source="dfy", ref=ref, actions=actions,
            dedupe_key=_dfy_key(task.id, qid), severity="ask", group=group)

@_safe
def dfy_question_closed(task: Any, question: Optional[Dict[str, Any]]) -> None:
    ib = _inbox()
    if ib is None or not question:
        return
    ib.resolve(_dfy_key(task.id, str(question.get("id") or "")), result="answered")

def _dfy_handler(manager: Any):
    async def handle(item: Dict[str, Any], action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        ref = item.get("ref") or {}
        task_id, qid = str(ref.get("task_id") or ""), str(ref.get("question_id") or "")
        body: Dict[str, Any] = {"question_id": qid}
        text = str(payload.get("text") or "").strip()
        if action == "approve":
            body["approve"] = True
        elif action == "deny":
            body["approve"] = False
        elif action == "decline":
            body["approve"] = False
        elif action.startswith("choice:"):
            task = manager.get(task_id)
            choices = ((task.question or {}).get("choices") if task is not None and task.question else None) or []
            i = int(action.split(":", 1)[1])
            if not 0 <= i < len(choices):
                raise ValueError(_t("Вариант не найден", "Option not found"))
            body["answer"] = str(choices[i])
        elif action == "reply":
            if not text:
                raise ValueError(_t("Пустой ответ", "Empty answer"))
            body["answer"] = text
            if ref.get("qkind") == "stuck":
                body.update({"decision": "hint", "hint": text})
        elif action == "code":
            if not text:
                raise ValueError(_t("Введите код", "Enter the code"))
            body["code"] = text
        elif action.startswith("stuck:"):
            body["decision"] = action.split(":", 1)[1]
        else:
            raise ValueError("unknown action")
        task = manager.get(task_id)
        if task is None or not task.question or str(task.question.get("id") or "") != qid:

            return {"resolve": True, "result": "stale", "ok": False,
                    "error": _t("Этот вопрос уже неактуален", "This question is no longer pending")}
        try:
            await manager.answer(task_id, body)
        except KeyError as exc:
            return {"resolve": True, "ok": False, "error": str(exc.args[0] if exc.args else exc)}
        return {"resolve": True, "result": action}
    return handle

@_safe
def computer_approval(runtime: Any, action: Dict[str, Any]) -> None:
    ib = _inbox()
    if ib is None:
        return
    step = action.get("step")
    tid = str(getattr(runtime, "task_id", "") or "cu")
    ib.post("approval", _t("Управление компьютером: разрешить действие?", "Computer control: allow this action?"),
            str(action.get("description") or action.get("action") or ""), source="computer",
            ref={"task_id": tid, "step": step},
            actions=[{"id": "approve", "label": _t("Разрешить", "Allow"), "style": "primary"},
                     {"id": "approve_all", "label": _t("Разрешить всё", "Allow all")},
                     {"id": "reject", "label": _t("Отклонить", "Deny"), "style": "danger"}],
            dedupe_key=f"computer:{tid}:{step}", severity="ask", group="confirm", ephemeral=True)

@_safe
def computer_approval_closed(runtime: Any, action: Optional[Dict[str, Any]]) -> None:
    ib = _inbox()
    if ib is None or not action:
        return
    tid = str(getattr(runtime, "task_id", "") or "cu")
    ib.resolve(f"computer:{tid}:{action.get('step')}", result="answered")

def _computer_handler(runtime: Any):
    def handle(item: Dict[str, Any], action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        step = (item.get("ref") or {}).get("step")
        if action in ("approve", "approve_all"):
            ok = runtime.approve(True, all_steps=action == "approve_all", step=step)
        elif action == "reject":
            ok = runtime.approve(False, note=str(payload.get("text") or ""), step=step)
        else:
            raise ValueError("unknown action")
        if not ok:
            return {"resolve": True, "ok": False, "result": "stale",
                    "error": _t("Это действие уже неактуально", "This action is no longer pending")}
        return {"resolve": True}
    return handle

_mem_closing: Dict[str, str] = {}

def _mem_key(pid: str, item_id: str) -> str:
    return f"memory:{pid}:{item_id}"

@_safe
def memory_sync(doc: Dict[str, Any], profile_id: str = "") -> None:
    """После каждой записи памяти: факты «на проверке» ⇄ элементы «Входящих»."""
    ib = _inbox()
    if ib is None or not isinstance(doc, dict):
        return
    pid = str(profile_id or doc.get("profile_id") or "default")
    pending = {str(i.get("id")): i for i in doc.get("items") or []
               if isinstance(i, dict) and i.get("status") == "pending" and i.get("id")}
    prefix = f"memory:{pid}:"
    open_items = [x for x in ib.list(status="open", source="memory", limit=600)
                  if str(x.get("dedupe_key") or "").startswith(prefix)]
    have = {str(x["dedupe_key"])[len(prefix):] for x in open_items}
    for item_id in have - set(pending):
        ib.resolve(prefix + item_id, result=_mem_closing.pop(prefix + item_id, "reviewed"))
    for item_id, fact in pending.items():
        if item_id in have:
            continue
        op = fact.get("op") or "add"
        if op == "forget":
            title = _t("Забыть факт? ", "Forget a fact? ") + _short(fact.get("text"), 140)
        elif op == "update" and fact.get("reason") == "conflict":
            title = _t("Обновить устаревший факт? ", "Replace an outdated fact? ") + _short(fact.get("text"), 140)
        elif op == "update":
            title = _t("Обновить факт? ", "Update a fact? ") + _short(fact.get("text"), 140)
        else:
            title = _t("Запомнить? ", "Remember? ") + _short(fact.get("text"), 140)
        body = str(fact.get("text") or "")
        old = next((i for i in doc.get("items") or [] if isinstance(i, dict) and op == "update" and fact.get("target")
                    and i.get("id") == fact.get("target") and i.get("status") == "active"), None)
        if old:
            body = _t("Было: ", "Was: ") + str(old.get("text") or "") + "\n" + _t("Станет: ", "Becomes: ") + body
        if fact.get("category"):
            body += "\n" + _t("Категория: ", "Category: ") + str(fact.get("category"))
        ib.post("review", title, body, source="memory",
                ref={"item_id": item_id, "profile_id": pid, "text": str(fact.get("text") or "")[:2000],
                     "op": op, "sensitive": bool(fact.get("sensitive"))},
                actions=[{"id": "accept", "label": _t("Обновить", "Replace") if old else _t("Принять", "Accept"),
                          "style": "primary"}]
                + ([{"id": "both", "label": _t("Оставить оба", "Keep both")}] if old else [])
                + [{"id": "edit", "label": _t("Изменить", "Edit"), "form": "text",
                          "placeholder": str(fact.get("text") or "")[:500]},
                         {"id": "reject", "label": _t("Отклонить", "Reject"), "style": "danger"},
                         {"id": "open", "label": _t("Открыть память", "Open memory"), "kind": "ui"}],
                dedupe_key=prefix + item_id, severity="ask", group="ask")

def _memory_handler():
    def handle(item: Dict[str, Any], action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        from . import memory_v2 as mem
        ref = item.get("ref") or {}
        item_id, pid = str(ref.get("item_id") or ""), str(ref.get("profile_id") or "")
        _mem_closing[_mem_key(pid, item_id)] = action
        try:
            if action == "accept":
                mem.approve(item_id, profile_id=pid)
            elif action == "both":
                mem.approve(item_id, profile_id=pid, keep_both=True)
            elif action == "edit":
                text = str(payload.get("text") or "").strip()
                if not text:
                    raise ValueError(_t("Пустой текст факта", "The fact is empty"))
                mem.approve(item_id, text=text, profile_id=pid)
            elif action == "reject":
                mem.reject(item_id, profile_id=pid)
            else:
                raise ValueError("unknown action")
        except KeyError:
            _mem_closing.pop(_mem_key(pid, item_id), None)
            return {"resolve": True, "ok": False, "result": "stale",
                    "error": _t("Факт уже проверен", "This fact was already reviewed")}
        except ValueError as exc:
            _mem_closing.pop(_mem_key(pid, item_id), None)
            if str(exc) == "secret":
                raise ValueError(_t("Похоже на секрет — такой факт не сохраняется",
                                    "Looks like a secret — such a fact is not saved")) from None
            raise
        return {"resolve": True}
    return handle

@_safe
def schedule_result(task_id: str, ok: bool, text: str, extra: Optional[Dict[str, Any]] = None,
                    meta: Optional[Dict[str, Any]] = None) -> None:
    """scheduler._record_result: результат / ошибка / Work-изменения на подтверждение."""
    ib = _inbox()
    if ib is None or (meta or {}).get("test"):
        return
    extra = extra or {}
    name = ""
    try:
        from .pgdata import load_schedules
        data = load_schedules()
        rows = data.get("tasks") if isinstance(data, dict) else data
        task = next((t for t in rows or [] if isinstance(t, dict) and str(t.get("id")) == str(task_id)), None)
        if task:
            name = str(task.get("name") or "")
            if task.get("inbox") is False:
                return
    except Exception:
        pass
    name = name or _t("Задача", "Task")
    ref = {"task_id": str(task_id)}
    open_btn = {"id": "open", "label": _t("Открыть в Планировании", "Open in Planning"), "kind": "ui"}
    if not ok:
        ib.post("error", _t("Задача не выполнена: ", "Task failed: ") + name, _short(text, 1500),
                source="schedule", ref=ref, actions=[open_btn, {"id": "ack", "label": _t("Понятно", "Got it")}],
                dedupe_key=f"schedule:fail:{task_id}", severity="warn", group="notify")
        return
    ib.resolve(f"schedule:fail:{task_id}", result="recovered")
    if extra.get("quiet"):
        return
    if extra.get("work_status") == "pending":
        files = [str(x.get("path") or "") for x in extra.get("pending_work") or [] if isinstance(x, dict)]
        rid = str(extra.get("result_id") or "")
        ib.post("approval", _t("Work-изменения ждут подтверждения: ", "Work changes need approval: ") + name,
                (_t("Файлы: ", "Files: ") + ", ".join(files[:12]) + ("…" if len(files) > 12 else "")) if files else "",
                source="schedule", ref={**ref, "result_id": rid},
                actions=[{"id": "open", "label": _t("Проверить и применить", "Review and apply"), "kind": "ui",
                          "style": "primary"}, {"id": "dismiss", "label": _t("Скрыть", "Hide")}],
                dedupe_key=f"schedule:work:{rid or task_id}", severity="ask", group="confirm")
        return
    ib.post("result", _t("Готово: ", "Done: ") + name, _short(text, 3000), source="schedule", ref=ref,
            actions=[open_btn], dedupe_key=f"schedule:result:{task_id}", severity="info", group="notify")

@_safe
def schedule_work_applied(result_id: str) -> None:
    ib = _inbox()
    if ib is not None and result_id:
        ib.resolve(f"schedule:work:{result_id}", result="applied")

@_safe
def module_event(module_id: str, title: str, text: str) -> None:
    ib = _inbox()
    if ib is None:
        return
    is_models = module_id == "api-models"
    ib.post("notice", str(title or module_id), _short(text, 4000), source="models" if is_models else "modules",
            ref={"module_id": module_id},
            actions=[{"id": "open", "label": _t("Открыть модуль", "Open module"), "kind": "ui"}],
            severity="info", group="notify")

_acct_flags: Dict[str, float] = {}
_acct_lock = threading.Lock()

def _bg(fn, *a, **kw) -> None:
    threading.Thread(target=_safe(fn), args=a, kwargs=kw, daemon=True).start()

def proxy_account_failed(account: Any, exc: Exception, public_error: str) -> None:
    """pool.report_failure: вход отвергнут / квота исчерпана. Не блокирует (файл пишет поток)."""
    try:
        from .providers.base import ProviderAuthError, ProviderQuotaError
        if isinstance(exc, ProviderAuthError):
            kind = "auth"
        elif isinstance(exc, ProviderQuotaError):
            kind = "quota"
        else:
            return
        aid = str(getattr(account, "id", "") or "")
        key = f"proxy:{kind}:{aid}"
        now = time.time()
        with _acct_lock:
            if now - _acct_flags.get(key, 0) < 600:
                return
            _acct_flags[key] = now
        name = str(getattr(account, "label", "") or getattr(account, "name", "") or getattr(account, "provider", "") or aid)
        provider = str(getattr(account, "provider", "") or "")
        _bg(_post_account, kind, name, provider, str(public_error or ""), aid, key)
    except Exception:  # noqa: BLE001
        log.debug("proxy_account_failed", exc_info=True)

def _post_account(kind: str, name: str, provider: str, error: str, aid: str, key: str) -> None:
    ib = _inbox()
    if ib is None:
        return
    if kind == "auth":
        title = _t("Аккаунт отклонил вход: ", "Account sign-in rejected: ") + name
    else:
        title = _t("Квота аккаунта исчерпана: ", "Account quota exhausted: ") + name
    body = (provider + " · " if provider else "") + _short(error, 600)
    ib.post("error", title, body, source="proxy", ref={"account_id": aid, "problem": kind},
            actions=[{"id": "open", "label": _t("Открыть аккаунты Proxy", "Open Proxy accounts"), "kind": "ui"},
                     {"id": "ack", "label": _t("Понятно", "Got it")}],
            dedupe_key=key, severity="warn", group="notify")

def proxy_account_ok(account: Any) -> None:
    try:
        aid = str(getattr(account, "id", "") or "")
        keys = [f"proxy:auth:{aid}", f"proxy:quota:{aid}"]
        with _acct_lock:
            hit = [k for k in keys if k in _acct_flags]
            for k in hit:
                _acct_flags.pop(k, None)
        for k in hit:
            _bg(lambda k=k: (_inbox() and _inbox().resolve(k, result="recovered")))
    except Exception:  # noqa: BLE001
        pass

_mc_seen: Dict[str, float] = {}

def memucode_scan() -> None:
    """Открытые вопросы/разрешения терминалов MemuCode (опубликованных через app/cli/remote.py)."""
    ib = _inbox()
    if ib is None:
        return
    try:
        from .cli import remote
        terminals = remote.list_terminals()
    except Exception:
        terminals = []
    live = set()
    for term in terminals:
        dialog = term.get("dialog") or None
        if not dialog:
            continue
        rc = str(term.get("rc") or "")
        key = f"memucode:{rc}:{dialog.get('id')}"
        live.add(key)
        if key in _mc_seen:
            continue
        _mc_seen[key] = time.time()
        project = str(term.get("root") or "").replace("\\", "/").rstrip("/").split("/")[-1]
        detail = dialog.get("detail") or {}
        perm = dialog.get("kind") == "permission"
        title = (_t("MemuCode просит разрешение: ", "MemuCode asks for permission: ") + str(detail.get("tool") or "")) \
            if perm else (_t("MemuCode спрашивает: ", "MemuCode asks: ") + _short(dialog.get("title"), 120))
        body = "\n".join(x for x in [
            _short(dialog.get("title"), 300) if perm else "",
            str(detail.get("reason") or ""),
            str(detail.get("preview") or detail.get("args") or "")[:1500],
            str(dialog.get("body") or "")[-1500:] if not perm else "",
            (_t("Проект: ", "Project: ") + project) if project else ""] if x)
        actions = []
        for opt in (dialog.get("options") or [])[:6]:
            if dialog.get("multi"):
                break
            a = {"id": f"choice:{opt.get('index')}", "label": _short(opt.get("label"), 60)}
            if opt.get("text_input"):
                a["form"] = "text"
            if opt.get("danger") or (detail.get("danger") and not opt.get("cancel") and opt.get("value") not in ("no", "feedback")):
                a["confirm"] = True
                a["style"] = "danger" if opt.get("danger") else ""
            if opt.get("value") == "always":
                a["confirm"] = True
            if opt.get("cancel"):
                a["style"] = "ghost"
            elif not any(x.get("style") == "primary" for x in actions) and not a.get("confirm") and not opt.get("text_input"):
                a["style"] = "primary"
            actions.append(a)
        ib.post("approval" if perm else "question", title, body, source="memucode",
                ref={"rc": rc, "dialog": dialog.get("id"), "session_id": term.get("session_id"), "project": project},
                actions=actions, dedupe_key=key, severity="ask", group="confirm" if perm else "ask")

    for key in list(_mc_seen):
        if key not in live:
            _mc_seen.pop(key, None)
            ib.resolve(key, result="answered")
    for it in ib.list(status="open", source="memucode", limit=200):
        if it.get("dedupe_key") not in live:
            ib.resolve(it["id"], result="gone")

def _memucode_handler():
    def handle(item: Dict[str, Any], action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        from . import tgapp_code
        ref = item.get("ref") or {}
        if not action.startswith("choice:"):
            raise ValueError("unknown action")
        cmd: Dict[str, Any] = {"type": "answer", "dialog": int(ref.get("dialog") or 0),
                               "choice": int(action.split(":", 1)[1])}
        if payload.get("text"):
            cmd["text"] = str(payload["text"])[:4000]
        if payload.get("confirm") is True:
            cmd["confirm"] = True
        try:
            tgapp_code.send_to_terminal(str(ref.get("rc") or ""), cmd)
        except LookupError:
            return {"resolve": True, "ok": False, "result": "stale",
                    "error": _t("Вопрос уже неактуален", "The question is no longer pending")}
        except PermissionError:
            return {"ok": False, "need_confirm": True,
                    "error": _t("Опасное действие — подтвердите", "A dangerous action — confirm it")}
        return {"resolve": True}
    return handle

async def _memucode_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(memucode_scan)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(4.0)

def install_playground(app: Any, *, dfy_manager: Any = None, computer_runtime: Any = None) -> None:
    """Сервер окна MemuAI: маршруты + обработчики кнопок + фоновый опрос MemuCode."""
    ib = _inbox()
    if ib is None:
        return
    from . import inbox_api
    inbox_api.register(app)
    if dfy_manager is None:
        dfy_manager = getattr(getattr(app, "state", None), "dfy_manager", None)
    if dfy_manager is not None:
        ib.register_handler("dfy", _dfy_handler(dfy_manager))
    if computer_runtime is not None:
        ib.register_handler("computer", _computer_handler(computer_runtime))
    ib.register_handler("memory", _memory_handler())
    ib.register_handler("memucode", _memucode_handler())

    state = {"started": False}

    async def _inbox_startup() -> None:

        if state["started"]:
            return
        state["started"] = True

        def stale():
            live_dfy = set()
            if dfy_manager is not None:
                try:
                    live_dfy = {t.id for t in dfy_manager.tasks.values() if t.question}
                except Exception:
                    live_dfy = set()
            for src in ("dfy", "computer"):
                for it in ib.list(status="open", source=src, limit=600):
                    if src == "dfy" and str((it.get("ref") or {}).get("task_id") or "") in live_dfy:
                        continue
                    ib.resolve(it["id"], result="expired")
            try:
                from . import memory_v2
                memory_sync(memory_v2.load(), memory_v2.active_profile_id())
            except Exception:
                pass
        await asyncio.to_thread(_safe(stale))
        app.state.inbox_memucode_task = asyncio.create_task(_memucode_loop())

    inbox_api.set_startup(_inbox_startup)
