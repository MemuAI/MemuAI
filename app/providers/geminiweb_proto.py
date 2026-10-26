"""Протокол Gemini Web (раунд 37): страница инициализации, f.req, разбор потока StreamGenerate.

Источники (сверено в октябре 2026):
* Sophomoresty/gemini-web2api (MIT) — поля f.req, MODE_CATEGORY в индексе 79, глубина в 17,
  загрузка в content-push.googleapis.com; перенесено с адаптацией.
* HanaokaYuzu/Gemini-API (AGPL-3.0) — код НЕ переносился; оттуда взяты только факты протокола:
  ключи страницы SNlM0e / cfb2h / FdrFJe, заголовки x-goog-ext-525001261-jspb и
  x-goog-ext-525005358-jspb, путь к коду ошибки part[5][2][0][1][0] и его значения
  (1013/1037/1050/1052/1060), расположение текста c[1][0], мыслей c[37][0][0] и картинок c[12],
  ротация через accounts.google.com/RotateCookies.
* Раунд 41b: сгенерированные картинки c[12] поле 7 ([0][i][0][3][3] адрес, [0][3][2] подпись), видео Veo — поле 59
  ([0][0][0][0][7] = [превью, mp4]), id ответа inner[1][1]; поля c[12] читаются и позиционно, и из разреженного
  словаря (jspb) — факты из HanaokaYuzu/Gemini-API master (types/video.py, client.py), код написан заново.
* Раунд 41: список моделей аккаунта — batchexecute rpcid otAQ7b (GetUserStatus): тело [14] статус аккаунта,
  [15] модели (m[0] hex-id, m[1]/m[10] категория, m[11]/m[19] имя в выборе модели, m[12]/m[2] описание,
  m[17]/m[9] номер модели), [16]/[17] флаги тарифа → capacity; формат заголовка выбора модели с хвостом
  [1|2 extended, uuid сессии] и x-goog-ext-73010990-jspb — факты протокола из HanaokaYuzu/Gemini-API
  (constants.py, types/availablemodel.py, master, октябрь 2026), код написан заново.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

BASE = "https://gemini.google.com"
RPC_PATH = "/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
BATCH_PATH = "/_/BardChatUi/data/batchexecute"
RPC_USER_STATUS = "otAQ7b"
RPC_DELETE_CHAT = "GzXR5e"
ROTATE_URL = "https://accounts.google.com/RotateCookies"
UPLOAD_URL = "https://content-push.googleapis.com/upload/"
DEFAULT_PUSH_ID = "feeds/mcudyrk2a4khkz"
MODEL_HEADER = "x-goog-ext-525001261-jspb"

MODEL_CAPS = (4, 5, 6, 8)

ERROR_TEXTS: Dict[int, Tuple[str, str]] = {
    1013: ("Временная ошибка Gemini (1013) — повторите через несколько секунд.",
           "Temporary Gemini error (1013) — retry in a few seconds."),
    1037: ("Исчерпан лимит этой модели в аккаунте Gemini (1037). Возьмите Flash или подождите сброса лимита.",
           "This model's usage limit is exhausted for the Gemini account (1037). Use Flash or wait for the reset."),
    1050: ("Модель не совпадает с историей диалога (1050).", "The model does not match the conversation (1050)."),
    1052: ("Google не принял выбор модели (1052) — модель недоступна аккаунту или формат устарел.",
           "Google rejected the model selection (1052) — the model is unavailable or the format is outdated."),
    1096: ("Google не принял гостевой запрос (1096). Добавьте куки Google или смените сеть/прокси.",
           "Google rejected the guest request (1096). Add Google cookies or switch network/proxy."),
    1060: ("Google временно заблокировал IP или регион не поддерживается (1060). Включите прокси/VPN в аккаунте.",
           "Google temporarily blocked the IP or the region is unsupported (1060). Set a proxy/VPN on the account."),
}

ACCOUNT_STATUS: Dict[int, Tuple[str, str]] = {
    1014: ("Доступ к Gemini временно ограничен (регион или сессия).", "Gemini access is temporarily restricted (region or session)."),
    1016: ("Google не узнал вход — куки просрочены, запрос идёт как гость.", "Google did not recognize the sign-in — the cookies expired, the request runs as a guest."),
    1021: ("Google отклонил доступ этого аккаунта к Gemini.", "Google rejected this account's access to Gemini."),
    1033: ("Аккаунт не прошёл проверки Google для части функций.", "The account did not pass Google's checks for some features."),
    1040: ("Нужно принять новые условия Gemini: откройте gemini.google.com в браузере.", "Accept the new Gemini terms: open gemini.google.com in the browser."),
    1042: ("Нужно принять новые условия Gemini: откройте gemini.google.com в браузере.", "Accept the new Gemini terms: open gemini.google.com in the browser."),
    1054: ("Доступ заблокирован родительским контролем.", "Access is blocked by parental controls."),
    1057: ("Нужно разрешение родителя/опекуна.", "Parent or guardian approval is required."),
    1060: ("Gemini не работает в вашей стране/регионе — нужен прокси/VPN.", "Gemini is not available in your country/region — use a proxy/VPN."),
}
ACCOUNT_BLOCKED = frozenset({1014, 1021, 1054, 1057, 1060})

CANNED_MAX_LEN = 360
_CANNED_RE = re.compile(
    r"(i'?m (?:just |only )?a language model|i am (?:just |only )?a language model|as a language model,? i (?:can'?t|don'?t)"
    r"|i'?m (?:having a hard time|not able to help with that|unable to help with that)|that'?s not something i'?m able to do"
    r"|i can'?t (?:help|assist) with that,? as i'?m (?:only|just) a language model"
    r"|я (?:всего лишь|просто|только) языковая модель|такие вопросы и задачи не для меня|я не могу (?:с этим )?помочь,? (?:так как|поскольку) я"
    r"|мне сложно выполнить (?:этот|ваш) запрос|не могу помочь с этим запросом"
    r"|в моей программе нет таких возможностей|я (?:просто|всего лишь|лишь) генерирую текст"
    r"|i'?m (?:just |only )?(?:designed|programmed) to (?:process and )?generate text|my programming doesn'?t (?:let|allow) me"
    r"|я лише мовна модель|ich bin (?:nur|lediglich) ein sprachmodell|solo soy un modelo de lenguaje|je ne suis qu'un modèle de langage)",
    re.I)

_SNLM0E_RE = re.compile(r'"SNlM0e"\s*:\s*"([^"]*)"')
_BL_RE = re.compile(r'"cfb2h"\s*:\s*"([^"]+)"')
_BL_FALLBACK_RE = re.compile(r"(boq_[a-z0-9-]+_\d{8}\.\d+_p\d+)")
_FSID_RE = re.compile(r'"FdrFJe"\s*:\s*"([^"]+)"')
_PUSH_RE = re.compile(r'"qKIAYe"\s*:\s*"([^"]+)"')
_EMAIL_RE = re.compile(r'"oPEP7c"\s*:\s*"([^"@]+@[^"]+)"')
_ARTIFACT_RE = re.compile(r"https?://googleusercontent\.com/(?:[\w-]+/)+\d+\n?")
_CODE_EVENT_RE = re.compile(
    r"```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?", re.S)
_CARD_RE = re.compile(r"^https?://googleusercontent\.com/card_content/\d+")
BARD_ERROR_RE = re.compile(r"BardErrorInfo\s*\[(\d+)\]")

@dataclass
class PageInfo:
    status: int = 0
    final_url: str = ""
    at: str = ""
    bl: str = ""
    fsid: str = ""
    push_id: str = ""
    email: str = ""
    login_page: bool = False
    captcha: bool = False

    @property
    def signed_in(self) -> bool:
        return bool(self.at)

def parse_page(status: int, final_url: str, html: str) -> PageInfo:
    info = PageInfo(status=status, final_url=final_url or "")
    html = html or ""
    if "accounts.google.com" in info.final_url and ("ServiceLogin" in info.final_url or "signin" in info.final_url):
        info.login_page = True
    if "g-recaptcha" in html and "captcha-form" in html:
        info.captcha = True
    for attr, regex in (("at", _SNLM0E_RE), ("bl", _BL_RE), ("fsid", _FSID_RE), ("push_id", _PUSH_RE), ("email", _EMAIL_RE)):
        match = regex.search(html)
        if match:
            setattr(info, attr, match.group(1))
    if not info.bl:
        match = _BL_FALLBACK_RE.search(html)
        if match:
            info.bl = match.group(1)
    return info

def build_freq(prompt: str, mode: int, think: int, *, temporary: bool = True, extended: bool = False,
               file_refs: Optional[List[Tuple[str, str]]] = None, request_uuid: str = "",
               language: str = "en") -> List[Any]:
    """Внутренний массив f.req (индексы — как у веб-клиента Gemini).

    mode — номер модели в индексе 79: при выборе модели заголовком это номер из списка моделей аккаунта
    (m[17]), без заголовка — MODE_CATEGORY (1 Flash, 3 Pro, 6 Flash-Lite)."""
    inner: List[Any] = [None] * 81
    refs = [[[ref], name] for ref, name in (file_refs or [])] or None
    inner[0] = [prompt, 0, None, refs, None, None, 0]
    inner[1] = [language or "en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [1]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [1]
    if temporary:
        inner[45] = 1
    inner[53] = 0
    inner[59] = request_uuid or str(uuid.uuid4()).upper()
    inner[61] = []
    inner[68] = 1
    inner[79] = mode
    inner[80] = 2 if extended else 1
    return inner

def encode_body(inner: List[Any], at: str = "") -> bytes:
    compact = (",", ":")
    params = {"f.req": json.dumps([None, json.dumps(inner, ensure_ascii=False, separators=compact)],
                                  ensure_ascii=False, separators=compact)}
    if at:
        params["at"] = at
    return urllib.parse.urlencode(params).encode("utf-8")

def encode_batch(rpcid: str, payload: str = "[]", at: str = "") -> bytes:
    """Тело batchexecute: f.req = [[[rpcid, payload, null, "generic"]]]."""
    params = {"f.req": json.dumps([[[rpcid, payload, None, "generic"]]], ensure_ascii=False, separators=(",", ":"))}
    if at:
        params["at"] = at
    return urllib.parse.urlencode(params).encode("utf-8")

def batch_model_header(session_uuid: str) -> str:
    """Заголовок выбора модели для batchexecute — модель не выбрана, в конце uuid сессии клиента."""
    return json.dumps([1, None, None, None, None, None, None, None, list(MODEL_CAPS),
                       None, None, None, None, None, None, None, session_uuid], separators=(",", ":"))

@dataclass
class AccountModel:
    """Модель из выбора модели приложения Gemini для конкретного аккаунта (GetUserStatus [15])."""
    id: str
    name: str
    display: str
    description: str = ""
    category: str = ""
    number: int = 1
    capacity: int = 1
    capacity_field: int = 12
    available: bool = True

    def header(self, session_uuid: str, *, extended: bool = False) -> str:
        tail = f"null,{self.capacity}" if self.capacity_field == 13 else str(self.capacity)
        return (f'[1,null,null,null,"{self.id}",null,null,0,[{",".join(map(str, MODEL_CAPS))}],null,null,{tail},'
                f'null,null,{self.number},{2 if extended else 1},{json.dumps(session_uuid)}]')

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["AccountModel"]:
        try:
            return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})
        except (TypeError, KeyError):
            return None

@dataclass
class UserStatus:
    status: int = 0
    models: List[AccountModel] = field(default_factory=list)
    tier_flags: List[int] = field(default_factory=list)
    capability_flags: List[int] = field(default_factory=list)
    found: bool = False

    @property
    def paid(self) -> bool:
        """Платный тариф (AI Plus/Pro/Ultra): capacity не 1 или особое поле 13."""
        return capacity_for(self.tier_flags, self.capability_flags) != (1, 12)

def capacity_for(tier_flags: List[int], capability_flags: List[int]) -> Tuple[int, int]:
    """Флаги тарифа → (capacity, номер поля в заголовке). Значения — как в веб-клиенте."""
    if 21 in tier_flags:
        return 1, 13
    if 22 in tier_flags:
        return 2, 13
    if 115 in capability_flags:
        return 4, 12
    if 16 in tier_flags or 106 in capability_flags:
        return 3, 12
    if 8 in tier_flags or 19 in capability_flags:
        return 2, 12
    return 1, 12

def jspb(container: Any, index: int, default: Any = None) -> Any:
    """Поле JSPB-массива: по позиции или из «разреженного» словаря в последней ячейке (ключ = номер+1)."""
    if not isinstance(container, list) or not container:
        return default
    value = container[index] if 0 <= index < len(container) else None
    if value in (None, [], {}) or isinstance(value, dict):
        bundle = container[-1] if isinstance(container[-1], dict) else None
        value = bundle.get(str(index + 1)) if bundle else None
    return default if value in (None, [], {}) else value

def model_slug(display: str, category: str = "", model_id: str = "") -> str:
    """«3.8 Flash» → gemini-3.8-flash, «4 Argon» → gemini-4-argon, «3.5 Flash-Lite» → gemini-3.5-flash-lite."""
    label = (display or category or "").strip()
    if not label:
        return f"gemini-{model_id}"
    slug = re.sub(r"[^a-z0-9.]+", "-", label.lower()).strip("-")
    slug = re.sub(r"^gemini-?", "", slug)
    return f"gemini-{slug}" if slug else f"gemini-{model_id}"

def parse_user_status(text: str) -> UserStatus:
    """Ответ batchexecute otAQ7b (или уже разобранные кадры) → статус и модели аккаунта."""
    out = UserStatus()
    parser = FrameParser()
    frames = parser.feed(text or "") + parser.flush()
    for frame in frames:
        for part in iter_parts(frame):
            if _get(part, [1]) != RPC_USER_STATUS:
                continue
            raw = _get(part, [2])
            try:
                body = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                continue
            if not isinstance(body, list):
                continue
            out.found = True
            status = jspb(body, 14)
            out.status = status if isinstance(status, int) else 1000
            tiers = jspb(body, 16, [])
            caps = jspb(body, 17, [])
            out.tier_flags = [x for x in tiers if isinstance(x, int)] if isinstance(tiers, list) else []
            out.capability_flags = [x for x in caps if isinstance(x, int)] if isinstance(caps, list) else []
            capacity, cap_field = capacity_for(out.tier_flags, out.capability_flags)
            guest = out.status == 1016
            seen = set()
            for item in jspb(body, 15, []) or []:
                model = _model_from(item, capacity, cap_field)
                if model is None or model.name in seen:
                    continue
                seen.add(model.name)
                model.available = not (guest and out.models)
                out.models.append(model)
            return out
    return out

def _model_from(item: Any, capacity: int, cap_field: int) -> Optional[AccountModel]:
    if not isinstance(item, list) or not item:
        return None
    model_id = jspb(item, 0)
    if not isinstance(model_id, str) or not model_id:
        return None
    category = str(jspb(item, 1, "") or jspb(item, 10, "") or "")
    display = str(jspb(item, 11, "") or jspb(item, 19, "") or category or model_id)
    description = str(jspb(item, 12, "") or jspb(item, 2, "") or "")
    number = jspb(item, 17)
    if not isinstance(number, int):
        number = jspb(item, 9)
    return AccountModel(id=model_id, name=model_slug(display, category, model_id), display=display,
                        description=description, category=category,
                        number=number if isinstance(number, int) else 1,
                        capacity=capacity, capacity_field=cap_field)

def is_canned_reply(text: str) -> bool:
    """Короткая служебная заглушка Google вместо ответа модели («Я всего лишь языковая модель»)."""
    text = " ".join((text or "").split())
    return bool(text) and len(text) <= CANNED_MAX_LEN and bool(_CANNED_RE.search(text.replace("’", "'")))

class FrameParser:
    """Инкрементальный разбор ответа StreamGenerate.

    Ответ начинается с «)]}'», дальше идут кадры «<длина>\\n<JSON-массив>». Старые сборки
    присылали по JSON-массиву на строку без длин — поддерживаются оба варианта: длины
    пропускаем и читаем JSON через raw_decode, неполный хвост ждёт следующих байт.
    """

    def __init__(self) -> None:
        self.buffer = ""
        self._decoder = json.JSONDecoder()
        self._prefix_done = False

    def feed(self, chunk: str) -> List[Any]:
        self.buffer += chunk
        if not self._prefix_done:
            stripped = self.buffer.lstrip()
            if len(stripped) < 4 and ")]}'".startswith(stripped):
                return []
            if stripped.startswith(")]}'"):
                self.buffer = stripped[4:]
            self._prefix_done = True
        frames: List[Any] = []
        pos = 0
        size = len(self.buffer)
        while pos < size:
            ch = self.buffer[pos]
            if ch.isspace():
                pos += 1
                continue
            if ch.isdigit():
                end = pos
                while end < size and self.buffer[end].isdigit():
                    end += 1
                if end == size:
                    break
                if self.buffer[end] in "\r\n[":
                    pos = end
                    continue
            if ch not in "[{":
                nl = self.buffer.find("\n", pos)
                if nl < 0:
                    break
                pos = nl + 1
                continue
            try:
                value, end = self._decoder.raw_decode(self.buffer, pos)
            except ValueError:
                break
            frames.append(value)
            pos = end
        self.buffer = self.buffer[pos:]
        return frames

    def flush(self) -> List[Any]:
        rest, self.buffer = self.buffer, ""
        out = []
        for line in rest.splitlines():
            line = line.strip()
            if line[:1] in "[{":
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

def _get(data: Any, path: List[int], default: Any = None) -> Any:
    for key in path:
        if isinstance(data, list) and -len(data) <= key < len(data):
            data = data[key]
        else:
            return default
    return default if data is None else data

@dataclass
class Snapshot:
    text: str = ""
    thoughts: str = ""
    images: List[str] = field(default_factory=list)
    done: bool = False
    cid: str = ""
    rid: str = ""
    media: List[Any] = field(default_factory=list)

def iter_parts(frame: Any):
    """Кадр — массив частей ["wrb.fr", null, "<json>", …]; бывает обёрнут ещё одним массивом."""
    if not isinstance(frame, list):
        return
    if frame and isinstance(frame[0], list) and frame[0] and isinstance(frame[0][0], str):
        parts = frame
    elif frame and isinstance(frame[0], str):
        parts = [frame]
    else:
        parts = [p for p in frame if isinstance(p, list)]
    for part in parts:
        if isinstance(part, list) and part and part[0] == "wrb.fr":
            yield part

def error_code(part: List[Any]) -> Optional[int]:
    code = _get(part, [5, 2, 0, 1, 0])
    return code if isinstance(code, int) and code else None

def parse_part(part: List[Any]) -> Optional[Snapshot]:
    raw = _get(part, [2])
    if not isinstance(raw, str) or not raw:
        return None
    try:
        inner = json.loads(raw)
    except ValueError:
        return None
    cid = _get(inner, [1, 0], "")
    rid = _get(inner, [1, 1], "")
    cand = pick_candidate(_get(inner, [4]))
    if cand is None:
        return Snapshot(cid=cid) if isinstance(cid, str) and cid.startswith("c_") else None
    snap = Snapshot(cid=cid if isinstance(cid, str) else "", rid=rid if isinstance(rid, str) else "")
    text = _get(cand, [1, 0], "")
    if isinstance(text, str) and _CARD_RE.match(text):
        text = _get(cand, [22, 0]) or text
    if isinstance(text, str):
        snap.text = text
    thoughts = _get(cand, [37, 0, 0], "")
    if isinstance(thoughts, str):
        snap.thoughts = thoughts
    rich = _get(cand, [12])
    if isinstance(rich, list):
        snap.media = rich_media(rich)
        for img in jspb(rich, 1, []) or []:
            url = _get(img, [0, 0, 0])
            if isinstance(url, str) and url.startswith("http"):
                snap.images.append(url)
    snap.done = _get(cand, [8, 0]) == 2
    return snap

def rich_media(rich: List[Any]) -> List[Any]:
    """Блок c[12] → сгенерированные картинки (поле 7) и видео Veo (поле 59); поля — позиционно или разреженно."""
    from .geminiweb_media import GeneratedMedia
    out: List[Any] = []
    generated = jspb(rich, 7)
    for img in (_get(generated, [0], []) if isinstance(generated, list) else []) or []:
        url = _get(img, [0, 3, 3])
        if isinstance(url, str) and url.startswith("http"):
            alt = _get(img, [0, 3, 2], "")
            out.append(GeneratedMedia("image", url, alt=alt if isinstance(alt, str) else ""))
    video = jspb(rich, 59)
    info = _get(video, [0, 0, 0]) if isinstance(video, list) else None
    urls = _get(info, [0, 7], []) if isinstance(info, list) else []
    if isinstance(urls, list) and len(urls) >= 2 and isinstance(urls[1], str) and urls[1].startswith("http"):
        out.append(GeneratedMedia("video", urls[1], thumb=urls[0] if isinstance(urls[0], str) else ""))
    return out

def pick_candidate(candidates: Any) -> Optional[List[Any]]:
    """Основной кандидат ответа: первый с идентификатором rc_… (без него — служебная запись, не ответ)."""
    if not isinstance(candidates, list):
        return None
    usable = [c for c in candidates if isinstance(c, list) and c]
    for cand in usable:
        if isinstance(cand[0], str) and cand[0]:
            return cand
    return None

def clean_text(text: str) -> str:
    text = _CODE_EVENT_RE.sub("", text or "")
    text = _ARTIFACT_RE.sub("", text)
    return text.replace("\r\n", "\n")

def delta(new: str, sent: str) -> Tuple[str, str]:
    """Снимок → (что дослать, новое «уже отправлено»). Переписанное начало не повторяем."""
    if not new or new == sent or sent.startswith(new):
        return "", sent
    if new.startswith(sent):
        return new[len(sent):], new
    if len(new) <= len(sent):
        return "", sent
    return new[len(sent):], new
