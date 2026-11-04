# -*- coding: utf-8 -*-
"""MemuMeet native system-audio capture: pure core (no I/O, no FastAPI).

The web capture (``getDisplayMedia``) can only pick ONE screen, and only
through a picker. The native path captures the sound of several output
devices at once (WASAPI loopback on Windows) and mixes them into one
stream for MemuMeet. This module is the part that can be tested anywhere:
sample-format conversion, resampling, per-source jitter buffers (devices
run on their own clocks, so they drift apart) and the mixer itself.

Wire format (see ``meet_native_audio_helper.py``)
-------------------------------------------------
One mixed stream: 48 kHz, stereo, signed 16-bit little-endian PCM,
20 ms frames (960 samples per channel, 3840 bytes per frame). Control
messages are JSON; audio frames are raw bytes. Device ids are opaque
strings minted by the capture backend; the mixer never interprets them.

Drift policy
------------
Every source keeps a jitter buffer with a 100 ms target depth. Each
20 ms tick pulls exactly 960 frames from every source: a source that is
behind contributes silence for the missing part (counted as an
underrun), a source that is ahead has its oldest data dropped down to
the target depth (counted as a drop). A recording therefore never
stalls on one slow device and never grows an unbounded backlog on a
fast one; the cost is at most a short silence or a dropped sliver when
a device clock is far off.
"""
from __future__ import annotations

import array
import math
import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 960 per channel
FRAME_BYTES = FRAME_SAMPLES * CHANNELS * 2  # int16 stereo
PROTOCOL_VERSION = 1

# Jitter-buffer policy: keep about this much audio queued per source.
TARGET_DEPTH_MS = 100
MAX_DEPTH_MS = 300

# A source that produced nothing for this long is considered dead by the
# session (the helper reports it as lost; the mixer itself just pads
# silence and counts underruns).
SOURCE_SILENCE_TIMEOUT_S = 5.0

_ERROR_TEXTS = {
    "unsupported_platform": (
        "Нативный захват системного звука доступен только в Windows",
        "Native system-audio capture is only available on Windows",
    ),
    "no_backend": (
        "Для нативного захвата нужен пакет sounddevice (pip install sounddevice)",
        "Native capture needs the sounddevice package (pip install sounddevice)",
    ),
    "portaudio_missing": (
        "Звуковая библиотека PortAudio не загрузилась — нативный захват недоступен",
        "The PortAudio sound library failed to load — native capture is unavailable",
    ),
    "no_devices": (
        "Устройства вывода звука не найдены",
        "No audio output devices were found",
    ),
    "device_not_found": (
        "Выбранное устройство звука не найдено — возможно, его отключили",
        "The selected audio device was not found — it may have been unplugged",
    ),
    "device_lost": (
        "Устройство звука отключилось во время записи",
        "An audio device was disconnected during recording",
    ),
    "helper_unavailable": (
        "Служба нативного захвата не отвечает",
        "The native capture service is not responding",
    ),
    "session_busy": (
        "Нативный захват уже идёт",
        "Native capture is already running",
    ),
    "invalid_selection": (
        "Выберите хотя бы одно устройство звука",
        "Choose at least one audio device",
    ),
    "timeout": (
        "Служба нативного захвата не ответила вовремя",
        "The native capture service timed out",
    ),
    "capture_failed": (
        "Не удалось запустить нативный захват звука",
        "Could not start native audio capture",
    ),
}


class NativeAudioError(Exception):
    """A native-capture failure with a stable code and a bilingual message."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        ru, en = _ERROR_TEXTS.get(code, _ERROR_TEXTS["capture_failed"])
        self.detail = str(detail or "")
        message = ru + ((" — " + self.detail) if self.detail else "")
        super().__init__(message)
        self.message_ru = message
        self.message_en = en + ((" — " + self.detail) if self.detail else "")

    def as_dict(self, lang: str = "ru") -> Dict[str, str]:
        return {"code": self.code,
                "error": self.message_ru if lang != "en" else self.message_en}


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def clean_device_id(value: object) -> str:
    """A device id that is safe to use as a key (no path tricks, no blanks)."""
    text = str(value or "").strip()
    if not text or len(text) > 128 or not _ID_RE.match(text):
        raise NativeAudioError("device_not_found")
    return text


def clean_selection(devices: object, gains: object,
                    available_ids: Sequence[str]) -> Tuple[List[str], Dict[str, float]]:
    """Validate a UI device selection against the enumerated devices.

    Returns the chosen ids (in enumeration order, de-duplicated) and a
    gain per id clamped to 0..1. Unknown ids are dropped; an empty
    selection after that is an error, not a silent no-op.
    """
    available = list(dict.fromkeys(str(item) for item in available_ids))
    wanted = []
    for item in (devices if isinstance(devices, list) else []):
        text = str(item or "").strip()
        if text and text in available and text not in wanted:
            wanted.append(text)
    if not wanted:
        raise NativeAudioError("invalid_selection")
    gains_in = gains if isinstance(gains, dict) else {}
    out_gains: Dict[str, float] = {}
    for device_id in wanted:
        try:
            gain = float(gains_in.get(device_id, 1.0))
        except (TypeError, ValueError):
            gain = 1.0
        if gain != gain:  # NaN
            gain = 1.0
        out_gains[device_id] = max(0.0, min(1.0, gain))
    return wanted, out_gains


def clamp_gain(value: object, default: float = 1.0) -> float:
    try:
        gain = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if gain != gain:
        return default
    return max(0.0, min(1.0, gain))


def upmix_to_stereo(raw: bytes, channels: int) -> bytes:
    """Convert one raw int16 block with ``channels`` channels to stereo.

    WASAPI loopback is usually stereo, but a mono endpoint (or a mono
    microphone) hands us one channel per frame, and a multichannel
    endpoint hands us more. The mixer only understands interleaved
    stereo, so: mono duplicates the channel, stereo passes through,
    more channels keep the first two. An empty block stays empty, and
    a trailing odd byte is dropped.
    """
    if channels <= 0:
        return b""
    usable = len(raw) - (len(raw) % 2)
    raw = raw[:usable]
    if channels == CHANNELS:
        return raw
    samples = array.array("h")
    samples.frombytes(raw)
    if channels == 1:
        out = array.array("h", (0 for _ in range(len(samples) * CHANNELS)))
        for index, value in enumerate(samples):
            out[index * CHANNELS] = value
            out[index * CHANNELS + 1] = value
        return out.tobytes()
    frames = len(samples) // channels
    out = array.array("h", (0 for _ in range(frames * CHANNELS)))
    for frame in range(frames):
        out[frame * CHANNELS] = samples[frame * channels]
        out[frame * CHANNELS + 1] = samples[frame * channels + 1]
    return out.tobytes()


def pcm16_to_float(raw: bytes) -> List[float]:
    """Interleaved int16 PCM bytes -> floats in [-1, 1]."""
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) - (len(raw) % 2)])
    if samples.itemsize != 2:  # pragma: no cover - platform quirk
        samples = array.array("h", samples)
    return [value / 32768.0 for value in samples]


def float_to_pcm16(samples: Sequence[float]) -> bytes:
    """Floats in [-1, 1] -> interleaved int16 PCM bytes (clamped)."""
    out = array.array("h", (0 for _ in range(len(samples))))
    for index, value in enumerate(samples):
        if value > 1.0:
            value = 1.0
        elif value < -1.0:
            value = -1.0
        out[index] = int(round(value * 32767.0))
    return out.tobytes()


def mix_samples(blocks: Sequence[Sequence[float]], gains: Sequence[float]) -> List[float]:
    """Sum per-source float blocks with per-source gains (no limiting)."""
    if not blocks:
        return []
    length = min(len(block) for block in blocks)
    out = [0.0] * length
    for block, gain in zip(blocks, gains):
        if gain == 0.0:
            continue
        for index in range(length):
            out[index] += block[index] * gain
    return out


class LinearResampler:
    """Streaming linear-interpolation resampler for one channel pair.

    Samples are pushed as interleaved stereo floats at ``source_rate``
    and pulled as interleaved stereo floats at ``target_rate``. The
    common case (48 kHz in, 48 kHz out) is a pass-through: no copies
    beyond the queue itself. Quality is deliberately plain — speech,
    not music mastering — and the implementation is stdlib-only so the
    helper has no hard dependency.
    """

    def __init__(self, source_rate: int, target_rate: int = SAMPLE_RATE) -> None:
        self.source_rate = int(source_rate or 0)
        self.target_rate = int(target_rate or SAMPLE_RATE)
        if self.source_rate <= 0:
            raise ValueError("Некорректная частота дискретизации")
        self._queue: List[float] = []
        self._position = 0.0  # read position in source frames
        self._step = self.source_rate / self.target_rate
        self._passthrough = self.source_rate == self.target_rate

    def push(self, samples: Sequence[float]) -> None:
        self._queue.extend(float(value) for value in samples)

    @property
    def queued_frames(self) -> int:
        return len(self._queue) // CHANNELS

    def pull(self, frames: int) -> List[float]:
        """Up to ``frames`` output frames (fewer when the input is short)."""
        if frames <= 0:
            return []
        if self._passthrough:
            take = min(frames, self.queued_frames)
            out = self._queue[:take * CHANNELS]
            del self._queue[:take * CHANNELS]
            return out
        out: List[float] = []
        available = self.queued_frames
        while len(out) // CHANNELS < frames:
            position = self._position
            index = int(math.floor(position))
            if index + 1 >= available:
                break
            frac = position - index
            base = index * CHANNELS
            for channel in range(CHANNELS):
                first = self._queue[base + channel]
                second = self._queue[base + CHANNELS + channel]
                out.append(first + (second - first) * frac)
            self._position += self._step
        consumed = int(math.floor(self._position))
        if consumed:
            del self._queue[:consumed * CHANNELS]
            self._position -= consumed
        return out


class JitterBuffer:
    """One source's queue: absorbs clock drift, never grows unbounded.

    ``push`` appends resampled frames; ``pull`` takes exactly ``frames``
    frames, padding with silence when the source is behind and dropping
    the oldest frames when it is ahead of ``MAX_DEPTH_MS``. Counters let
    the session report underruns/drops honestly instead of hiding them.
    """

    def __init__(self, target_ms: int = TARGET_DEPTH_MS, max_ms: int = MAX_DEPTH_MS) -> None:
        self._data: List[float] = []
        self._target = max(1, int(target_ms * SAMPLE_RATE / 1000)) * CHANNELS
        self._max = max(self._target, int(max_ms * SAMPLE_RATE / 1000)) * CHANNELS
        self.underruns = 0
        self.drops = 0
        self.last_push = time.monotonic()

    def push(self, samples: Sequence[float]) -> None:
        self._data.extend(samples)
        self.last_push = time.monotonic()
        if len(self._data) > self._max:
            excess = len(self._data) - self._target
            excess -= excess % CHANNELS
            del self._data[:excess]
            self.drops += excess // CHANNELS

    def pull(self, frames: int) -> List[float]:
        wanted = frames * CHANNELS
        if len(self._data) >= wanted:
            out = self._data[:wanted]
            del self._data[:wanted]
            return out
        missing = wanted - len(self._data)
        out = self._data + [0.0] * missing
        self._data = []
        self.underruns += missing // CHANNELS
        return out

    @property
    def depth_frames(self) -> int:
        return len(self._data) // CHANNELS

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_push


class Mixer:
    """Mix N sources into 48 kHz stereo int16 frames with a soft limiter.

    Each source feeds a :class:`JitterBuffer` through its own
    :class:`LinearResampler`, so sources may arrive at different rates
    and on different clocks. The mix sums the gained sources; when the
    sum would clip, a one-pole limiter pulls the master gain down
    smoothly instead of hard-clipping every peak. :meth:`mix_frame`
    always returns exactly one 20 ms frame (silence when every source
    is behind), so the output stream never stalls.
    """

    def __init__(self, gains: Optional[Dict[str, float]] = None) -> None:
        self._sources: Dict[str, Dict[str, object]] = {}
        self._gains: Dict[str, float] = dict(gains or {})
        self._master_gain = 1.0
        self.frames_mixed = 0
        self.peak = 0.0

    def add_source(self, device_id: str, source_rate: int, gain: float = 1.0) -> None:
        device_id = clean_device_id(device_id)
        self._sources[device_id] = {
            "resampler": LinearResampler(source_rate),
            "buffer": JitterBuffer(),
            "peak": 0.0,
        }
        self._gains[device_id] = clamp_gain(gain)

    def remove_source(self, device_id: str) -> None:
        self._sources.pop(device_id, None)
        self._gains.pop(device_id, None)

    def set_gain(self, device_id: str, gain: float) -> None:
        if device_id in self._sources:
            self._gains[device_id] = clamp_gain(gain)

    def feed(self, device_id: str, pcm16: bytes, source_rate: int) -> None:
        """Push one raw int16 block from a capture backend."""
        source = self._sources.get(device_id)
        if source is None:
            return
        resampler = source["resampler"]  # type: ignore[assignment]
        if resampler.source_rate != int(source_rate or 0):
            source["resampler"] = resampler = LinearResampler(int(source_rate or 0))
        floats = pcm16_to_float(pcm16)
        resampler.push(floats)
        pulled = resampler.pull(resampler.queued_frames)
        if pulled:
            peak = 0.0
            for value in pulled:
                magnitude = abs(value)
                if magnitude > peak:
                    peak = magnitude
            source["peak"] = max(float(source["peak"]), peak)  # type: ignore[arg-type]
            buffer = source["buffer"]  # type: ignore[assignment]
            buffer.push(pulled)

    def source_peak(self, device_id: str) -> float:
        """The source's recent peak (decays on every mixed frame)."""
        source = self._sources.get(device_id)
        if source is None:
            return 0.0
        peak = float(source["peak"])
        source["peak"] = peak * 0.85
        return peak

    def gain(self, device_id: str) -> float:
        return self._gains.get(device_id, 1.0)

    def source_ids(self) -> List[str]:
        return list(self._sources)

    def idle_seconds(self, device_id: str) -> float:
        """Seconds since the source last produced a block."""
        source = self._sources.get(device_id)
        if source is None:
            return 0.0
        return source["buffer"].idle_seconds  # type: ignore[attr-defined]

    def mix_frame(self) -> Tuple[bytes, float]:
        """One 20 ms mixed frame plus its peak (0..1, post-limiter)."""
        blocks: List[List[float]] = []
        gains: List[float] = []
        for device_id, source in self._sources.items():
            buffer = source["buffer"]  # type: ignore[assignment]
            blocks.append(buffer.pull(FRAME_SAMPLES))
            gains.append(self._gains.get(device_id, 1.0))
        mixed = mix_samples(blocks, gains) if blocks else [0.0] * (FRAME_SAMPLES * CHANNELS)
        peak = 0.0
        for value in mixed:
            magnitude = abs(value)
            if magnitude > peak:
                peak = magnitude
        # Soft limiter: pull the master gain down when the mix clips,
        # let it recover slowly when the mix is quiet.
        if peak * self._master_gain > 0.98:
            target = 0.98 / peak if peak > 0 else 1.0
            self._master_gain = max(target, self._master_gain * 0.9)
        else:
            self._master_gain = min(1.0, self._master_gain * 1.001 + 0.0005)
        if self._master_gain < 1.0:
            mixed = [value * self._master_gain for value in mixed]
            peak = peak * self._master_gain
        self.peak = peak
        self.frames_mixed += 1
        return float_to_pcm16(mixed), peak

    def stats(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for device_id, source in self._sources.items():
            buffer = source["buffer"]  # type: ignore[assignment]
            out[device_id] = {
                "depth_frames": buffer.depth_frames,
                "underruns": buffer.underruns,
                "drops": buffer.drops,
            }
        return out


def device_sort_key(device: Dict[str, object]) -> Tuple[int, str]:
    """Default output first, then by name (stable list order for the UI)."""
    return (0 if device.get("is_default") else 1, str(device.get("name") or ""))
