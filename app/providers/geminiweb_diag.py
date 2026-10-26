"""Gemini Web: пошаговая диагностика подключения (кнопка «Проверить подключение», раунд 37).

Шаги: куки распознаны → страница открылась → токен получен → модели аккаунта → тестовый запрос.
Раунд 41: шаг «Модели аккаунта» читает выбор модели самого аккаунта с gemini.google.com (GetUserStatus) и
возвращает их списком в result["models"] — UI показывает, какие модели аккаунту доступны.
Каждый шаг: status ok | warn | fail | skip, понятное описание и текст «как исправить» на ru/en.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from . import http
from .base import (ProviderAuthError, ProviderContentFilterError, ProviderError, ProviderModelUnavailableError,
                   ProviderRateLimitError)
from .geminiweb_cookies import PSIDTS, parse_cookie_import

STEPS = (
    ("cookies", "Куки распознаны", "Cookies recognized"),
    ("page", "Страница Gemini открылась", "Gemini page opened"),
    ("token", "Токен входа получен", "Sign-in token received"),
    ("models", "Модели аккаунта", "Account models"),
    ("limits", "Лимиты аккаунта", "Account limits"),
    ("request", "Тестовый запрос", "Test request"),
)

def _step(sid: str, status: str, ru: str, en: str, fix_ru: str = "", fix_en: str = "") -> Dict[str, Any]:
    title_ru, title_en = next((r, e) for s, r, e in STEPS if s == sid)
    return {"id": sid, "status": status, "title_ru": title_ru, "title_en": title_en,
            "detail_ru": ru, "detail_en": en, "fix_ru": fix_ru, "fix_en": fix_en}

def _pending(steps: List[Dict[str, Any]]) -> None:
    done = {s["id"] for s in steps}
    for sid, _ru, _en in STEPS:
        if sid not in done:
            steps.append(_step(sid, "skip", "Не проверялось — сначала исправьте шаг выше.",
                               "Not checked — fix the step above first."))

def _result(steps: List[Dict[str, Any]], signed_in: bool, email: str = "",
            models: Optional[List[Dict[str, Any]]] = None, limits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _pending(steps)
    failed = next((s for s in steps if s["status"] == "fail"), None)
    if failed:
        summary = f"{failed['title_ru']}: {failed['detail_ru']}"
    else:
        mode = f"вход выполнен{(' · ' + email) if email else ''}" if signed_in else "гостевой режим (только Flash)"
        summary = f"Gemini Web работает · {mode}"
    return {"ok": failed is None, "summary": summary, "steps": steps,
            "signed_in": signed_in, "email": email, "models": models or [], "limits": limits or {}}

async def run_diagnosis(provider, account, *, test_request: bool = True) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    raw = str((account.fields or {}).get("cookie", "") or "").strip()
    has_cookie = bool(raw)
    if not has_cookie:
        steps.append(_step("cookies", "warn", "Куки не указаны — гостевой режим: только Flash, без Pro и истории.",
                           "No cookies — guest mode: Flash only, no Pro or history.",
                           "Для Pro и надёжной работы добавьте куки или нажмите «Войти через Google».",
                           "For Pro and reliable access add cookies or use Sign in with Google."))
    else:
        parsed = parse_cookie_import(raw)
        if parsed.errors:
            err = parsed.errors[0]
            steps.append(_step("cookies", "fail", err["ru"], err["en"],
                               "Скопируйте куки заново по инструкции выше — подойдут два значения __Secure-1PSID и __Secure-1PSIDTS.",
                               "Copy the cookies again following the guide above — the two values __Secure-1PSID and __Secure-1PSIDTS are enough."))
            return _result(steps, False)
        names = ", ".join(n for n in parsed.cookies if n.startswith("__Secure-1PSID")) or "__Secure-1PSID"
        warn = next((w for w in parsed.warnings), None)
        steps.append(_step("cookies", "warn" if warn else "ok",
                           f"Найдено кук: {len(parsed.cookies)} ({names})." + (f" {warn['ru']}" if warn else ""),
                           f"Cookies found: {len(parsed.cookies)} ({names})." + (f" {warn['en']}" if warn else "")))

    try:
        info = await provider.page(account, force=True)
    except Exception as exc:  # noqa: BLE001 — показываем пользователю как шаг
        text = http.network_error_text(exc)
        steps.append(_step("page", "fail", f"Нет связи с gemini.google.com: {text}", f"Cannot reach gemini.google.com: {text}",
                           "Проверьте интернет. В регионах, где Gemini недоступен, укажите прокси/VPN в поле «Прокси» аккаунта.",
                           "Check the internet connection. Where Gemini is unavailable, set a proxy/VPN in the account Proxy field."))
        return _result(steps, False)
    if info.captcha or info.status == 429:
        steps.append(_step("page", "fail", "Google показал капчу «необычный трафик» для этого IP.",
                           "Google shows an \"unusual traffic\" captcha for this IP.",
                           "Добавьте куки вошедшего аккаунта, смените сеть или задайте прокси.",
                           "Add cookies of a signed-in account, switch network, or set a proxy."))
        return _result(steps, False)
    if info.login_page:
        steps.append(_step("page", "fail", "Google вернул страницу входа — куки просрочены или от другого аккаунта.",
                           "Google returned the sign-in page — the cookies expired or belong to another account.",
                           "Откройте gemini.google.com в браузере, убедитесь, что вошли, и скопируйте куки заново.",
                           "Open gemini.google.com in the browser, make sure you are signed in, and copy the cookies again."))
        return _result(steps, False)
    if info.status != 200:
        steps.append(_step("page", "fail", f"gemini.google.com ответил HTTP {info.status}.", f"gemini.google.com answered HTTP {info.status}.",
                           "Повторите позже; если ошибка держится — включите прокси/VPN.", "Retry later; if it persists, use a proxy/VPN."))
        return _result(steps, False)
    build = info.bl.split("_")[-2] if info.bl.count("_") >= 2 else (info.bl or "?")
    steps.append(_step("page", "ok", f"Страница открылась (сборка {build}).", f"The page opened (build {build})."))

    if has_cookie and not info.signed_in:
        index = str((account.fields or {}).get("auth_user", "") or "").strip()
        steps.append(_step("token", "fail", "Google не узнал аккаунт: на странице нет токена SNlM0e.",
                           "Google did not recognize the account: the page has no SNlM0e token.",
                           ("Скопируйте свежие куки (особенно __Secure-1PSIDTS) из вкладки, где Gemini открыт и вы вошли. "
                            + (f"Проверьте номер аккаунта /u/{index}." if index else
                               "Если в браузере несколько Google-аккаунтов, укажите номер из адреса /u/N/app.")),
                           ("Copy fresh cookies (especially __Secure-1PSIDTS) from a tab where Gemini is open and you are signed in. "
                            + (f"Check the account number /u/{index}." if index else
                               "With several Google accounts, set the number from the /u/N/app address."))))
        return _result(steps, False)
    if info.signed_in:
        who = f" ({info.email})" if info.email else ""
        steps.append(_step("token", "ok", f"Вход подтверждён{who}.", f"Sign-in confirmed{who}."))
        if has_cookie and PSIDTS not in str(account.fields.get("cookie", "")):
            steps[-1]["status"] = "warn"
    else:
        steps.append(_step("token", "warn", "Гостевой режим — токена входа нет, это нормально без кук.",
                           "Guest mode — no sign-in token, which is expected without cookies."))

    models, test_model = await _models_step(provider, account, info, steps)
    if steps[-1]["status"] == "fail":
        return _result(steps, info.signed_in, info.email, models)
    limits = await _limits_step(provider, account, info, steps)

    if test_request:
        try:
            text = await asyncio.wait_for(_ask(provider, account, test_model), timeout=60)
            preview = " ".join(text.split())[:80]
            steps.append(_step("request", "ok", f"Gemini ответил ({test_model}): «{preview}»",
                               f"Gemini replied ({test_model}): \"{preview}\""))
        except asyncio.TimeoutError:
            steps.append(_step("request", "fail", "Gemini не ответил за 60 секунд.", "Gemini did not answer within 60 seconds.",
                               "Повторите позже или задайте прокси.", "Retry later or set a proxy."))
        except ProviderAuthError as exc:
            steps.append(_step("request", "fail", str(exc), str(exc), "Обновите куки.", "Refresh the cookies."))
        except ProviderRateLimitError as exc:
            steps.append(_step("request", "fail", str(exc), str(exc),
                               "Подождите несколько минут, смените IP/прокси или войдите куками.",
                               "Wait a few minutes, change the IP/proxy, or sign in with cookies."))
        except ProviderModelUnavailableError as exc:
            ru = getattr(exc, "message_ru", "") or str(exc)
            steps.append(_step("request", "fail", ru, str(exc),
                               "Выберите в чате/Proxy модель из списка выше.", "Pick a model from the list above."))
        except ProviderContentFilterError as exc:
            steps.append(_step("request", "warn", str(exc), str(exc)))
        except ProviderError as exc:
            steps.append(_step("request", "fail", str(exc), str(exc),
                               "Если ошибка повторяется — обновите MemuAI: Google мог изменить протокол.",
                               "If it repeats, update MemuAI: Google may have changed the protocol."))
    else:
        steps.append(_step("request", "skip", "Пропущен в быстрой проверке.", "Skipped in the quick check."))
    return _result(steps, info.signed_in, info.email, models, limits)

def _fmt_reset(ts: Any) -> str:
    import time
    return time.strftime("%d.%m %H:%M", time.localtime(ts)) if isinstance(ts, (int, float)) and ts > 0 else ""

async def _limits_step(provider, account, info, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Лимиты из окна «Лимиты использования» Gemini. Никогда не валит проверку: это справка, а не условие работы."""
    if not info.signed_in:
        steps.append(_step("limits", "skip", "Лимиты есть только у вошедшего аккаунта Google.",
                           "Limits exist only for a signed-in Google account."))
        return {}
    try:
        data = await provider.account_limits(account, force=True, info=info)
    except Exception:  # noqa: BLE001
        data = {}
    rows = data.get("rows") or []
    if not rows:
        steps.append(_step("limits", "warn",
                           "Google не отдал счётчики лимитов. Исчерпание лимита всё равно распознаётся по ответу "
                           "Gemini (ошибка 1037 / «вы достигли лимита») — с понятной ошибкой 429 и временем сброса.",
                           "Google returned no limit counters. A used-up limit is still detected from Gemini's answer "
                           "(error 1037 / «you've reached your limit») — as a clear 429 with the reset time."))
        return data
    parts_ru, parts_en = [], []
    for row in rows[:5]:
        if row.get("limit") and row.get("used") is not None:
            value = value_en = f"{row['used']}/{row['limit']}" if row.get("unit") != "%" else f"{row['used']}%"
        else:
            value = row.get("unit") or "—"
            value_en = row.get("unit_en") or value
        reset = _fmt_reset(row.get("reset"))
        parts_ru.append(f"{row['display']} {value}" + (f" (сброс {reset})" if reset else ""))
        parts_en.append(f"{row.get('display_en') or row['display']} {value_en}" + (f" (resets {reset})" if reset else ""))
    plan = data.get("subscription") or ""
    exhausted = data.get("exhausted") or []
    steps.append(_step("limits", "warn" if exhausted else "ok",
                       (f"{plan} · " if plan else "") + "; ".join(parts_ru) + "."
                       + (f" Исчерпано: {', '.join(exhausted)}." if exhausted else ""),
                       (f"{plan} · " if plan else "") + "; ".join(parts_en) + "."
                       + (f" Used up: {', '.join(exhausted)}." if exhausted else ""),
                       "Возьмите другую модель или дождитесь сброса." if exhausted else "",
                       "Pick another model or wait for the reset." if exhausted else ""))
    return data

async def _models_step(provider, account, info, steps: List[Dict[str, Any]]):
    """Модели из выбора модели самого аккаунта; без ответа Google — статический список с предупреждением."""
    from .geminiweb import DEFAULT_MODEL, MODELS
    from .geminiweb_proto import ACCOUNT_STATUS
    try:
        status = await provider.account_models(account, force=True, info=info)
    except ProviderError:
        status = None
    if status and status.status not in (0, 1000) and not (status.status == 1016 and not info.signed_in):
        ru, en = ACCOUNT_STATUS.get(status.status, (f"Статус аккаунта {status.status}.", f"Account status {status.status}."))
        steps.append(_step("models", "fail", f"{ru} (код {status.status})", f"{en} (code {status.status})",
                           "Откройте gemini.google.com в браузере этим аккаунтом и проверьте, что Gemini работает.",
                           "Open gemini.google.com in the browser with this account and check that Gemini works."))
        return [], DEFAULT_MODEL
    if status and status.models:
        rows = [{"name": m.name, "display": m.display, "description": m.description, "available": m.available,
                 "id": m.id} for m in status.models]
        ok = [m for m in status.models if m.available]
        plan_ru = "платный тариф Google AI" if status.paid else ("бесплатный тариф" if info.signed_in else "гость")
        plan_en = "paid Google AI plan" if status.paid else ("free plan" if info.signed_in else "guest")
        names = ", ".join(f"{m.display} ({m.name})" for m in ok)
        locked = [m.display for m in status.models if not m.available]
        steps.append(_step("models", "ok",
                           f"{plan_ru.capitalize()} · доступно {len(ok)}: {names}."
                           + (f" Только со входом: {', '.join(locked)}." if locked else ""),
                           f"{plan_en.capitalize()} · {len(ok)} available: {names}."
                           + (f" Sign-in only: {', '.join(locked)}." if locked else "")))
        return rows, ok[0].name if ok else DEFAULT_MODEL
    rows = [{"name": n, "display": c["display"], "description": c["note"], "available": None, "id": ""}
            for n, c in MODELS.items()]
    steps.append(_step("models", "warn",
                       "Google не отдал список моделей аккаунта — показан стандартный: "
                       + ", ".join(c["display"] for c in MODELS.values()) + ".",
                       "Google did not return the account's model list — showing the standard one: "
                       + ", ".join(c["display"] for c in MODELS.values()) + ".",
                       "Модели, недоступные аккаунту, вернут понятную ошибку. Повторите проверку позже.",
                       "Models the account lacks return a clear error. Re-run the check later."))
    return rows, DEFAULT_MODEL

async def _ask(provider, account, model: str) -> str:
    out = []
    async for event in provider.chat_stream(account, model,
                                            [{"role": "user", "content": "Привет! Ответь одним коротким предложением."}], {}):
        if event.get("type") == "text":
            out.append(event.get("text", ""))
    return "".join(out)
