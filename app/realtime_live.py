"""Live-модели реального времени для MemuAI Live.

Обычный MemuAI Live работает конвейером «запись → распознавание → чат-модель →
озвучка». Live-модели (GPT-Live-1, Gemini Live, Grok Voice и совместимые)
слушают и говорят сами, с перебиванием и без пауз на распознавание. Этот
модуль — адаптеры их протоколов к одному простому формату, на котором
говорит интерфейс MemuAI.

Интерфейс ↔ MemuAI (WebSocket ``/api/voice/realtime``):

* клиент шлёт ``start`` (инструкции), ``audio`` (PCM16 mono base64 с частотой
  ``input_rate``), ``text``, ``interrupt``, ``mute``/``unmute``, ``end``,
  а также ``video`` (кадр JPEG base64 с камеры или экрана), ``video_end`` и
  ``screen`` (попросить MemuAI самому снимать экран, если окно не умеет);
* сервер шлёт ``ready``, ``audio`` (PCM16 с частотой ``output_rate``),
  ``user_transcript``/``assistant_transcript`` (``delta`` или ``text`` +
  ``final``), ``interrupted``, ``turn_complete``, ``delegating``,
  ``searching`` (идёт веб-поиск), ``sources`` (найденные страницы),
  ``preview`` (уменьшенный кадр экрана), ``error``, ``closed``.

Зрение и поиск у провайдеров устроены по-разному, поэтому адаптер сообщает в
``ready`` два поля. ``video``: ``stream`` — модель получает кадры непрерывно
(Gemini Live), ``turn`` — свежий кадр прикладывается к каждой реплике
(OpenAI Realtime), ``delegate`` — кадр видит модель MemuAI, которой GPT-Live
передаёт вопрос, пусто — зрения нет. ``search``: ``native`` — собственный
поиск провайдера, ``tool`` — поиск выполняет MemuAI по просьбе модели,
``delegate`` — ищет модель MemuAI, пусто — поиска нет.

Ключ API никогда не уходит в окно: соединение с провайдером открывает
процесс MemuAI, а интерфейс получает только нормализованные события.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Каталог провайдеров. ``protocol`` выбирает адаптер, ``models``/``voices`` —
#: подсказки для интерфейса; вписать можно любой ID.
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai-live": {
        "label": "OpenAI GPT-Live", "protocol": "gpt-live",
        "url": "wss://api.openai.com/v1/live/sessions",
        "models": ["gpt-live-1"],
        "voices": ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"],
        "default_voice": "marin", "input_rate": 24000, "output_rate": 24000,
        "key_hint": "platform.openai.com → API keys",
    },
    "openai-realtime": {
        "label": "OpenAI Realtime", "protocol": "openai-realtime", "dialect": "ga",
        "url": "wss://api.openai.com/v1/realtime",
        "models": ["gpt-realtime", "gpt-realtime-mini"],
        "voices": ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"],
        "default_voice": "marin", "input_rate": 24000, "output_rate": 24000,
        "key_hint": "platform.openai.com → API keys",
    },
    "gemini": {
        "label": "Google Gemini Live", "protocol": "gemini-live",
        "url": "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent",
        "fallback_url": "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent",
        "models": ["gemini-3.8-live", "gemini-3.8-live-extended-thinking", "gemini-3.1-flash-live-preview"],
        "voices": ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"],
        "default_voice": "Puck", "input_rate": 16000, "output_rate": 24000,
        "key_hint": "aistudio.google.com → Get API key",
    },
    "xai": {
        "label": "xAI Grok Voice", "protocol": "openai-realtime", "dialect": "xai",
        "url": "wss://api.x.ai/v1/realtime",
        "models": ["grok-voice-latest", "grok-voice-think-fast-2.0"],
        "voices": ["eve", "ara", "rex", "sal", "leo"],
        "default_voice": "eve", "input_rate": 24000, "output_rate": 24000,
        "key_hint": "console.x.ai → API Keys",
    },
    "custom": {
        "label": "Custom · OpenAI Realtime", "protocol": "openai-realtime", "dialect": "ga",
        "url": "", "models": [], "voices": [], "default_voice": "",
        "input_rate": 24000, "output_rate": 24000, "key_hint": "",
    },
    "custom-gemini": {
        "label": "Custom · Gemini Live", "protocol": "gemini-live",
        "url": "", "models": [], "voices": [], "default_voice": "",
        "input_rate": 16000, "output_rate": 24000, "key_hint": "",
    },
}
DEFAULT_PROVIDER = "openai-live"
TURN_DETECTION = ("auto", "server_vad", "semantic_vad")
DIALECTS = ("ga", "beta")
DELEGATION = ("memuai", "responses", "off")


def catalog() -> List[Dict[str, Any]]:
    """Каталог для окна настроек, без URL-подробностей протокола."""
    return [{"id": key, "label": row["label"], "protocol": row["protocol"],
             "models": list(row["models"]), "voices": list(row["voices"]),
             "default_voice": row["default_voice"], "custom": key.startswith("custom"),
             "key_hint": row["key_hint"]} for key, row in PROVIDERS.items()]


class RealtimeConfigError(ValueError):
    """Понятная пользователю ошибка настройки Live-модели."""


@dataclass
class LiveConfig:
    provider: str
    model: str
    voice: str
    api_key: str
    url: str
    instructions: str = ""
    turn_detection: str = "auto"
    dialect: str = "ga"
    delegation: str = "memuai"
    backend_model: str = ""
    language: str = ""
    search: bool = True

    @classmethod
    def from_preferences(cls, prefs: Dict[str, Any], instructions: str = "") -> "LiveConfig":
        provider = str(prefs.get("voice_realtime_provider") or DEFAULT_PROVIDER)
        if provider not in PROVIDERS:
            provider = DEFAULT_PROVIDER
        spec = PROVIDERS[provider]
        keys = prefs.get("voice_realtime_api_keys") if isinstance(prefs.get("voice_realtime_api_keys"), dict) else {}
        api_key = str(keys.get(provider) or "").strip()
        base_url = str(prefs.get("voice_realtime_base_url") or "").strip()
        url = base_url if (provider.startswith("custom") or base_url) else spec["url"]
        if provider.startswith("custom") and not url:
            raise RealtimeConfigError("Укажите адрес WebSocket своего Live API в настройках «Голос и Live»")
        if url:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("wss", "ws") or not parsed.hostname:
                raise RealtimeConfigError("Адрес Live API должен начинаться с wss:// (или ws:// для локального сервера)")
            if parsed.scheme == "ws" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
                raise RealtimeConfigError("Незашифрованный ws:// разрешён только для локального сервера")
        if not api_key and not provider.startswith("custom"):
            raise RealtimeConfigError(f"Добавьте API-ключ {spec['label']} в настройках «Голос и Live»")
        model = str(prefs.get("voice_realtime_model") or "").strip() or (spec["models"][0] if spec["models"] else "")
        if not model and spec["protocol"] == "gemini-live":
            raise RealtimeConfigError("Укажите ID Live-модели в настройках «Голос и Live»")
        voice = str(prefs.get("voice_realtime_voice") or "").strip() or spec["default_voice"]
        turn = str(prefs.get("voice_realtime_turn_detection") or "auto")
        dialect = str(prefs.get("voice_realtime_dialect") or spec.get("dialect") or "ga")
        if not provider.startswith("custom"):
            dialect = spec.get("dialect", "ga")
        language = str(prefs.get("voice_language") or "")
        return cls(provider=provider, model=model[:200], voice=voice[:100], api_key=api_key,
                   url=url, instructions=str(instructions or "")[:24000],
                   turn_detection=turn if turn in TURN_DETECTION else "auto",
                   dialect=dialect if dialect in DIALECTS + ("xai",) else "ga",
                   delegation=str(prefs.get("voice_realtime_delegation") or "memuai")
                   if str(prefs.get("voice_realtime_delegation") or "memuai") in DELEGATION else "memuai",
                   backend_model=str(prefs.get("voice_realtime_backend_model") or "").strip()[:200],
                   language="" if language in ("", "auto") else language[:12],
                   search=prefs.get("voice_realtime_search", True) is not False)


#: Инструменты, которые MemuAI выполняет сам по просьбе Live-модели.
SEARCH_TOOLS: List[Dict[str, Any]] = [
    {"name": "web_search",
     "description": "Search the web for fresh or factual information: news, prices, weather, schedules, "
                    "releases, anything that may have changed. Returns titles, links and short excerpts.",
     "parameters": {"type": "object",
                    "properties": {"query": {"type": "string", "description": "Focused search query"}},
                    "required": ["query"]}},
    {"name": "open_page",
     "description": "Read the text of a web page found by web_search when the excerpt is not enough.",
     "parameters": {"type": "object",
                    "properties": {"url": {"type": "string", "description": "Full http(s) address"}},
                    "required": ["url"]}},
]
MAX_FRAME_CHARS = 2_800_000   # base64 одного кадра (~2 МБ JPEG)

_SEARCH_NOTE = ("\n\nYou can look things up on the web during this conversation. Do it whenever the answer "
                "depends on fresh or checkable facts (news, prices, weather, schedules, releases, people, "
                "documentation) instead of guessing; say in a few words that you are checking, then answer "
                "from what you found and mention the source by name.")
_VIDEO_NOTES = {
    "stream": ("\n\nThe user can share their screen or camera at any moment; you then receive live video "
               "frames. Describe only what is really visible, and say so plainly when nothing is shared."),
    "turn": ("\n\nThe user can share their screen or camera; a fresh frame is then attached to each of their "
             "turns. Describe only what is really visible, and say so plainly when nothing is shared."),
    "delegate": ("\n\nThe user can share their screen or camera. You do not see it yourself: whenever they "
                 "refer to what is on the screen or in front of the camera, delegate the request — the backend "
                 "receives the latest frame and tells you what is there."),
}


@dataclass
class Adapter:
    """Базовый адаптер: превращает события провайдера в события MemuAI."""

    config: LiveConfig
    ready_sent: bool = False
    assistant_open: bool = False
    transcript: List[Dict[str, str]] = field(default_factory=list)
    #: Сообщения провайдеру, которые родились при разборе его же событий
    #: (кадр к началу реплики, повторная настройка без отвергнутого инструмента).
    outbox: List[Dict[str, Any]] = field(default_factory=list)
    #: Последний кадр камеры/экрана и его номер; ``frame_sent`` — номер уже переданного.
    frame: str = ""
    frame_mime: str = "image/jpeg"
    frame_id: int = 0
    frame_sent: int = 0
    #: Сколько раз уже упростили поиск (см. search_plan).
    search_step: int = 0
    #: Откуда берутся субтитры ответа (item_id → "audio" или "text"), чтобы не задвоить их.
    caption_source: Dict[str, str] = field(default_factory=dict)

    protocol = "base"

    @property
    def video_mode(self) -> str:
        return ""

    def search_plan(self) -> List[str]:
        """Способы поиска от лучшего к запасному; последний — без поиска."""
        return [""]

    @property
    def search_mode(self) -> str:
        if not self.config.search:
            return ""
        plan = self.search_plan()
        return plan[min(self.search_step, len(plan) - 1)]

    def degrade(self) -> bool:
        """Провайдер отверг настройку: пробуем следующий способ поиска. False — упрощать нечего."""
        if not self.config.search or self.search_step >= len(self.search_plan()) - 1:
            return False
        self.search_step += 1
        return True

    def instructions(self) -> str:
        text = _instructions_with_language(self.config)
        if self.search_mode in ("native", "tool"):
            text += _SEARCH_NOTE
        return text + _VIDEO_NOTES.get(self.video_mode, "")

    def drain(self) -> List[Dict[str, Any]]:
        items, self.outbox = self.outbox, []
        return items

    def keep_frame(self, data: str, mime: str = "image/jpeg") -> None:
        self.frame = data
        self.frame_mime = mime if mime in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
        self.frame_id += 1

    def drop_frame(self) -> None:
        self.frame = ""

    def video_messages(self, data: str, mime: str = "image/jpeg") -> List[Dict[str, Any]]:
        """Кадр камеры или экрана. По умолчанию только запоминается (режимы turn/delegate)."""
        self.keep_frame(data, mime)
        return []

    def tool_result_messages(self, call_id: str, name: str, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    @property
    def spec(self) -> Dict[str, Any]:
        return PROVIDERS[self.config.provider]

    @property
    def input_rate(self) -> int:
        return int(self.spec["input_rate"])

    @property
    def output_rate(self) -> int:
        return int(self.spec["output_rate"])

    def urls(self) -> List[str]:
        return [self.config.url]

    @property
    def yandex(self) -> bool:
        """Realtime API Yandex Cloud (wss://ai.api.cloud.yandex.net/v1/realtime/openai)."""
        host = (urllib.parse.urlsplit(self.config.url).hostname or "").lower()
        return host.endswith((".yandex.net", ".yandexcloud.net"))

    def headers(self) -> Dict[str, str]:
        key = self.config.api_key
        if not key:
            return {}
        if key.lower().startswith(("bearer ", "api-key ")):
            # Схема вписана вместе с ключом — передаём как есть.
            return {"Authorization": key}
        # Yandex принимает API-ключ только как «Api-Key …» (на Bearer отвечает
        # invalid_credentials и закрывает сессию); Bearer — для IAM-токенов «t1.…».
        if self.yandex and not key.startswith("t1."):
            return {"Authorization": f"Api-Key {key}"}
        return {"Authorization": f"Bearer {key}"}

    def setup_messages(self) -> List[Dict[str, Any]]:
        return []

    def audio_message(self, data: str) -> Optional[Dict[str, Any]]:
        return None

    def text_messages(self, text: str) -> List[Dict[str, Any]]:
        return []

    def interrupt_messages(self) -> List[Dict[str, Any]]:
        return []

    def mute_messages(self, muted: bool) -> List[Dict[str, Any]]:
        return []

    def close_messages(self) -> List[Dict[str, Any]]:
        return []

    def translate(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def ready_event(self) -> Dict[str, Any]:
        self.ready_sent = True
        return {"type": "ready", "provider": self.config.provider, "model": self.config.model,
                "voice": self.config.voice, "input_rate": self.input_rate,
                "output_rate": self.output_rate, "protocol": self.protocol,
                "supports_text": self.protocol != "gpt-live",
                "full_duplex": self.protocol == "gpt-live",
                "video": self.video_mode, "search": self.search_mode}

    # Короткая стенограмма нужна делегированию: модели MemuAI передаётся,
    # о чём шла речь, раз сам запрос делегирования её не содержит.
    def remember(self, role: str, text: str, final: bool = False) -> None:
        if not text:
            return
        if self.transcript and self.transcript[-1]["role"] == role and not self.transcript[-1].get("final"):
            self.transcript[-1]["text"] = (self.transcript[-1]["text"] + text) if not final else text
        else:
            self.transcript.append({"role": role, "text": text})
        if final:
            self.transcript[-1]["final"] = "1"
        del self.transcript[:-40]

    def transcript_text(self, limit: int = 6000) -> str:
        lines = [f"{'User' if row['role'] == 'user' else 'Assistant'}: {row['text'].strip()}"
                 for row in self.transcript if row["text"].strip()]
        return "\n".join(lines)[-limit:]


def _instructions_with_language(config: LiveConfig) -> str:
    text = config.instructions.strip()
    if config.language:
        text += f"\nPreferred spoken language: {config.language} (switch if the user switches)."
    return text


class OpenAIRealtimeAdapter(Adapter):
    """OpenAI Realtime API и совместимые (xAI Grok Voice, Azure, свои серверы)."""

    protocol = "openai-realtime"

    @property
    def video_mode(self) -> str:
        # Картинки в разговоре понимает только текущая схема OpenAI (gpt-realtime).
        # Yandex на input_image отвечает malformed_event и рвёт всю сессию (1008).
        return "turn" if self.config.dialect == "ga" and not self.yandex else ""

    def search_plan(self) -> List[str]:
        # У Grok Voice свой поиск по вебу и X; остальным ищет MemuAI. Речевые модели
        # Yandex функции не вызывают: «[TOOL_CALL_START]web_search {…}» звучит вслух
        # или ответ приходит пустым, а тип web_search рвёт сессию — поиска нет.
        if self.yandex:
            return [""]
        return ["native", "tool", ""] if self.config.dialect == "xai" else ["tool", ""]

    def _tools(self) -> List[Dict[str, Any]]:
        mode = self.search_mode
        if mode == "native":
            return [{"type": "web_search"}, {"type": "x_search"}]
        if mode == "tool":
            return [{"type": "function", **tool} for tool in SEARCH_TOOLS]
        return []

    def _frame_item(self) -> List[Dict[str, Any]]:
        """Свежий кадр отдельным сообщением: один на реплику и только если он новый."""
        if self.video_mode != "turn" or not self.frame or self.frame_sent == self.frame_id:
            return []
        self.frame_sent = self.frame_id
        return [{"type": "conversation.item.create",
                 "item": {"type": "message", "role": "user", "content": [
                     {"type": "input_image", "image_url": f"data:{self.frame_mime};base64,{self.frame}"}]}}]

    def tool_result_messages(self, call_id: str, name: str, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"type": "conversation.item.create",
                 "item": {"type": "function_call_output", "call_id": call_id,
                          "output": json.dumps(result, ensure_ascii=False)[:16000]}},
                {"type": "response.create"}]

    def urls(self) -> List[str]:
        url = self.config.url
        if self.config.model and "model=" not in url:
            url += ("&" if "?" in url else "?") + "model=" + urllib.parse.quote(self.config.model)
        return [url]

    def headers(self) -> Dict[str, str]:
        headers = super().headers()
        if self.config.dialect == "beta":
            headers["OpenAI-Beta"] = "realtime=v1"
        return headers

    def _turn(self) -> Dict[str, Any]:
        kind = self.config.turn_detection
        if kind == "auto":
            kind = "semantic_vad" if self.config.provider == "openai-realtime" else "server_vad"
        return {"type": kind}

    def setup_messages(self) -> List[Dict[str, Any]]:
        instructions = self.instructions()
        rate = self.input_rate
        if self.config.dialect == "beta":
            session: Dict[str, Any] = {
                "modalities": ["audio", "text"], "instructions": instructions,
                "input_audio_format": "pcm16", "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": self._turn(),
            }
            if self.config.voice:
                session["voice"] = self.config.voice
        elif self.config.dialect == "xai":
            session = {
                "instructions": instructions, "turn_detection": self._turn(),
                "audio": {"input": {"format": {"type": "audio/pcm", "rate": rate}},
                          "output": {"format": {"type": "audio/pcm", "rate": self.output_rate}}},
            }
            if self.config.voice:
                session["voice"] = self.config.voice
        else:
            session = {
                "type": "realtime", "instructions": instructions, "output_modalities": ["audio"],
                "audio": {
                    "input": {"format": {"type": "audio/pcm", "rate": rate},
                              "transcription": {"model": "gpt-4o-mini-transcribe"},
                              "turn_detection": self._turn()},
                    "output": {"format": {"type": "audio/pcm", "rate": self.output_rate}},
                },
            }
            if self.config.voice:
                session["audio"]["output"]["voice"] = self.config.voice
        tools = self._tools()
        if tools:
            session["tools"] = tools
            session["tool_choice"] = "auto"
        return [{"type": "session.update", "session": session}]

    def audio_message(self, data: str) -> Optional[Dict[str, Any]]:
        return {"type": "input_audio_buffer.append", "audio": data}

    def text_messages(self, text: str) -> List[Dict[str, Any]]:
        return self._frame_item() + [
            {"type": "conversation.item.create",
             "item": {"type": "message", "role": "user",
                      "content": [{"type": "input_text", "text": text[:8000]}]}},
            {"type": "response.create"}]

    def interrupt_messages(self) -> List[Dict[str, Any]]:
        return [{"type": "response.cancel"}]

    def _caption_from(self, item_id: str, source: str) -> str:
        """Первый источник живых субтитров реплики остаётся её источником."""
        if item_id not in self.caption_source:
            self.caption_source[item_id] = source
            while len(self.caption_source) > 64:
                del self.caption_source[next(iter(self.caption_source))]
        return self.caption_source[item_id]

    def translate(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        kind = str(event.get("type") or "")
        out: List[Dict[str, Any]] = []
        item_id = str(event.get("item_id") or "")[:200]
        turn = {"turn_id": item_id} if item_id else {}
        if kind in ("session.created", "session.updated") and not self.ready_sent:
            return [self.ready_event()]
        if kind == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            message = str(error.get("message") or event.get("message") or "Ошибка Live-модели")
            code = str(error.get("code") or "")
            # Отмена ответа, которого уже нет, — не ошибка для пользователя.
            if code in ("response_cancel_not_active",) or "no active response" in message.lower():
                return []
            if self.search_mode and "tool" in message.lower() and self.degrade():
                # Сервер не принял инструменты поиска: настраиваем сессию заново без них,
                # разговор при этом не рвётся.
                self.outbox.extend(self.setup_messages())
                return [{"type": "search_mode", "search": self.search_mode}]
            return [{"type": "error", "message": message[:600], "code": code[:80]}]
        if kind == "response.function_call_arguments.done":
            try:
                arguments = json.loads(event.get("arguments") or "{}")
            except (TypeError, ValueError):
                arguments = {}
            return [{"type": "tool_call", "id": str(event.get("call_id") or ""),
                     "name": str(event.get("name") or ""),
                     "arguments": arguments if isinstance(arguments, dict) else {}}]
        if kind.endswith("audio.delta") and "transcript" not in kind:
            delta = event.get("delta") or event.get("audio")
            if delta:
                self.assistant_open = True
                out.append({"type": "audio", "data": delta, **turn})
            return out
        if kind == "response.output_text.delta":
            # Yandex озвучивает ответ без output_audio_transcript.delta: живые
            # субтитры идут текстом, а у расшифровки озвучки только итоговый .done.
            text = str(event.get("delta") or "")
            if not text or self._caption_from(item_id, "text") != "text":
                return []
            self.remember("assistant", text)
            return [{"type": "assistant_transcript", "delta": text, **turn}]
        is_input = "input_audio_transcription" in kind or "input_transcript" in kind
        if "transcript" in kind or "input_audio_transcription" in kind:
            if is_input:
                if kind.endswith((".delta", ".updated")):
                    text = str(event.get("delta") or event.get("transcript") or event.get("text") or "")
                    final = kind.endswith(".updated") and bool(event.get("is_final") or event.get("final"))
                    if kind.endswith(".updated"):
                        # xAI шлёт накопленный текст, а не приращение.
                        self.remember("user", text, final=True)
                        return [{"type": "user_transcript", "text": text, "final": final, **turn}]
                    self.remember("user", text)
                    return [{"type": "user_transcript", "delta": text, **turn}]
                if kind.endswith((".completed", ".done")):
                    text = str(event.get("transcript") or event.get("text") or "")
                    self.remember("user", text, final=True)
                    return [{"type": "user_transcript", "text": text, "final": True, **turn}]
                return []
            if kind.endswith(".delta"):
                text = str(event.get("delta") or "")
                if self._caption_from(item_id, "audio") != "audio":
                    return []
                self.remember("assistant", text)
                return [{"type": "assistant_transcript", "delta": text, **turn}] if text else []
            if kind.endswith(".done"):
                text = str(event.get("transcript") or event.get("text") or "")
                self.remember("assistant", text, final=True)
                return [{"type": "assistant_transcript", "text": text, "final": True, **turn}]
            return []
        if kind == "input_audio_buffer.speech_started":
            # Кадр встаёт в историю перед звуком реплики: модель видит то же, что и пользователь.
            self.outbox.extend(self._frame_item())
            if self.assistant_open:
                self.assistant_open = False
                return [{"type": "interrupted"}, {"type": "speech_started", **turn}]
            return [{"type": "speech_started", **turn}]
        if kind == "input_audio_buffer.speech_stopped":
            return [{"type": "speech_stopped", **turn}]
        if kind == "response.done":
            self.assistant_open = False
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            if response.get("status") == "failed":
                details = response.get("status_details") or {}
                error = details.get("error") if isinstance(details, dict) else None
                message = (error or {}).get("message") if isinstance(error, dict) else ""
                return [{"type": "error", "message": str(message or "Live-модель не смогла ответить")[:600]},
                        {"type": "turn_complete"}]
            return [{"type": "turn_complete"}]
        return out


class GptLiveAdapter(Adapter):
    """OpenAI GPT-Live (``/v1/live/sessions``): полный дуплекс с делегированием."""

    protocol = "gpt-live"

    @property
    def video_mode(self) -> str:
        return "delegate" if self.config.delegation != "responses" else ""

    @property
    def search_mode(self) -> str:
        return "delegate" if self.config.search else ""

    def setup_messages(self) -> List[Dict[str, Any]]:
        session: Dict[str, Any] = {
            "model": self.config.model or "gpt-live-1",
            "instructions": self.instructions(),
            "audio": {"format": {"type": "audio/pcm", "rate": self.input_rate},
                      "output": {"voice": self.config.voice or "marin"}},
        }
        if self.config.delegation == "responses" and self.config.backend_model:
            session["delegation"] = {"type": "responses",
                                     "responses": {"model": self.config.backend_model,
                                                   **({"tools": [{"type": "web_search"}],
                                                       "tool_choice": "auto"} if self.config.search else {})}}
        else:
            # null — клиентское делегирование: сложные вопросы решает модель MemuAI.
            session["delegation"] = None
        return [{"type": "session.start", "session": session}]

    def audio_message(self, data: str) -> Optional[Dict[str, Any]]:
        return {"type": "session.input_audio.append", "audio": data}

    def mute_messages(self, muted: bool) -> List[Dict[str, Any]]:
        return [{"type": "session.input_audio.mute" if muted else "session.input_audio.unmute"}]

    def close_messages(self) -> List[Dict[str, Any]]:
        return [{"type": "session.close"}]

    def delegation_reply(self, delegation_id: str, content: str) -> Dict[str, Any]:
        return {"type": "session.thinking.append", "delegation_id": delegation_id,
                "content": content[:12000]}

    def translate(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        kind = str(event.get("type") or "")
        if kind == "session.started" and not self.ready_sent:
            return [self.ready_event()]
        if kind == "session.output_audio.delta":
            delta = event.get("delta")
            if delta:
                self.assistant_open = True
                return [{"type": "audio", "data": delta}]
            return []
        if kind == "session.input_transcript.delta":
            text = str(event.get("delta") or "")
            if self.assistant_open:
                # Пользователь заговорил поверх ответа: реплика ассистента закончена.
                self.assistant_open = False
            self.remember("user", text)
            return [{"type": "user_transcript", "delta": text}] if text else []
        if kind == "session.output_transcript.delta":
            text = str(event.get("delta") or "")
            self.assistant_open = True
            self.remember("assistant", text)
            return [{"type": "assistant_transcript", "delta": text}] if text else []
        if kind == "session.delegation.created":
            delegation = event.get("delegation") if isinstance(event.get("delegation"), dict) else {}
            if delegation.get("target", "client") == "client" and delegation.get("id"):
                return [{"type": "delegate", "id": str(delegation["id"])}]
            return []
        if kind == "session.closed":
            return [{"type": "closed", "reason": str(event.get("reason") or "")[:80]}]
        if kind == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            return [{"type": "error", "message": str(error.get("message") or "Ошибка GPT-Live")[:600],
                     "code": str(error.get("code") or "")[:80]}]
        return []


class GeminiLiveAdapter(Adapter):
    """Google Gemini Live API (BidiGenerateContent)."""

    protocol = "gemini-live"

    @property
    def video_mode(self) -> str:
        return "stream"

    def search_plan(self) -> List[str]:
        # Сначала Google Search самой модели; если она его не принимает — поиск MemuAI.
        return ["native", "tool", ""]

    def _tools(self) -> List[Dict[str, Any]]:
        mode = self.search_mode
        if mode == "native":
            return [{"googleSearch": {}}]
        if mode == "tool":
            return [{"functionDeclarations": [dict(tool) for tool in SEARCH_TOOLS]}]
        return []

    def video_messages(self, data: str, mime: str = "image/jpeg") -> List[Dict[str, Any]]:
        self.keep_frame(data, mime)
        return [{"realtimeInput": {"video": {"data": data, "mimeType": self.frame_mime}}}]

    def tool_result_messages(self, call_id: str, name: str, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"toolResponse": {"functionResponses": [
            {"id": call_id, "name": name, "response": {"result": result}}]}}]

    def urls(self) -> List[str]:
        def with_key(url: str) -> str:
            if not self.config.api_key or "key=" in url:
                return url
            return url + ("&" if "?" in url else "?") + "key=" + urllib.parse.quote(self.config.api_key)
        primary = with_key(self.config.url)
        fallback = self.spec.get("fallback_url") if self.config.url == self.spec.get("url") else ""
        return [primary] + ([with_key(fallback)] if fallback else [])

    def headers(self) -> Dict[str, str]:
        return {}

    def setup_messages(self) -> List[Dict[str, Any]]:
        model = self.config.model
        setup: Dict[str, Any] = {
            "model": model if model.startswith(("models/", "projects/")) else f"models/{model}",
            "generationConfig": {"responseModalities": ["AUDIO"]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }
        if self.config.voice:
            setup["generationConfig"]["speechConfig"] = {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.config.voice}}}
        instructions = self.instructions()
        if instructions:
            setup["systemInstruction"] = {"parts": [{"text": instructions}]}
        tools = self._tools()
        if tools:
            setup["tools"] = tools
        return [{"setup": setup}]

    def audio_message(self, data: str) -> Optional[Dict[str, Any]]:
        return {"realtimeInput": {"audio": {"data": data, "mimeType": f"audio/pcm;rate={self.input_rate}"}}}

    def text_messages(self, text: str) -> List[Dict[str, Any]]:
        return [{"clientContent": {"turns": [{"role": "user", "parts": [{"text": text[:8000]}]}],
                                   "turnComplete": True}}]

    def close_messages(self) -> List[Dict[str, Any]]:
        return [{"realtimeInput": {"audioStreamEnd": True}}]

    def translate(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if "setupComplete" in event and not self.ready_sent:
            return [self.ready_event()]
        content = event.get("serverContent")
        if isinstance(content, dict):
            if content.get("interrupted"):
                self.assistant_open = False
                out.append({"type": "interrupted"})
            transcription = content.get("inputTranscription")
            if isinstance(transcription, dict) and transcription.get("text"):
                text = str(transcription["text"])
                self.remember("user", text)
                out.append({"type": "user_transcript", "delta": text})
            turn = content.get("modelTurn")
            if isinstance(turn, dict):
                for part in turn.get("parts") or []:
                    inline = part.get("inlineData") if isinstance(part, dict) else None
                    if isinstance(inline, dict) and inline.get("data") and \
                            str(inline.get("mimeType") or "audio").startswith("audio"):
                        self.assistant_open = True
                        out.append({"type": "audio", "data": inline["data"]})
            transcription = content.get("outputTranscription")
            if isinstance(transcription, dict) and transcription.get("text"):
                text = str(transcription["text"])
                self.remember("assistant", text)
                out.append({"type": "assistant_transcript", "delta": text})
            sources = _grounding_sources(content.get("groundingMetadata"))
            if sources:
                out.append({"type": "sources", "sources": sources})
            if content.get("turnComplete"):
                self.assistant_open = False
                out.append({"type": "turn_complete"})
        call = event.get("toolCall")
        if isinstance(call, dict):
            for item in call.get("functionCalls") or []:
                if isinstance(item, dict) and item.get("name"):
                    out.append({"type": "tool_call", "id": str(item.get("id") or ""),
                                "name": str(item["name"]),
                                "arguments": item.get("args") if isinstance(item.get("args"), dict) else {}})
        if "goAway" in event:
            out.append({"type": "notice", "message": "Сервер Gemini скоро закроет сессию"})
        error = event.get("error")
        if isinstance(error, dict):
            out.append({"type": "error", "message": str(error.get("message") or "Ошибка Gemini Live")[:600]})
        return out


def _grounding_sources(metadata: Any) -> List[Dict[str, str]]:
    """Источники встроенного поиска Gemini: адрес и название страницы."""
    if not isinstance(metadata, dict):
        return []
    rows: List[Dict[str, str]] = []
    for chunk in metadata.get("groundingChunks") or []:
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if isinstance(web, dict) and str(web.get("uri") or "").startswith(("http://", "https://")):
            rows.append({"url": str(web["uri"])[:2000], "title": str(web.get("title") or "")[:300]})
    return rows[:12]


def build_adapter(config: LiveConfig) -> Adapter:
    protocol = PROVIDERS[config.provider]["protocol"]
    if protocol == "gpt-live":
        return GptLiveAdapter(config)
    if protocol == "gemini-live":
        return GeminiLiveAdapter(config)
    return OpenAIRealtimeAdapter(config)


def decode_frame(message: Any) -> Dict[str, Any]:
    """Кадр провайдера → dict. Gemini присылает JSON и в бинарных кадрах."""
    if isinstance(message, (bytes, bytearray)):
        try:
            message = bytes(message).decode("utf-8")
        except UnicodeDecodeError:
            return {}
    try:
        value = json.loads(message)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def redact(text: str, config: LiveConfig) -> str:
    """Ключ провайдера не должен попасть ни в окно, ни в журнал."""
    value = str(text or "")
    if config.api_key:
        value = value.replace(config.api_key, "***")
        value = value.replace(urllib.parse.quote(config.api_key), "***")
    return value


#: Сообщения MemuAI Live (этот модуль и ретранслятор в playground_api) по-английски — для окна на английском.
_MESSAGES_EN = (
    (r"Укажите адрес WebSocket своего Live API в настройках «Голос и Live»",
     "Enter the WebSocket address of your Live API in “Voice & Live” settings"),
    (r"Адрес Live API должен начинаться с wss:// \(или ws:// для локального сервера\)",
     "The Live API address must start with wss:// (or ws:// for a local server)"),
    (r"Незашифрованный ws:// разрешён только для локального сервера", "Unencrypted ws:// is allowed only for a local server"),
    (r"Добавьте API-ключ (.+) в настройках «Голос и Live»", r"Add the \1 API key in “Voice & Live” settings"),
    (r"Укажите ID Live-модели в настройках «Голос и Live»", "Enter the Live model ID in “Voice & Live” settings"),
    (r"Ошибка Live-модели", "Live model error"),
    (r"Live-модель не смогла ответить", "The live model could not answer"),
    (r"Ошибка GPT-Live", "GPT-Live error"),
    (r"Ошибка Gemini Live", "Gemini Live error"),
    (r"Сервер Gemini скоро закроет сессию", "The Gemini server will close the session soon"),
    (r"Live-модель недоступна", "The live model is unavailable"),
    (r"Live-модель не подтвердила настройку сессии за 20 секунд",
     "The live model did not confirm the session setup within 20 seconds"),
    (r"Ошибка настройки сессии", "Session setup error"),
    (r"Провайдер отклонил ключ API \(HTTP (\S+)\)\. Проверьте ключ и доступ к модели",
     r"The provider rejected the API key (HTTP \1). Check the key and access to the model"),
    (r"Провайдер ответил HTTP (\S+) при подключении", r"The provider answered HTTP \1 while connecting"),
    (r"Провайдер закрыл соединение \(([^)]*)\)(.*)", r"The provider closed the connection (\1)\2"),
    (r"Интерфейс не начал сессию Live", "The window did not start the Live session"),
    (r"Первое сообщение должно быть start", "The first message must be start"),
    (r"Не удалось снять экран: (.*)", r"Could not capture the screen: \1"),
    (r"Live-модель закрыла соединение: (.*)", r"The live model closed the connection: \1"),
)


def localize(text: str, lang: str) -> str:
    """Текст ошибки/уведомления Live на языке окна (``en``); чужие (провайдерские) тексты — как есть."""
    value = str(text or "")
    if lang != "en":
        return value
    for pattern, english in _MESSAGES_EN:
        if re.fullmatch(pattern, value, flags=re.S):
            return re.sub(pattern, english, value, count=1, flags=re.S)
    return value
