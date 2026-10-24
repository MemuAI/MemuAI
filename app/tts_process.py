"""Bounded local Piper process. Never imports Piper into the WebView process.

Source runs use a module entry point; frozen builds re-enter MemuAI with an
internal flag handled before single-instance/UI initialization. Text and audio
travel through a private temporary directory, not command-line arguments,
stdout, logs, a shell, or a network service.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

MAX_TEXT_CHARS = 20_000
MAX_WAV_BYTES = 50 * 1024 * 1024
WORKER_TIMEOUT = 120
_SYNTHESIS_SLOT = threading.BoundedSemaphore(1)


# code → (Russian, English). The UI shows the message in its language (LocalTTSError.text).
MESSAGES = {
    "tts_invalid_audio": ("TTS-модель вернула пустое или слишком большое аудио.",
                          "The TTS model returned empty or oversized audio."),
    "tts_broken_audio": ("Локальная TTS-модель вернула повреждённое аудио.",
                         "The local TTS model returned damaged audio."),
    "tts_busy": ("Локальная озвучка уже готовится. Дождитесь её завершения.",
                 "Local speech is already being prepared. Wait for it to finish."),
    "tts_timeout": ("Локальная озвучка заняла слишком много времени. Попробуйте более короткий текст или другой голос.",
                    "Local speech took too long. Try a shorter text or another voice."),
    "tts_worker_crashed": ("Движок локальной озвучки аварийно завершился. MemuAI продолжает работать. "
                           "Попробуйте другой голос или переустановите модель.",
                           "The local speech engine crashed. MemuAI keeps working. "
                           "Try another voice or reinstall the model."),
    "tts_no_result": ("Не получен результат локальной озвучки.", "Local speech produced no result."),
    "tts_bad_result": ("Не удалось прочитать результат локальной озвучки.", "Could not read the local speech result."),
    "tts_missing_runtime": ("Движок Piper не вошёл в сборку. Обновите или пересоберите MemuAI.",
                            "The Piper engine is missing from this build. Update or rebuild MemuAI."),
    "tts_invalid_model": ("Файлы голоса повреждены или несовместимы. Переустановите выбранную TTS-модель.",
                          "The voice files are damaged or incompatible. Reinstall the selected TTS model."),
    "tts_output_limit": ("Получилось слишком длинное аудио. Озвучьте более короткое сообщение.",
                         "The audio came out too long. Read a shorter message aloud."),
    "tts_worker_failed": ("Не удалось озвучить текст локальной моделью. Попробуйте другой голос.",
                          "Could not read the text aloud with the local model. Try another voice."),
}
# Several messages share one public code: the UI and the HTTP status only distinguish these.
_PUBLIC_CODE = {"tts_broken_audio": "tts_invalid_audio", "tts_no_result": "tts_worker_failed",
                "tts_bad_result": "tts_worker_failed"}


class LocalTTSError(RuntimeError):
    def __init__(self, code: str) -> None:
        ru, en = MESSAGES.get(code, MESSAGES["tts_worker_failed"])
        super().__init__(ru)
        self.code = _PUBLIC_CODE.get(code, code if code in MESSAGES else "tts_worker_failed")
        self.en = en

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else str(self)


def worker_command(job_dir: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--memuai-tts-worker", str(job_dir)]
    return [sys.executable, "-m", "app.tts_worker", str(job_dir)]


def validate_wave(path: Path) -> None:
    if not path.is_file() or not 44 < path.stat().st_size <= MAX_WAV_BYTES:
        raise LocalTTSError("tts_invalid_audio")
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                    or not 8000 <= wav.getframerate() <= 96000 or wav.getnframes() <= 0):
                raise ValueError("Invalid PCM format")
            expected = wav.getnframes() * 2
            # Bound memory and verify that a truncated worker file is not played.
            received = 0
            while data := wav.readframes(32768):
                received += len(data)
            if received != expected:
                raise ValueError("Truncated audio")
    except (EOFError, wave.Error, ValueError) as exc:
        raise LocalTTSError("tts_broken_audio") from exc


def synthesize_isolated(text: str, model_file: Path, config_file: Path,
                        *, timeout: float = WORKER_TIMEOUT) -> tuple[bytes, str]:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ValueError("Для озвучивания нужен текст длиной до 20 000 символов.")
    if not model_file.is_file() or not config_file.is_file():
        raise FileNotFoundError("Сначала скачайте выбранную TTS-модель целиком.")
    if not _SYNTHESIS_SLOT.acquire(blocking=False):
        raise LocalTTSError("tts_busy")
    try:
        with tempfile.TemporaryDirectory(prefix="memuai-tts-") as temp:
            job = Path(temp)
            request = {"text": text, "model": str(model_file.resolve()), "config": str(config_file.resolve())}
            (job / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            # In a one-file PyInstaller build a separately launched instance
            # must own its unpacked files rather than borrowing a dying parent.
            env = os.environ.copy()
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            env["OMP_NUM_THREADS"] = "2"
            env["OPENBLAS_NUM_THREADS"] = "2"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            proc = None
            try:
                proc = subprocess.Popen(
                    worker_command(job), cwd=str(Path(__file__).resolve().parents[1]),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, shell=False, creationflags=flags, env=env,
                )
                try:
                    exit_code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    raise LocalTTSError("tts_timeout") from exc
                if exit_code != 0:
                    # Do not expose native stderr, paths or the spoken text.
                    raise LocalTTSError("tts_worker_crashed")
                status_path = job / "result.json"
                if not status_path.is_file() or status_path.stat().st_size > 8192:
                    raise LocalTTSError("tts_no_result")
                try:
                    status: Any = json.loads(status_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise LocalTTSError("tts_bad_result") from exc
                if not isinstance(status, dict) or status.get("ok") is not True:
                    code = status.get("code") if isinstance(status, dict) else "tts_worker_failed"
                    known = code if code in ("tts_missing_runtime", "tts_invalid_model", "tts_output_limit") \
                        else "tts_worker_failed"
                    raise LocalTTSError(known)
                audio_path = job / "speech.wav"
                validate_wave(audio_path)
                return audio_path.read_bytes(), "audio/wav"
            finally:
                # Always reap before TemporaryDirectory removes an open WAV.
                if proc is not None:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
    finally:
        _SYNTHESIS_SLOT.release()
