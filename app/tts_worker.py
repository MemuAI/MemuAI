"""Private one-shot Piper worker; stdlib-only until the native import.

Do not import this module to synthesize in the desktop process. Use
app.tts_process.synthesize_isolated. No desktop, HTTP or account initialization
is performed here, including in a frozen --windowed executable.
"""
from __future__ import annotations

import json
import os
import re
import sys
import wave
from pathlib import Path
from typing import Iterator

from .tts_process import MAX_TEXT_CHARS, MAX_WAV_BYTES

class AudioLimitError(ValueError):
    pass

def split_text(text: str, limit: int = 600) -> Iterator[str]:
    for sentence in re.split(r"(?<=[.!?。！？])\s+|\n+", text):
        sentence = sentence.strip()
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit + 1)
            if cut < limit // 3:
                cut = limit
            yield sentence[:cut].strip()
            sentence = sentence[cut:].strip()
        if sentence:
            yield sentence

def synthesize_to_wave(voice, text: str, output: Path) -> None:
    total = 0
    audio_format = None

    with wave.open(str(output), "wb") as wav:
        for piece in split_text(text):
            for chunk in voice.synthesize(piece):
                current = (int(chunk.sample_rate), int(chunk.sample_width), int(chunk.sample_channels))
                rate, width, channels = current
                if not 8000 <= rate <= 96000 or width != 2 or channels != 1:
                    raise ValueError("Unsupported PCM format")
                if audio_format is None:
                    audio_format = current
                    wav.setframerate(rate); wav.setsampwidth(width); wav.setnchannels(channels)
                elif current != audio_format:
                    raise ValueError("Inconsistent PCM format")
                audio = bytes(chunk.audio_int16_bytes)
                if len(audio) % (width * channels):
                    raise ValueError("Incomplete PCM sample")
                total += len(audio)
                if total + 44 > MAX_WAV_BYTES:
                    raise AudioLimitError("Audio limit exceeded")
                wav.writeframesraw(audio)
        if not total:

            if audio_format is None:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050)
            raise ValueError("No audio")

def run_job(job_dir: Path) -> None:
    request_path = job_dir / "request.json"
    result = {"ok": False, "code": "tts_worker_failed"}
    try:
        if request_path.stat().st_size > 256_000:
            raise ValueError("Request limit exceeded")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        text = request["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError("Invalid text")
        model = Path(request["model"])
        config = Path(request["config"])
        if (model.suffix.lower() != ".onnx" or not model.is_file()
                or config != model.with_suffix(".onnx.json") or not config.is_file()
                or config.stat().st_size > 1_048_576):
            raise ValueError("Invalid model pair")
        metadata = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("Invalid configuration")

        from piper import PiperVoice
        voice = PiperVoice.load(str(model), config_path=str(config), use_cuda=False)
        synthesize_to_wave(voice, text, job_dir / "speech.wav")
        result = {"ok": True}
    except ImportError:
        result["code"] = "tts_missing_runtime"
    except AudioLimitError:
        result["code"] = "tts_output_limit"
    except (ValueError, KeyError, TypeError, OSError):
        result["code"] = "tts_invalid_model"
    except Exception:

        result["code"] = "tts_worker_failed"
    (job_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    else:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ImportError, ValueError, OSError):
            pass
    try:
        run_job(Path(args[0]))
        return 0
    except Exception:
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
