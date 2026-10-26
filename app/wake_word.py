"""Wake word for Quick Ask (Round 27): «Привет, Мему» / «Hey Memu» opens the popup already listening.

Fully local and offline: the microphone is read in this process (``app/wake_audio.py``), recognised by Vosk
(``pip install vosk``; small ru/en models ~45 MB, downloaded on demand like Piper/GGUF), nothing leaves the PC.

Pipeline (one background thread, :class:`WakeService`):

  mic 100 ms chunks → :class:`EnergyGate` (adaptive noise floor; silence never reaches the recogniser)
  → :class:`Detector` (Vosk with a restricted grammar: the phrase + ~100 frequent "decoy" words + ``[unk]``)
  → hit (the phrase as consecutive words, confidence ≥ threshold from the sensitivity slider)
  → command capture (:class:`Session`, still in Python: the command said in one breath with the phrase is
    kept — the audio after the phrase's last word) → the Quick Ask window shows a listening state
    (``quick/plugins/wake.js``), sends the WAV to the usual ``/api/voice/transcribe {quick: true}`` and the text
    as a normal request. If that STT is not ready the offline Vosk transcript is used.

Why decoys: in small Vosk models a grammar is a loop of WORDS, not phrases. With only ``["привет мемо",
"[unk]"]`` the sentence «привет мама» comes back as «привет мемо» with confidence 1.0. Frequent words give the
decoder something better to pick, so other speech no longer collapses onto the phrase.

Why aliases: brand words are not in the model's dictionary («мему», «memu»). A word the model does not know
is replaced by sound-alikes it does know (vowel/voicing swaps, a split into two words): «мему» → «мемо».
The settings show this as a warning; a phrase with an unmatched word is refused.

Anti false triggers: sensitivity → confidence threshold, cooldown, nothing while MemuAI speaks (TTS), while
MemuAI Live or voice input is on (:meth:`WakeService.busy`), while the session is locked (the mic is released).
"""
from __future__ import annotations

import base64
import io
import itertools
import json
import logging
import math
import re
import shutil
import struct
import sys
import threading
import time
import wave
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import wake_audio
from .wake_audio import CHUNK_MS, RATE, MicError

log = logging.getLogger("memuai.wake")

MODELS: Dict[str, Dict[str, Any]] = {
    "ru": {"id": "vosk-model-small-ru-0.22", "size": 46236750,
           "sha256": "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4",
           "url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
           "phrase": "привет мему", "title": {"ru": "Русский", "en": "Russian"}},
    "en": {"id": "vosk-model-small-en-us-0.15", "size": 41205931,
           "sha256": "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
           "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
           "phrase": "hey memu", "title": {"ru": "Английский", "en": "English"}},
}

DECOYS: Dict[str, Sequence[str]] = {
    "ru": ("я", "ты", "он", "она", "мы", "вы", "они", "мама", "папа", "меня", "мне", "мой", "моя", "мир", "мэр",
           "мимо", "мило", "мало", "мыло", "может", "много", "море", "мясо", "мем", "мемы", "меню", "память",
           "приветик", "как", "дела", "что", "это", "да", "нет", "не", "и", "в", "на", "с", "по", "у", "о", "а",
           "но", "так", "там", "тут", "вот", "где", "кто", "когда", "сегодня", "погода", "открой", "включи",
           "скажи", "давай", "хорошо", "спасибо", "пока", "здравствуй", "эй", "слушай", "алло", "ну", "ещё", "уже",
           "очень", "можно", "надо", "буду", "было", "есть", "был", "метро", "вечер", "утро", "день", "время",
           "сколько", "почему", "зачем", "только", "тоже", "просто", "сейчас", "потом", "всё", "все", "его",
           "её", "их", "мою", "твой", "сам", "себя", "который", "если", "бы", "ли", "же", "до", "за", "от", "из",
           "для", "без", "под", "над", "при", "про", "через", "после", "привет", "доброе", "добрый", "окей"),
    "en": ("i", "you", "he", "she", "we", "they", "it", "me", "my", "mom", "mum", "man", "many", "may", "maybe",
           "made", "make", "memory", "menu", "meme", "mean", "move", "more", "much", "mute", "music", "movie",
           "hello", "hi", "hay", "hate", "high", "how", "what", "where", "when", "who", "why", "the", "a", "an",
           "and", "or", "to", "of", "in", "on", "is", "are", "was", "be", "do", "does", "go", "yes", "no", "not",
           "okay", "please", "thanks", "open", "play", "stop", "turn", "set", "tell", "weather", "today", "time",
           "now", "here", "there", "this", "that", "can", "could", "would", "will", "just", "so", "well", "good",
           "new", "day", "one", "two", "three", "about", "with", "for", "at", "by", "from", "up", "out", "if",
           "all", "some", "any", "like", "know", "think", "see", "look", "want", "need", "get", "say", "hey",
           "morning", "night", "people", "really", "right", "let", "let's", "call", "come"),
}

_SOUND_ALIKE: Dict[str, Dict[str, str]] = {
    "ru": {"е": "эиё", "э": "еи", "и": "ыеэ", "ы": "и", "о": "ау", "а": "оя", "у": "оюы", "ю": "уе", "я": "ае",
           "ё": "ео", "б": "п", "п": "б", "в": "ф", "ф": "в", "г": "к", "к": "г", "д": "т", "т": "д", "ж": "ш",
           "ш": "жщ", "щ": "ш", "з": "с", "с": "з", "ь": "", "ъ": ""},
    "en": {"a": "eiou", "e": "aio", "i": "eya", "o": "ua", "u": "oe", "y": "ie", "b": "p", "p": "b", "d": "t",
           "t": "d", "g": "k", "k": "gc", "c": "k", "s": "z", "z": "s", "v": "f", "f": "v"},
}
_SHORT_OK = {"ru": set("уваиоскя"), "en": {"a", "i", "o"}}

DEFAULTS: Dict[str, Any] = {"enabled": False, "phrase": "", "lang": "", "device": "", "sensitivity": 50,
                            "cooldown": 3, "chime": True, "glow": True}
SENSITIVITY_RANGE = (0, 100)
COOLDOWN_RANGE = (1, 30)
MAX_VARIANTS = 12

NO_SPEECH, END_SILENCE, MAX_COMMAND = 6.0, 1.2, 15.0

TEST_TTL = 20.0

BUSY_MAX = 120.0

SESSION_TTL = 120.0

def _tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru

def _root() -> Path:
    from .pgdata import MEMU_DIR
    return Path(MEMU_DIR)

def settings_file() -> Path:
    return _root() / "wake_word.json"

def models_root() -> Path:
    return _root() / "wake-models"

def ui_language() -> str:
    try:
        from .pgdata import load_preferences
        return "en" if load_preferences().get("language") == "en" else "ru"
    except Exception:
        return "ru"

def normalize_phrase(value: Any) -> str:
    text = str(value or "").lower().replace("’", "'")
    text = re.sub(r"[^\w\s'-]+", " ", text)
    text = re.sub(r"[-_]+", " ", text)
    return " ".join(text.split())[:80]

def normalize_settings(raw: Any, lang_hint: str = "ru") -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    out["enabled"] = bool(raw.get("enabled", False))
    lang = str(raw.get("lang") or "")
    out["lang"] = lang if lang in MODELS else ("en" if lang_hint == "en" else "ru")
    out["phrase"] = normalize_phrase(raw.get("phrase"))
    out["device"] = str(raw.get("device") or "")[:120]

    def number(key: str, low: int, high: int) -> int:
        try:
            value = float(raw.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            value = DEFAULTS[key]
        if value != value or value in (float("inf"), float("-inf")):
            value = DEFAULTS[key]
        return int(max(low, min(high, round(value))))

    out["sensitivity"] = number("sensitivity", *SENSITIVITY_RANGE)
    out["cooldown"] = number("cooldown", *COOLDOWN_RANGE)
    out["chime"] = bool(raw.get("chime", True))
    out["glow"] = bool(raw.get("glow", True))
    return out

def load_settings() -> Dict[str, Any]:
    try:
        data = json.loads(settings_file().read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    return normalize_settings(data.get("settings") if isinstance(data, dict) else {}, ui_language())

def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    current = load_settings()
    merged = dict(current)
    merged.update({k: v for k, v in (patch or {}).items() if k in DEFAULTS})
    clean = normalize_settings(merged, current["lang"])
    from .storage import save_json
    settings_file().parent.mkdir(parents=True, exist_ok=True)
    save_json(settings_file(), {"version": 1, "settings": clean}, backup=False)
    return clean

def effective_phrase(settings: Dict[str, Any]) -> str:
    return settings.get("phrase") or MODELS[settings["lang"]]["phrase"]

def threshold(sensitivity: float) -> float:
    """Minimum mean word confidence: 0.85 at sensitivity 0 … 0.35 at 100 (0.6 by default).

    Picked on synthetic ru/en speech (espeak-ng, 6 speeds/pitches): the default phrase scored 0.54–1.0, while other
    phrases either did not match at all or matched only as near-homophones («привет мимо») at any threshold."""
    return 0.85 - 0.005 * max(0.0, min(100.0, float(sensitivity)))

ALIAS_BONUS = 0.1

def _variants_of(word: str, lang: str) -> List[str]:
    """Spellings that sound like ``word``: one swap, one deletion, then two swaps (ordered by distance)."""
    table = _SOUND_ALIKE.get(lang, {})
    out: List[str] = []
    seen = {word}

    def add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)

    singles = []
    for i, ch in enumerate(word):
        for sub in table.get(ch, ""):
            singles.append(word[:i] + sub + word[i + 1:])
        if table.get(ch) == "":
            singles.append(word[:i] + word[i + 1:])
    for cand in singles:
        add(cand)
    if len(word) > 3:
        add(word[:-1])
    for cand in list(singles):
        for i, ch in enumerate(cand):
            for sub in table.get(ch, ""):
                add(cand[:i] + sub + cand[i + 1:])
    return out

def analyze_phrase(phrase: str, lang: str, known: Callable[[str], bool]) -> Dict[str, Any]:
    """Words of the phrase checked against the model's dictionary (``known``), sound-alike aliases for unknown words.

    Returns ``{"phrase", "words": [{"word", "known", "alias": [..]}], "variants": [[w, ...], ...], "ok", "problem"}``
    where ``problem`` is a ``{"ru", "en"}`` message when the phrase cannot be used.
    """
    phrase = normalize_phrase(phrase)
    words = phrase.split()
    result: Dict[str, Any] = {"phrase": phrase, "words": [], "variants": [], "ok": False, "problem": None}
    if not 2 <= len(words) <= 4:
        result["problem"] = {"ru": "Фраза должна состоять из 2–4 слов: например, «Привет, Мему».",
                             "en": "The phrase must have 2–4 words, e.g. “Hey Memu”."}
        return result
    options: List[List[str]] = []
    missing = []
    for word in words:
        row: Dict[str, Any] = {"word": word, "known": False, "alias": []}
        if known(word):
            row["known"] = True
            options.append([word])
        else:
            spelled = word.replace("е", "ё") if lang == "ru" else ""
            yo = [word[:i] + "ё" + word[i + 1:] for i, ch in enumerate(word) if ch == "е"] if lang == "ru" else []
            exact = next((w for w in yo + ([spelled] if spelled != word else []) if known(w)), "")
            if exact:
                row["known"] = True
                row["spelling"] = exact
                options.append([exact])
            else:

                alias = [cand for cand in _variants_of(word, lang) if known(cand)][:1]
                if not alias:
                    for i in range(2, len(word) - 1):
                        left, right = word[:i], word[i:]
                        ok_piece = lambda p: len(p) >= 2 or p in _SHORT_OK.get(lang, set())  # noqa: E731
                        if ok_piece(left) and ok_piece(right) and known(left) and known(right):
                            alias.append(left + " " + right)
                            break
                row["alias"] = alias
                if alias:
                    options.append(alias)
                else:
                    missing.append(word)
        result["words"].append(row)
    if missing:
        quoted = ", ".join(f"«{w}»" for w in missing)
        quoted_en = ", ".join(f"“{w}”" for w in missing)
        result["problem"] = {
            "ru": f"Модель не знает слово {quoted} и не нашла похожего по звучанию. Выберите другое слово.",
            "en": f"The model does not know {quoted_en} and found nothing that sounds alike. Pick another word."}
        return result
    variants = []
    for combo in itertools.product(*options):
        variants.append(" ".join(combo).split())
        if len(variants) >= MAX_VARIANTS:
            break
    result["variants"] = variants
    result["ok"] = True
    return result

def grammar_for(variants: List[List[str]], lang: str, known: Callable[[str], bool],
                phrase: str = "") -> List[str]:
    """Phrase variants + decoys + ``[unk]``. Decoys that sound like a phrase word (or like the user's own spelling
    of it) are left out: a near-homophone in the grammar takes confidence away from the phrase itself."""
    taken = {w for v in variants for w in v}
    near = set()
    for word in taken | set(normalize_phrase(phrase).split()):
        near.update(_variants_of(word, lang))
    decoys = [w for w in DECOYS.get(lang, ()) if w not in taken and w not in near and known(w)]
    return [" ".join(v) for v in variants] + decoys + ["[unk]"]

def alias_words(analysis: Dict[str, Any]) -> frozenset:
    return frozenset(w for row in analysis.get("words") or [] for alias in row.get("alias") or [] for w in alias.split())

def match_hit(result: Dict[str, Any], variants: List[List[str]], min_conf: float,
              aliases: frozenset = frozenset()) -> Optional[Dict[str, Any]]:
    """Find a variant as consecutive words of a Vosk result (``SetWords(True)``) with enough confidence:
    the mean ≥ ``min_conf`` and every word ≥ ``min_conf`` − 0.15 (alias words get :data:`ALIAS_BONUS`)."""
    words = [w for w in (result or {}).get("result") or [] if isinstance(w, dict)]
    tokens = [str(w.get("word", "")) for w in words]
    best = None
    for variant in variants:
        n = len(variant)
        for i in range(0, len(tokens) - n + 1):
            if tokens[i:i + n] != variant:
                continue
            confs = [min(1.0, float(words[j].get("conf", 0.0)) + (ALIAS_BONUS if tokens[j] in aliases else 0.0))
                     for j in range(i, i + n)]
            mean = sum(confs) / n
            if mean >= min_conf and min(confs) >= min_conf - 0.15 and (best is None or mean > best["conf"]):
                best = {"conf": round(mean, 3), "start": float(words[i].get("start", 0.0)),
                        "end": float(words[i + n - 1].get("end", 0.0)), "words": variant}
    return best

class EnergyGate:
    """Opens on speech-like energy above an adaptive noise floor; hands 500 ms of pre-roll to the recogniser
    (a fresh recogniser needs some lead-in: with 300 ms synthetic speech at some speeds was missed).

    ``push(chunk, level)`` → ``(chunks_to_feed, closed_now)``.
    """

    def __init__(self, ratio: float = 3.0, floor_min: float = 0.004, hang: int = 7, preroll: int = 5) -> None:
        self.ratio, self.floor_min, self.hang = ratio, floor_min, hang
        self.floor = 0.0
        self.open = False
        self.quiet = 0
        self.pre: deque = deque(maxlen=preroll)

    def speech_level(self) -> float:
        return max(self.floor_min * self.ratio, self.floor * self.ratio)

    def push(self, chunk: bytes, level: float):
        if not self.floor:
            self.floor = max(level, self.floor_min)
        loud = level > self.speech_level()
        if not self.open:
            if loud:
                self.open, self.quiet = True, 0
                feed = list(self.pre) + [chunk]
                self.pre.clear()
                return feed, False

            alpha = 0.05 if level > self.floor else 0.15
            self.floor = max(self.floor_min, self.floor + (level - self.floor) * alpha)
            self.pre.append(chunk)
            return [], False
        self.quiet = 0 if level > self.speech_level() / 1.5 else self.quiet + 1
        if self.quiet >= self.hang:
            self.open = False
            return [chunk], True
        return [chunk], False

    def reset(self) -> None:
        self.open, self.quiet = False, 0
        self.pre.clear()

class Detector:
    """Vosk with the restricted grammar. ONE recogniser for the whole listening run, fed only while the gate is open:
    its speaker/channel adaptation carries over between utterances (a fresh recogniser per utterance turned
    «Привет, Мему» after a short lead-in into «без» on synthetic speech). Each utterance ends with
    ``FinalResult()``, which resets decoding but keeps the adaptation and the running timestamps.

    Word timestamps count every sample ever fed, so the fed audio is kept (the last :data:`KEEP_SECONDS`) with its
    offset and :meth:`tail` can return what was said after the phrase.
    """

    KEEP_SECONDS = 20

    def __init__(self, model: Any, vosk: Any, grammar: List[str], variants: List[List[str]], min_conf: float,
                 aliases: frozenset = frozenset()) -> None:
        self.model, self.vosk = model, vosk
        self.grammar = json.dumps(grammar, ensure_ascii=False)
        self.variants, self.min_conf, self.aliases = variants, min_conf, aliases
        self.rec = None
        self.audio = bytearray()
        self.base = 0
        self.speaking = False

    def _ensure(self) -> Any:
        if self.rec is None:
            self.rec = self.vosk.KaldiRecognizer(self.model, RATE, self.grammar)
            self.rec.SetWords(True)
            self.audio = bytearray()
            self.base = 0
        return self.rec

    def accept(self, chunk: bytes) -> Optional[Dict[str, Any]]:
        rec = self._ensure()
        self.speaking = True
        self.audio += chunk
        extra = len(self.audio) - self.KEEP_SECONDS * RATE * 2
        if extra > 0:
            del self.audio[:extra]
            self.base += extra // 2
        if rec.AcceptWaveform(chunk):
            return match_hit(json.loads(rec.Result()), self.variants, self.min_conf, self.aliases)
        return None

    def finish(self) -> Optional[Dict[str, Any]]:
        """End the utterance (the gate closed): the last words, then a clean decoder for the next one."""
        if self.rec is None or not self.speaking:
            return None
        self.speaking = False
        return match_hit(json.loads(self.rec.FinalResult()), self.variants, self.min_conf, self.aliases)

    def tail(self, hit: Dict[str, Any]) -> bytes:
        """Audio fed after the phrase's last word (+150 ms): the start of a command said in one breath."""
        start = int((hit["end"] + 0.15) * RATE) - self.base
        return bytes(self.audio[max(0, start) * 2:])

def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()

def chime_pcm(rate: int = 22050) -> bytes:
    """A soft two-note «I'm listening» chime (≈ 0.22 s)."""
    samples = []
    for freq, length in ((880.0, 0.09), (1318.5, 0.13)):
        count = int(rate * length)
        for i in range(count):
            env = min(1.0, i / (rate * 0.008)) * (1.0 - i / count) ** 1.6
            samples.append(int(0.22 * 32767 * env * math.sin(2 * math.pi * freq * i / rate)))
    return struct.pack("<%dh" % len(samples), *samples)

def audio_seconds(audio: bytes, mime: str) -> float:
    """Length of synthesised speech, to mute the wake word while it plays (WAV exactly, else ~128 kbit/s)."""
    if "wav" in (mime or "") and audio[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(audio)) as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except Exception:
            pass
    return len(audio) / 16000.0

class Session:
    """The command after the phrase: audio kept in Python, ended by silence (see NO_SPEECH/END_SILENCE/MAX_COMMAND)."""

    _ids = itertools.count(1)

    def __init__(self, recognizer: Any = None, mute_until: float = 0.0) -> None:
        self.id = next(self._ids)
        self.audio = bytearray()
        self.phase = "listening"
        self.heard = False
        self.silence = 0.0
        self.level = 0.0
        self.partial = ""
        self.text = ""
        self.rec = recognizer
        self.mute = mute_until
        self.created = time.time()

    @property
    def seconds(self) -> float:
        return len(self.audio) / 2 / RATE

    def feed(self, chunk: bytes, level: float, speech_level: float) -> None:
        if self.phase != "listening":
            return
        self.audio += chunk
        self.level = level
        if self.rec is not None:
            try:
                if self.rec.AcceptWaveform(chunk):
                    text = json.loads(self.rec.Result()).get("text", "")
                    self.text = (self.text + " " + text).strip()
                    self.partial = ""
                else:
                    self.partial = json.loads(self.rec.PartialResult()).get("partial", "")
            except Exception:
                self.rec = None
        voiced = level > speech_level and self.seconds > self.mute
        if voiced:
            self.heard, self.silence = True, 0.0
        else:
            self.silence += len(chunk) / 2 / RATE
        if self.heard and self.silence >= END_SILENCE or self.seconds >= MAX_COMMAND:
            self.close("captured")
        elif not self.heard and self.seconds >= NO_SPEECH:
            self.close("empty")

    def close(self, phase: str) -> None:
        if self.phase != "listening":
            return
        if self.rec is not None and phase == "captured":
            try:
                text = json.loads(self.rec.FinalResult()).get("text", "")
                self.text = (self.text + " " + text).strip()
            except Exception:
                pass
        self.rec = None
        self.partial = ""
        self.phase = phase

    def public(self, with_audio: bool = False) -> Dict[str, Any]:
        out = {"id": self.id, "phase": self.phase, "level": round(self.level, 4), "heard": self.heard,
               "partial": (self.text + " " + self.partial).strip(), "seconds": round(self.seconds, 2)}
        if self.phase == "captured":
            out["text"] = self.text
            if with_audio:
                out["audio"] = "data:audio/wav;base64," + base64.b64encode(wav_bytes(bytes(self.audio))).decode("ascii")
        return out

class _Job:
    """The duck-typed job ``local_models._fetch`` expects (cancel / done / speed)."""

    def __init__(self, lang: str) -> None:
        self.lang = lang
        self.cancel = threading.Event()
        self.done = 0
        self.total = 0
        self.speed = 0.0
        self.state = "downloading"
        self.error: Optional[Dict[str, str]] = None

    def public(self) -> Dict[str, Any]:
        progress = int(self.done * 95 / self.total) if self.total else 0
        if self.state == "unpacking":
            progress = 97
        elif self.state == "done":
            progress = 100
        return {"state": self.state, "progress": progress, "done": self.done, "total": self.total,
                "speed": int(self.speed), "error": self.error}

def _download_problem(exc: BaseException) -> Dict[str, str]:
    """A ru/en message for a failed download. ``local_models._fetch`` speaks Russian about «Hugging Face» (its usual
    host); here the host is alphacephei.com, so its messages are recognised and re-worded, never shown as is."""
    text = str(exc)
    low = text.lower()
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return {"ru": "Недостаточно места на диске: загрузка остановлена. Освободите место и повторите — скачанное продолжится.",
                "en": "Not enough disk space: the download stopped. Free some space and retry — it resumes."}
    if "sha-256" in low:
        return {"ru": "Контрольная сумма модели не совпала: файл повреждён и удалён. Повторите загрузку.",
                "en": "The model checksum did not match: the file was corrupt and has been deleted. Retry the download."}
    if "оборвалась" in low or "неполный" in low:
        return {"ru": "Загрузка оборвалась. Нажмите «Повторить» — она продолжится с того же места.",
                "en": "The download was cut off. Press “Retry” — it resumes where it stopped."}
    match = re.search(r"http (\d+)", low)
    if match or "не отдал файл" in low:
        code = match.group(1) if match else "403"
        return {"ru": f"Сервер моделей Vosk ответил HTTP {code}. Повторите позже.",
                "en": f"The Vosk model server answered HTTP {code}. Try again later."}
    if "повреждён" in low or "zip" in low or "archive" in low:
        return {"ru": "Архив модели повреждён и удалён. Повторите загрузку.",
                "en": "The model archive was corrupt and has been deleted. Retry the download."}
    if "размер" in low or "диапазон" in low:
        return {"ru": "Сервер отдал файл не того размера. Повторите загрузку.",
                "en": "The server sent a file of the wrong size. Retry the download."}
    if isinstance(exc, (OSError, ConnectionError)) or type(exc).__module__.startswith("httpx"):
        return {"ru": "Нет связи с сервером моделей (alphacephei.com). Проверьте интернет и повторите — скачанное продолжится.",
                "en": "Cannot reach the model server (alphacephei.com). Check the connection and retry — it resumes."}
    return {"ru": f"Не удалось скачать модель: {type(exc).__name__}", "en": f"Could not download the model: {type(exc).__name__}"}

def model_dir(lang: str) -> Path:
    return models_root() / MODELS[lang]["id"]

def model_ready(lang: str) -> bool:
    folder = model_dir(lang)
    return (folder / "am" / "final.mdl").is_file() and (folder / "conf" / "model.conf").is_file() \
        and (folder / "graph").is_dir()

def _check_zip(path: Path) -> None:
    from .local_models import LocalModelError
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise LocalModelError(f"архив zip повреждён ({exc})") from None
    if not any(name.endswith("am/final.mdl") for name in names):
        raise LocalModelError("в архиве zip нет модели Vosk")

def _unpack(archive: Path, lang: str) -> None:
    root = models_root()
    target = model_dir(lang)
    staging = root / f".{target.name}-unpack"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                name = member.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValueError("unsafe path in the model archive")
            zf.extractall(staging)
        inner = next((p.parent.parent for p in staging.rglob("final.mdl") if p.parent.name == "am"), None)
        if inner is None:
            raise ValueError("no Vosk model in the archive")
        if target.exists():
            shutil.rmtree(target)
        inner.replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

def _download(job: _Job, on_done: Callable[[], None]) -> None:
    import httpx
    from . import local_models
    meta = MODELS[job.lang]
    root = models_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        archive = root / (meta["id"] + ".zip")
        part = archive.with_name(archive.name + ".part")
        job.total = meta["size"]
        have = part.stat().st_size if part.exists() else 0
        needed = (meta["size"] - have) + meta["size"] * 2
        free = shutil.disk_usage(root).free
        if needed > free:
            mb = lambda v: f"{v / 1024 ** 2:.0f}"  # noqa: E731
            job.state = "error"
            job.error = {"ru": f"Недостаточно места на диске: нужно {mb(needed)} МБ, свободно {mb(free)} МБ.",
                         "en": f"Not enough disk space: {mb(needed)} MB needed, {mb(free)} MB free."}
            return
        job.done = have
        if not archive.exists():
            from .version import APP_VERSION
            with httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=True,
                              headers={"User-Agent": f"MemuAI/{APP_VERSION}"}) as client:
                local_models._fetch(client, meta["url"], archive, job, {}, expected_size=meta["size"],
                                    expected_sha256=meta["sha256"], check=_check_zip)
        if job.cancel.is_set():
            raise InterruptedError()
        job.state = "unpacking"
        _unpack(archive, job.lang)
        archive.unlink(missing_ok=True)
        job.state = "done"
        on_done()
    except InterruptedError:
        job.state = "cancelled"
    except Exception as exc:
        job.state = "error"
        job.error = _download_problem(exc)
        log.warning("wake word model download failed: %s", exc)

def delete_model(lang: str) -> None:
    shutil.rmtree(model_dir(lang), ignore_errors=True)
    for leftover in models_root().glob(MODELS[lang]["id"] + ".zip*"):
        leftover.unlink(missing_ok=True)

def engine_info() -> Dict[str, Any]:
    """Is the ``vosk`` package importable? ``installable`` — a source install may pip-install it from Settings."""
    try:
        import vosk  # noqa: F401
        return {"available": True, "installable": False}
    except Exception:
        return {"available": False, "installable": not getattr(sys, "frozen", False)}

def _import_vosk():
    import vosk
    try:
        vosk.SetLogLevel(-1)
    except Exception:
        pass
    return vosk

class WakeService:
    """The background listener. Thread-safe public methods; the work runs on ``memuai-wake``."""

    def __init__(self, *, source_factory: Callable[[str], Any] = wake_audio.open_source,
                 vosk_loader: Callable[[], Any] = _import_vosk,
                 locked: Callable[[], bool] = wake_audio.session_locked,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.source_factory = source_factory
        self.vosk_loader = vosk_loader
        self.locked = locked
        self.clock = clock
        self.app: Any = None
        self.opener: Optional[Callable[[Dict[str, Any]], Any]] = None
        self.settings = dict(DEFAULTS, lang="ru")
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.generation = 0
        self.state = "off"
        self.reason = ""
        self.error: Optional[Dict[str, str]] = None
        self.level = 0.0
        self.last_hit: Optional[Dict[str, Any]] = None
        self.test_until = 0.0
        self.cooldown_until = 0.0
        self.busy_until: Dict[str, float] = {}
        self.holds: Dict[str, int] = {}
        self.session: Optional[Session] = None
        self.jobs: Dict[str, _Job] = {}
        self.analysis: Optional[Dict[str, Any]] = None
        self._model_cache: Dict[str, Any] = {}
        self._tray_on = False
        self._loaded = False

    def attach(self, app: Any = None, opener: Optional[Callable[[Dict[str, Any]], Any]] = None) -> None:
        """Desktop start-up: remember the app (for ``app.state.quick_ask``) and start if enabled."""
        self.app = app
        if opener is not None:
            self.opener = opener
        self.reload()

    def ensure_loaded(self) -> None:
        """The saved settings, once (the desktop app calls :meth:`attach`; a dev server only serves the API)."""
        if not self._loaded:
            self.reload()

    def reload(self) -> Dict[str, Any]:
        settings = load_settings()
        with self.lock:
            self._loaded = True
            self.settings = settings
            self.generation += 1
        self.apply()
        return settings

    def shutdown(self) -> None:
        self.stop_event.set()
        self.wake.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._tray(False)

    def busy(self, source: str, seconds: float) -> None:
        """Mute the wake word for ``seconds`` because of ``source`` (tts / live / voice …); 0 clears it."""
        source = re.sub(r"[^a-z0-9_-]", "", str(source or "").lower())[:24] or "other"
        with self.lock:
            if seconds and seconds > 0:
                self.busy_until[source] = self.clock() + min(float(seconds), BUSY_MAX)
            else:
                self.busy_until.pop(source, None)

    def hold(self, source: str, on: bool) -> None:
        """A server-side mute without expiry (MemuAI Live: the whole WebSocket session); counted, so overlaps nest."""
        with self.lock:
            count = self.holds.get(source, 0) + (1 if on else -1)
            if count > 0:
                self.holds[source] = count
                if self.session is not None:
                    self.session.close("cancelled")
            else:
                self.holds.pop(source, None)

    def busy_reason(self) -> str:
        now = self.clock()
        with self.lock:
            if self.holds:
                return next(iter(sorted(self.holds)))
            for source, until in list(self.busy_until.items()):
                if until <= now:
                    self.busy_until.pop(source, None)
            return next(iter(sorted(self.busy_until)), "")

    def set_test(self, on: bool) -> None:
        with self.lock:
            self.test_until = self.clock() + TEST_TTL if on else 0.0
            if on:
                self.last_hit = None
        self.apply()

    def testing(self) -> bool:
        return self.test_until > self.clock()

    def wanted(self) -> bool:
        return bool(self.settings.get("enabled")) or self.testing()

    def start_download(self, lang: str) -> Dict[str, Any]:
        if lang not in MODELS:
            raise ValueError("unknown language")
        with self.lock:
            job = self.jobs.get(lang)
            if job and job.state in ("downloading", "unpacking"):
                return job.public()
            job = self.jobs[lang] = _Job(lang)
        threading.Thread(target=_download, args=(job, self.apply), name=f"memuai-wake-dl-{lang}", daemon=True).start()
        return job.public()

    def cancel_download(self, lang: str) -> bool:
        job = self.jobs.get(lang)
        if not job or job.state not in ("downloading", "unpacking"):
            return False
        job.cancel.set()
        return True

    def delete_model(self, lang: str) -> None:
        job = self.jobs.get(lang)
        if job and job.state in ("downloading", "unpacking"):
            raise RuntimeError("busy")
        with self.lock:
            self._model_cache.pop(lang, None)
            self.generation += 1
        self.wake.set()
        delete_model(lang)
        self.jobs.pop(lang, None)
        self.apply()

    def models(self) -> List[Dict[str, Any]]:
        rows = []
        for lang, meta in MODELS.items():
            job = self.jobs.get(lang)
            rows.append({"lang": lang, "id": meta["id"], "size": meta["size"], "title": meta["title"],
                         "phrase": meta["phrase"], "ready": model_ready(lang),
                         "download": job.public() if job else None})
        return rows

    def _model(self, lang: str):
        vosk = self.vosk_loader()
        with self.lock:
            cached = self._model_cache.get(lang)
        if cached is None:
            cached = vosk.Model(str(model_dir(lang)))
            with self.lock:
                self._model_cache = {lang: cached}
        return vosk, cached

    def check_phrase(self, phrase: str, lang: str) -> Dict[str, Any]:
        """Analysis of any phrase for the settings' live check; ``checked: False`` until the model is downloaded."""
        lang = lang if lang in MODELS else self.settings["lang"]
        phrase = normalize_phrase(phrase) or MODELS[lang]["phrase"]
        if not engine_info()["available"] or not model_ready(lang):
            words = phrase.split()
            out = {"phrase": phrase, "checked": False, "ok": 2 <= len(words) <= 4, "words": [], "variants": [],
                   "problem": None}
            if not out["ok"]:
                out["problem"] = analyze_phrase(phrase, lang, lambda _w: True)["problem"]
            return out
        _vosk, model = self._model(lang)
        known = lambda word: model.vosk_model_find_word(word) >= 0  # noqa: E731
        out = analyze_phrase(phrase, lang, known)
        out["checked"] = True
        return out

    def apply(self) -> None:
        """Start the thread when it is wanted, possible and not running; a running thread re-reads settings."""
        with self.lock:
            running = self.thread is not None and self.thread.is_alive()
            if self.wanted() and not running and not self.stop_event.is_set():
                self.thread = threading.Thread(target=self._run, name="memuai-wake", daemon=True)
                self.thread.start()
            elif not self.wanted() and not running:
                self._set_state("off")
        self.wake.set()

    def _set_state(self, state: str, reason: str = "", error: Optional[Dict[str, str]] = None) -> None:
        self.state, self.reason, self.error = state, reason, error
        if state not in ("listening", "paused"):
            self.level = 0.0

    def _run(self) -> None:
        source = None
        crashed = False
        try:
            while not self.stop_event.is_set() and self.wanted():
                self.wake.clear()
                settings, generation = dict(self.settings), self.generation
                lang = settings["lang"]
                if (not engine_info()["available"] or not model_ready(lang)) and source is not None:
                    self._close_source(source)
                    source = None
                if not engine_info()["available"]:
                    self._set_state("error", "no_engine")
                    self.wake.wait(5.0)
                    continue
                if not model_ready(lang):
                    job = self.jobs.get(lang)
                    self._set_state("loading" if job and job.state in ("downloading", "unpacking") else "error",
                                    "downloading" if job and job.state in ("downloading", "unpacking") else "no_model")
                    self.wake.wait(2.0)
                    continue
                try:
                    self._set_state("loading", "model")
                    vosk, model = self._model(lang)
                    known = lambda word: model.vosk_model_find_word(word) >= 0  # noqa: E731
                    analysis = analyze_phrase(effective_phrase(settings), lang, known)
                    self.analysis = analysis
                except Exception as exc:
                    log.warning("wake word model failed to load: %s", exc)
                    self._set_state("error", "model_broken",
                                    {"ru": "Модель распознавания повреждена. Удалите и скачайте её заново.",
                                     "en": "The speech model is broken. Delete it and download it again."})
                    self.wake.wait(10.0)
                    continue
                if not analysis["ok"]:
                    if source is not None:
                        self._close_source(source)
                        source = None
                    self._set_state("error", "phrase", analysis["problem"])
                    self.wake.wait(30.0)
                    continue
                grammar = grammar_for(analysis["variants"], lang, known, analysis["phrase"])
                detector = Detector(model, vosk, grammar, analysis["variants"], threshold(settings["sensitivity"]),
                                    alias_words(analysis))
                source = self._listen(source, settings, generation, detector, vosk, model)
        except Exception as exc:
            log.exception("wake word thread crashed")
            crashed = True
            self._set_state("error", "crash", {"ru": f"Слово-активатор остановлено: {exc}",
                                               "en": f"The wake word stopped: {exc}"})
        finally:
            if source is not None:
                self._close_source(source)
            with self.lock:
                if self.session is not None:
                    self.session.close("cancelled")
                if self.thread is threading.current_thread():
                    self.thread = None
            if self.state != "error" or not self.wanted():
                self._set_state("off")
            if self.wanted() and not crashed and not self.stop_event.is_set():
                self.apply()

    def _open_source(self, settings: Dict[str, Any], generation: int):
        """Open the mic, retrying every 3 s with the error in the status; None when stopped/reconfigured."""
        while not self.stop_event.is_set() and self.wanted() and self.generation == generation:
            if self.locked():
                self._set_state("paused", "locked")
                self.wake.wait(2.0)
                continue
            try:
                source = self.source_factory(settings["device"])
                self._tray(True)
                return source
            except MicError as exc:
                self._set_state("error", "mic", {"ru": exc.ru, "en": exc.en, "code": exc.code})
            except Exception as exc:
                self._set_state("error", "mic", {"ru": f"Микрофон недоступен: {exc}", "en": f"Microphone unavailable: {exc}"})
            self.wake.wait(3.0)
        return None

    def _close_source(self, source) -> None:
        try:
            source.close()
        except Exception:
            pass
        self._tray(False)

    def _listen(self, source, settings, generation, detector: Detector, vosk, model):
        """Read the mic until the settings change / stop. Returns the still-open source (same device) or None."""
        if source is not None and getattr(source, "device", None) != settings["device"]:
            self._close_source(source)
            source = None
        gate = EnergyGate()
        silent_reads = 0
        last_lock_check = 0.0
        while not self.stop_event.is_set() and self.wanted() and self.generation == generation:
            now = self.clock()
            if now - last_lock_check > 2.0:
                last_lock_check = now
                if self.locked():
                    if source is not None:
                        self._close_source(source)
                        source = None
                    self._set_state("paused", "locked")
                    detector.finish()
                    gate.reset()
                    self.wake.wait(2.0)
                    continue
            if source is None:
                source = self._open_source(settings, generation)
                if source is None:
                    continue
                silent_reads = 0
            chunk = source.read(0.5)
            if chunk is None:
                silent_reads += 1
                if silent_reads >= 6:
                    self._close_source(source)
                    source = None
                continue
            silent_reads = 0
            level = wake_audio.rms(chunk)
            self.level = level
            with self.lock:
                session = self.session
                if session is not None and session.phase != "listening" and time.time() - session.created > SESSION_TTL:
                    self.session = session = None
            if session is not None and session.phase == "listening":
                session.feed(chunk, level, gate.speech_level())
                self._set_state("listening", "command")
                continue
            reason = self.busy_reason() or ("cooldown" if now < self.cooldown_until else "")
            if reason:
                detector.finish()
                gate.reset()
                self._set_state("paused", reason)
                continue
            self._set_state("listening", "test" if self.testing() and not settings["enabled"] else "")
            feed, closed = gate.push(chunk, level)
            hit = None
            for index, piece in enumerate(feed):
                hit = detector.accept(piece)
                if hit:
                    rest = b"".join(feed[index + 1:])
                    break
            if not hit and closed:
                hit, rest = detector.finish(), b""
            if hit:
                tail = detector.tail(hit) + rest
                detector.finish()
                gate.reset()
                self._on_hit(hit, tail, settings, vosk, model, gate)
        return source

    def _on_hit(self, hit, tail: bytes, settings, vosk, model, gate: EnergyGate) -> None:
        phrase = " ".join(hit["words"])
        now = self.clock()
        self.cooldown_until = now + settings["cooldown"]
        self.last_hit = {"at": time.time(), "conf": hit["conf"], "phrase": phrase, "test": self.testing()}
        log.info("wake word heard (%s, conf %.2f)", phrase, hit["conf"])
        if self.testing():
            return
        if not self._quick_enabled():
            return
        try:
            recognizer = vosk.KaldiRecognizer(model, RATE)
        except Exception:
            recognizer = None
        chime = settings.get("chime") and self._chime()
        session = Session(recognizer, mute_until=0.3 if chime else 0.0)
        for i in range(0, len(tail), wake_audio.CHUNK_BYTES):
            piece = tail[i:i + wake_audio.CHUNK_BYTES]
            session.feed(piece, wake_audio.rms(piece), gate.speech_level())
        with self.lock:
            if self.session is not None:
                self.session.close("cancelled")
            self.session = session
        threading.Thread(target=self._open_window, args=(session,), name="memuai-wake-open", daemon=True).start()

    def _quick_enabled(self) -> bool:
        try:
            from .pgdata import load_preferences
            return bool(load_preferences().get("quick_ask_enabled", True))
        except Exception:
            return True

    def _open_window(self, session: Session) -> None:
        info = {"id": session.id, "glow": bool(self.settings.get("glow", True))}
        try:
            if self.opener is not None:
                self.opener(info)
                return
            manager = getattr(getattr(self.app, "state", None), "quick_ask", None) if self.app is not None else None
            if manager is None:
                return
            if not getattr(manager, "visible", False):
                manager.show("wake")
            else:
                manager.raise_to_top()
                if getattr(manager, "api", None) and getattr(manager, "hwnd", 0):
                    manager.api.take_foreground(manager.hwnd)
            manager._push("window.MemuQuick && MemuQuick.emit('wake', %s)" % json.dumps(info))
        except Exception as exc:
            log.warning("wake word: could not open Quick Ask: %s", exc)

    def _chime(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winsound
            path = models_root() / "chime.wav"
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(wav_bytes(chime_pcm(), 22050))
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return True
        except Exception:
            return False

    def session_public(self, with_audio: bool = False) -> Optional[Dict[str, Any]]:
        with self.lock:
            session = self.session
        if session is None:
            return None
        out = session.public(with_audio)
        out["level"] = round(self.level, 4) if session.phase == "listening" else 0.0
        return out

    def end_session(self, session_id: Any, action: str = "done") -> bool:
        """``finish`` — stop recording now and keep the audio; ``cancel`` / ``done`` — forget the session."""
        with self.lock:
            session = self.session
            if session is None or (session_id not in (None, "") and str(session.id) != str(session_id)):
                return False
            if action == "finish":
                session.close("captured" if session.audio else "empty")
                return True
            session.close("cancelled")
            self.session = None
            self.cooldown_until = self.clock() + self.settings.get("cooldown", 3)
        return True

    def status(self, lang: str = "") -> Dict[str, Any]:
        reason = self.reason
        out = {"state": self.state, "reason": reason, "error": self.error, "level": round(self.level, 4),
               "last_hit": self.last_hit, "testing": self.testing(), "enabled": bool(self.settings.get("enabled")),
               "phrase": effective_phrase(self.settings), "lang": self.settings["lang"],
               "quick_enabled": self._quick_enabled()}
        if self.state == "loading" and reason == "downloading":
            job = self.jobs.get(self.settings["lang"])
            out["progress"] = job.public()["progress"] if job else 0
        return out

    def _tray(self, on: bool) -> None:
        if on == self._tray_on:
            return
        self._tray_on = on
        try:
            from . import desktop
            shell = getattr(desktop, "_active_shell", None)
            tray = getattr(shell, "tray", None)
            if tray is None:
                return
            if on:
                from PIL import Image, ImageDraw
                image = shell._tray_image().convert("RGBA").resize((64, 64))
                draw = ImageDraw.Draw(image)
                draw.ellipse((36, 36, 63, 63), fill=(255, 255, 255, 255))
                draw.ellipse((40, 40, 59, 59), fill=(229, 72, 77, 255))
                tray.icon = image
                lang = ui_language()
                tray.title = _tr(lang, f"{shell.title} — слушает «{effective_phrase(self.settings)}»",
                                 f"{shell.title} — listening for “{effective_phrase(self.settings)}”")
            else:
                tray.icon = shell._tray_image()
                tray.title = shell.title
        except Exception as exc:
            log.debug("wake word tray indicator: %s", exc)

SERVICE = WakeService()

def note_speech(audio: bytes, mime: str) -> None:
    """``/api/voice/speak`` produced speech: the wake word ignores the mic while it plays (+1.5 s of echo)."""
    try:
        SERVICE.busy("tts", audio_seconds(audio, mime) + 1.5)
    except Exception:
        pass

def live_session(on: bool) -> None:
    """MemuAI Live opened/closed its WebSocket: no wake word in between."""
    try:
        SERVICE.hold("live", on)
    except Exception:
        pass
