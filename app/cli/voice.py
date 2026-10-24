"""Voice input for the terminal: record the microphone, transcribe, insert.

Recording uses the Windows Media Control Interface (winmm), which every
Windows has — no audio packages needed. Transcription follows MemuAI's own
speech settings, in this order:

1. the running MemuAI app (``/api/voice/transcribe``) — the user's configured
   engine, local Whisper or API, including the optional clean-up pass;
2. the OpenAI-compatible speech API from MemuAI preferences;
3. local faster-whisper, when it is installed and the model is downloaded.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from ..config import DATA_DIR

ALIAS = "memucoderec"


def _winmm():
    import ctypes
    return ctypes.windll.winmm


def mci(command: str) -> str:
    import ctypes
    buffer = ctypes.create_unicode_buffer(512)
    code = _winmm().mciSendStringW(command, buffer, 511, 0)
    if code:
        error = ctypes.create_unicode_buffer(512)
        _winmm().mciGetErrorStringW(code, error, 511)
        raise RuntimeError(error.value or f"MCI error {code}")
    return buffer.value


def mci_available() -> bool:
    if os.name != "nt":
        return False
    try:
        return int(_winmm().waveInGetNumDevs()) > 0
    except Exception:
        return False


class Recorder:
    def __init__(self) -> None:
        self.started = 0.0
        self.active = False

    def start(self) -> None:
        if not mci_available():
            raise RuntimeError("no microphone is available")
        try:
            mci(f"close {ALIAS}")
        except RuntimeError:
            pass
        mci(f"open new type waveaudio alias {ALIAS}")
        try:
            mci(f"set {ALIAS} bitspersample 16 channels 1 samplespersec 16000 bytespersec 32000 alignment 2")
        except RuntimeError:
            pass
        mci(f"record {ALIAS}")
        self.started = time.monotonic()
        self.active = True

    def stop(self) -> bytes:
        if not self.active:
            return b""
        path = Path(tempfile.gettempdir()) / f"memucode-voice-{os.getpid()}.wav"
        try:
            mci(f"stop {ALIAS}")
            mci(f'save {ALIAS} "{path}"')
        finally:
            try:
                mci(f"close {ALIAS}")
            except RuntimeError:
                pass
            self.active = False
        try:
            return path.read_bytes()
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def cancel(self) -> None:
        if self.active:
            try:
                mci(f"close {ALIAS}")
            except RuntimeError:
                pass
            self.active = False

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started if self.active else 0.0


def _memuai_url() -> str:
    try:
        data = json.loads((Path(DATA_DIR) / "memuai" / "memuai.json").read_text(encoding="utf-8"))
        url = str(data.get("url") or "").rstrip("/")
        if url.startswith("http://127.0.0.1") or url.startswith("http://localhost"):
            return url
    except (OSError, ValueError):
        pass
    return ""


def _post_json(url: str, payload: Any, timeout: float) -> Any:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    # The URL is always the local MemuAI window (127.0.0.1): never through the env/system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


async def transcribe(audio: bytes, *, model: str = "") -> str:
    if len(audio) < 2000:
        raise RuntimeError("the recording is empty")
    data_url = "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")
    url = _memuai_url()
    errors = []
    if url:
        try:
            result = await asyncio.to_thread(_post_json, url + "/api/voice/transcribe",
                                             {"audio": data_url, "model": model}, 180)
            if isinstance(result, dict) and result.get("text"):
                return str(result["text"]).strip()
            if isinstance(result, dict) and result.get("error"):
                errors.append(str(result["error"]))
        except Exception as exc:
            errors.append(f"MemuAI: {exc}")
    try:
        prefs = json.loads((Path(DATA_DIR) / "memuai" / "preferences.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prefs = {}
    from ..voice_runtime import VoiceRuntime
    runtime = VoiceRuntime(DATA_DIR)
    language = str(prefs.get("voice_language") or "auto")
    if prefs.get("voice_stt_provider") == "api" and prefs.get("voice_stt_api_url"):
        try:
            return (await runtime.transcribe_api(audio, "audio/wav", base_url=prefs.get("voice_stt_api_url", ""),
                                                 api_key=prefs.get("voice_stt_api_key", ""),
                                                 model=prefs.get("voice_stt_api_model", "gpt-4o-mini-transcribe"),
                                                 language=language)).strip()
        except Exception as exc:
            errors.append(f"API: {exc}")
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is None:
        # The installed CLI ships without the speech engine: MemuAI has it.
        errors.append("local: start MemuAI — local speech recognition runs in the app")
    else:
        try:
            return (await asyncio.to_thread(runtime.transcribe_local, audio, "audio/wav",
                                            prefs.get("voice_stt_local_model", "small"), language)).strip()
        except Exception as exc:
            errors.append(f"local: {exc}")
    raise RuntimeError("; ".join(errors) or "no speech recognition is configured")
