"""Microphone capture for the wake word (Round 27), without heavy dependencies.

* Windows: WinMM ``waveIn*`` through ctypes — 16 kHz mono 16-bit PCM straight from the audio engine
  (it resamples for us), event callback (``CALLBACK_EVENT``), eight 100 ms buffers in rotation.
  Device names come from ``waveInGetDevCapsW`` (Windows truncates them to 31 characters); the device is
  stored by name because indexes move when a headset is plugged in. ``""`` = the default device.
* Elsewhere: the optional ``sounddevice`` package (imported lazily), or a clear "unavailable" error.
* :func:`session_locked` — the lock screen / secure desktop (the mic is released while locked).

Everything here is used from the wake word thread only (``app/wake_word.py``); nothing blocks the UI.
"""
from __future__ import annotations

import array
import math
import sys
from typing import Any, Dict, List, Optional

RATE = 16000
CHUNK_MS = 100
CHUNK_BYTES = RATE * 2 * CHUNK_MS // 1000      # 3200 bytes = 100 ms of 16-bit mono


class MicError(RuntimeError):
    """A microphone problem the settings page shows: ``code`` ∈ {unavailable, no_device, busy, denied, failed}."""

    def __init__(self, code: str, ru: str, en: str) -> None:
        super().__init__(ru)
        self.code, self.ru, self.en = code, ru, en

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru


def rms(pcm: bytes) -> float:
    """RMS of 16-bit little-endian PCM, 0..1 (pure Python: ~50 µs per 100 ms chunk)."""
    if len(pcm) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    total = sum(x * x for x in samples)
    return math.sqrt(total / len(samples)) / 32768.0


def resample(pcm: bytes, rate: int, target: int = RATE) -> bytes:
    """Linear resampling of 16-bit mono PCM (only for a device that refuses 16 kHz)."""
    if rate == target or not pcm:
        return pcm
    src = array.array("h")
    src.frombytes(pcm[: len(pcm) // 2 * 2])
    count = int(len(src) * target / rate)
    out = array.array("h", bytes(2 * count))
    step = rate / target
    last = len(src) - 1
    for i in range(count):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = src[j] if j <= last else src[last]
        b = src[j + 1] if j + 1 <= last else a
        out[i] = int(a + (b - a) * frac)
    return out.tobytes()


# ---------------------------------------------------------------- Windows: WinMM waveIn
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _winmm = ctypes.WinDLL("winmm")
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WAVE_MAPPER = 0xFFFFFFFF
    WAVE_FORMAT_PCM = 1
    CALLBACK_EVENT = 0x00050000
    WHDR_DONE = 0x00000001
    MMSYSERR_NOERROR = 0
    MMSYSERR_ALLOCATED = 4
    MMSYSERR_BADDEVICEID = 2
    MMSYSERR_NODRIVER = 6
    WAIT_OBJECT_0 = 0

    class WAVEFORMATEX(ctypes.Structure):
        _pack_ = 1                       # mmsystem.h is byte-packed: 18 bytes, not 20
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                    ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                    ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        pass

    WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                        ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD),
                        ("dwLoops", wintypes.DWORD), ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]

    class WAVEINCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD), ("wChannels", wintypes.WORD),
                    ("wReserved1", wintypes.WORD)]

    HWAVEIN = wintypes.HANDLE
    _winmm.waveInGetNumDevs.restype = wintypes.UINT
    _winmm.waveInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(WAVEINCAPSW), wintypes.UINT]
    _winmm.waveInOpen.argtypes = [ctypes.POINTER(HWAVEIN), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                                  ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    for _name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
        getattr(_winmm, _name).argtypes = [HWAVEIN, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    for _name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
        getattr(_winmm, _name).argtypes = [HWAVEIN]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _winmm_devices() -> List[str]:
        names = []
        for index in range(int(_winmm.waveInGetNumDevs())):
            caps = WAVEINCAPSW()
            if _winmm.waveInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)) == MMSYSERR_NOERROR:
                names.append(caps.szPname or f"#{index}")
        return names

    class WinMMSource:
        """waveIn at 16 kHz mono; ``read()`` returns 100 ms of PCM or ``None`` on timeout.

        Eight buffers = 0.8 s of slack: loading a model or a long ``FinalResult`` must not drop the user's words."""

        BUFFERS = 8

        def __init__(self, device: str = "") -> None:
            self.device = device
            self.handle = HWAVEIN()
            self.event = None
            self.headers: List[Any] = []
            self.buffers: List[Any] = []
            self.next = 0
            self.opened = False

        def open(self) -> None:
            devices = _winmm_devices()
            if not devices:
                raise MicError("no_device", "Микрофон не найден. Подключите микрофон и проверьте «Параметры → Звук».",
                               "No microphone found. Plug one in and check Settings → Sound.")
            device_id = WAVE_MAPPER
            if self.device:
                if self.device not in devices:
                    raise MicError("no_device", f"Микрофон «{self.device}» не подключён. Выберите другой или «По умолчанию».",
                                   f"The microphone “{self.device}” is not connected. Pick another one or “Default”.")
                device_id = devices.index(self.device)
            fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, 1, RATE, RATE * 2, 2, 16, 0)
            self.event = _kernel32.CreateEventW(None, False, False, None)
            code = _winmm.waveInOpen(ctypes.byref(self.handle), device_id, ctypes.byref(fmt), self.event or 0, 0,
                                     CALLBACK_EVENT)
            if code != MMSYSERR_NOERROR:
                _kernel32.CloseHandle(self.event)
                self.event = None
                if code == MMSYSERR_ALLOCATED:
                    raise MicError("busy", "Микрофон занят другой программой в монопольном режиме.",
                                   "Another program holds the microphone exclusively.")
                if code in (MMSYSERR_BADDEVICEID, MMSYSERR_NODRIVER):
                    raise MicError("no_device", "Микрофон отключён или его драйвер недоступен.",
                                   "The microphone is unplugged or its driver is unavailable.")
                raise MicError("denied" if code == 1 else "failed",
                               f"Windows не открыл микрофон (код {code}). Проверьте «Конфиденциальность → Микрофон» "
                               "— доступ для классических приложений.",
                               f"Windows could not open the microphone (code {code}). Check Privacy → Microphone "
                               "— access for desktop apps.")
            self.opened = True
            self.headers, self.buffers = [], []
            for _ in range(self.BUFFERS):
                buffer = ctypes.create_string_buffer(CHUNK_BYTES)
                header = WAVEHDR()
                header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
                header.dwBufferLength = CHUNK_BYTES
                _winmm.waveInPrepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(header))
                _winmm.waveInAddBuffer(self.handle, ctypes.byref(header), ctypes.sizeof(header))
                self.headers.append(header)
                self.buffers.append(buffer)
            self.next = 0
            _winmm.waveInStart(self.handle)

        def read(self, timeout: float = 0.5) -> Optional[bytes]:
            header = self.headers[self.next]
            if not header.dwFlags & WHDR_DONE:
                _kernel32.WaitForSingleObject(self.event, int(timeout * 1000))
                if not header.dwFlags & WHDR_DONE:
                    return None
            data = ctypes.string_at(self.buffers[self.next], header.dwBytesRecorded)
            header.dwBytesRecorded = 0
            header.dwFlags &= ~WHDR_DONE
            _winmm.waveInAddBuffer(self.handle, ctypes.byref(header), ctypes.sizeof(header))
            self.next = (self.next + 1) % len(self.headers)
            return data

        def close(self) -> None:
            if self.opened:
                self.opened = False
                _winmm.waveInReset(self.handle)       # returns every queued buffer (marks them done)
                for header in self.headers:
                    _winmm.waveInUnprepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(header))
                _winmm.waveInClose(self.handle)
            if self.event:
                _kernel32.CloseHandle(self.event)
                self.event = None
            self.headers, self.buffers = [], []

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.OpenInputDesktop.restype = wintypes.HANDLE
    _user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD)]
    _user32.CloseDesktop.argtypes = [wintypes.HANDLE]

    def session_locked() -> bool:
        """The lock screen / UAC secure desktop is the input desktop (it is not ours to open, or not "Default")."""
        desktop = _user32.OpenInputDesktop(0, False, 0x0001)        # DESKTOP_READOBJECTS
        if not desktop:
            return True
        try:
            name = ctypes.create_unicode_buffer(64)
            needed = wintypes.DWORD(0)
            if not _user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
                return False
            return name.value.lower() != "default"
        finally:
            _user32.CloseDesktop(desktop)
else:
    def session_locked() -> bool:
        return False


# ---------------------------------------------------------------- optional: sounddevice (other OSes)
class SoundDeviceSource:
    def __init__(self, device: str = "") -> None:
        self.device = device
        self.stream = None
        self.rate = RATE

    def open(self) -> None:
        try:
            import sounddevice as sd  # optional
        except Exception:
            raise MicError("unavailable", "Захват микрофона в этой системе требует пакета sounddevice "
                           "(pip install sounddevice).",
                           "Microphone capture on this system needs the sounddevice package "
                           "(pip install sounddevice).") from None
        device = self.device or None
        try:
            if device is not None and device not in [d["name"] for d in sd.query_devices() if d["max_input_channels"]]:
                raise MicError("no_device", f"Микрофон «{device}» не подключён.", f"The microphone “{device}” is not connected.")
            try:
                self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                                blocksize=RATE * CHUNK_MS // 1000, device=device)
                self.rate = RATE
            except Exception:
                info = sd.query_devices(device, "input")
                self.rate = int(info["default_samplerate"])
                self.stream = sd.RawInputStream(samplerate=self.rate, channels=1, dtype="int16",
                                                blocksize=self.rate * CHUNK_MS // 1000, device=device)
            self.stream.start()
        except MicError:
            raise
        except Exception as exc:
            raise MicError("failed", f"Не удалось открыть микрофон: {exc}", f"Could not open the microphone: {exc}") from None

    def read(self, timeout: float = 0.5) -> Optional[bytes]:
        data, _overflow = self.stream.read(self.rate * CHUNK_MS // 1000)
        return resample(bytes(data), self.rate)

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


def list_devices() -> Dict[str, Any]:
    """``{"backend": "winmm"|"sounddevice"|"", "devices": [names]}`` for the settings' microphone list."""
    if sys.platform == "win32":
        try:
            return {"backend": "winmm", "devices": _winmm_devices()}
        except Exception:
            return {"backend": "winmm", "devices": []}
    try:
        import sounddevice as sd  # optional
        names = [d["name"] for d in sd.query_devices() if d.get("max_input_channels")]
        return {"backend": "sounddevice", "devices": names}
    except Exception:
        return {"backend": "", "devices": []}


def open_source(device: str = ""):
    """An opened microphone source (raises :class:`MicError` with a ru/en message)."""
    source = WinMMSource(device) if sys.platform == "win32" else SoundDeviceSource(device)
    source.open()
    return source


__all__ = ["RATE", "CHUNK_MS", "CHUNK_BYTES", "MicError", "rms", "resample", "list_devices", "open_source",
           "session_locked"]
