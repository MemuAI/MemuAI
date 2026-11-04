# -*- coding: utf-8 -*-
"""MemuMeet native system-audio capture: the helper service.

A tiny loopback-only HTTP service (stdlib only — no new dependency) that
captures the sound of one or more output devices and streams the mix as
raw 48 kHz stereo int16 PCM. It runs inside the MemuAI server process
(in a daemon thread; see ``meet_native_audio_api.py``) so the frozen
Windows build needs no extra executable, and it can also run standalone
(``python -m app.meet_native_audio_helper --port 0``) for debugging.

Endpoints (all require ``?token=<token>`` or ``Authorization: Bearer``)
-----------------------------------------------------------------------
``GET  /health``                  ``{"ok": true, "version": 1}``
``GET  /devices``                 ``{"backend": ..., "devices": [{id, name,
                                  is_default, channels, default_samplerate}]}``
``POST /sessions``                ``{"devices": [id], "gains": {id: 0..1}}``
                                  -> ``{"session": id, "sample_rate": 48000,
                                  "channels": 2, "format": "s16le"}``
``GET  /sessions/<id>/stream``    chunked ``audio/x-raw`` PCM frames
``GET  /sessions/<id>/events``    Server-Sent Events: ``level`` (per
                                  device, ~2 Hz), ``device-lost``,
                                  ``error``, ``ended``
``POST /sessions/<id>/gain``      ``{"device": id, "gain": 0..1}``
``POST /sessions/<id>/stop``      stop the session (the stream closes)
``DELETE /sessions/<id>``         same as stop

Backends
--------
``wasapi`` — Windows WASAPI input and loopback capture through PyAudioWPatch.
``sine`` — virtual devices that play sine waves (``MEMUAI_MEET_NATIVE_BACKEND=sine``
or ``?backend=sine`` on the command line). For tests and for checking the
mixer/UI on a machine without audio hardware. Never the default.
``none`` — no capture backend on this machine (Linux/macOS, or Windows
without PyAudioWPatch): enumeration reports no devices and the cause.
"""
from __future__ import annotations

import json
import math
import os
import queue
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from .meet_native_audio import (
    CHANNELS, FRAME_BYTES, FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE,
    Mixer, NativeAudioError, clean_device_id, clean_selection,
    upmix_to_stereo,
)

PROTOCOL_VERSION = 1
MAX_SESSIONS = 1  # one meeting records at a time; a second start is a 409
STREAM_CHUNK_FRAMES = 2  # 40 ms per HTTP chunk
LEVEL_INTERVAL_S = 0.1
IDLE_SHUTDOWN_S = 120.0


def _now() -> float:
    return time.monotonic()


class Backend:
    """A capture backend: enumerate output devices, open loopback streams."""

    name = "none"
    reason = "unsupported_platform"

    def list_devices(self) -> List[Dict[str, Any]]:
        return []

    def info(self) -> Dict[str, Any]:
        """Diagnostics for the UI: did the WASAPI library load?"""
        return probe_wasapi()

    def open(self, device_id: str, rate: int, callback) -> Any:
        """Start capturing ``device_id``; call ``callback(pcm16_bytes)`` per block.

        Returns a handle with a ``close()`` method. Raises
        :class:`NativeAudioError` when the device cannot be opened.
        """
        raise NativeAudioError("no_backend")

    def close(self) -> None:
        pass


def probe_wasapi() -> Dict[str, Any]:
    """Import PyAudioWPatch and report what failed, without raising.

    ``installed`` is false when the package itself is missing;
    ``portaudio`` is false when the package imports but its PortAudio
    DLL does not load (the frozen-build case). ``error`` carries the
    real exception text for the diagnostics UI.
    """
    try:
        import pyaudiowpatch as sd  # type: ignore[import-not-found]
    except ImportError:
        return {"installed": False, "version": "", "portaudio": False,
                "error": "pyaudiowpatch_not_installed"}
    except OSError as exc:
        return {"installed": True, "version": "", "portaudio": False, "error": str(exc)}
    except Exception as exc:
        return {"installed": True, "version": "", "portaudio": False, "error": str(exc)}
    return {"installed": True, "version": str(getattr(sd, "__version__", "") or ""),
            "portaudio": True, "error": ""}


def describe_backend(backend: Backend) -> Dict[str, Any]:
    """One diagnostics dict for the API/UI: backend and PortAudio state."""
    info = dict(backend.info())
    info["name"] = backend.name
    info["reason"] = getattr(backend, "reason", "")
    detail = str(getattr(backend, "detail", "") or "")
    if detail:
        info["detail"] = detail
    return info


class NullBackend(Backend):
    name = "none"

    def __init__(self, reason: str = "unsupported_platform", detail: str = "") -> None:
        self.reason = reason
        self.detail = str(detail or "")
        self._info = probe_wasapi()

    def info(self) -> Dict[str, Any]:
        return dict(self._info)


class SineBackend(Backend):
    """Virtual devices for tests: each plays a sine wave at its own rate.

    ``devices`` is a list of ``(id, name, frequency_hz, rate)`` or
    ``(id, name, frequency_hz, rate, kind, silent)`` where ``kind`` is
    ``"output"`` (loopback) or ``"input"`` (microphone) and ``silent``
    means the stream stays alive but never delivers data — the model of
    a quiet WASAPI loopback endpoint, which delivers no callbacks at
    all while nothing plays (see fixlog-meet-audio2.md, F1). A device
    that runs out of ``fail_after_s`` stops producing data AND dies
    (the session then reports it as lost), which is how the
    reconnect/device-loss paths are exercised without unplugging real
    hardware.
    """

    name = "sine"

    def __init__(self, devices: Optional[List[tuple]] = None) -> None:
        self._devices = []
        for entry in (devices or [
                ("sine-a", "Virtual monitor A", 440.0, 48000),
                ("sine-b", "Virtual monitor B", 660.0, 44100),
        ]):
            device_id, name, freq, rate = entry[0], entry[1], entry[2], entry[3]
            kind = str(entry[4]) if len(entry) > 4 and entry[4] else "output"
            silent = bool(entry[5]) if len(entry) > 5 else False
            self._devices.append((device_id, name, freq, rate, kind, silent))
        self._streams: Dict[str, "_SineStream"] = {}

    def list_devices(self) -> List[Dict[str, Any]]:
        return [{"id": device_id, "name": name, "kind": kind,
                 "is_default": index == 0,
                 "channels": 2, "default_samplerate": rate}
                for index, (device_id, name, _freq, rate, kind, _silent)
                in enumerate(self._devices)]

    def open(self, device_id: str, rate: int, callback) -> Any:
        for dev_id, _name, freq, dev_rate, _kind, silent in self._devices:
            if dev_id == device_id:
                stream = _SineStream(freq, dev_rate, callback, silent=silent)
                self._streams[device_id] = stream
                stream.start()
                return stream
        raise NativeAudioError("device_not_found", device_id)

    def close(self) -> None:
        for stream in list(self._streams.values()):
            stream.close()
        self._streams.clear()


class _SineStream:
    def __init__(self, freq: float, rate: int, callback, fail_after_s: float = 0.0,
                 silent: bool = False) -> None:
        self._freq = freq
        self._rate = rate
        self._callback = callback
        self._fail_after_s = fail_after_s
        self._silent = silent
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._phase = 0.0

    def alive(self) -> bool:
        """True while the stream thread runs (a dead thread = lost device)."""
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="meet-sine")
        self._thread.start()

    def _run(self) -> None:
        import array as _array
        started = _now()
        block_frames = max(1, int(self._rate * FRAME_MS / 1000))
        while not self._stop.is_set():
            if self._fail_after_s and _now() - started > self._fail_after_s:
                return  # silent death: the session notices the stream is gone
            if self._silent:
                # Alive but quiet, like a WASAPI loopback endpoint with
                # nothing playing: no callbacks at all, no death.
                self._stop.wait(FRAME_MS / 1000.0)
                continue
            samples = _array.array("h", (0 for _ in range(block_frames * CHANNELS)))
            step = 2.0 * math.pi * self._freq / self._rate
            for index in range(block_frames):
                value = int(0.5 * 32767 * math.sin(self._phase))
                self._phase += step
                samples[index * CHANNELS] = value
                samples[index * CHANNELS + 1] = value
            try:
                self._callback(samples.tobytes())
            except Exception:
                return
            self._stop.wait(FRAME_MS / 1000.0)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


class WasapiBackend(Backend):
    """List only WASAPI endpoints and open render endpoints as loopback inputs."""

    name = "wasapi"

    def __init__(self) -> None:
        try:
            import pyaudiowpatch as pa
        except ModuleNotFoundError as exc:
            raise NativeAudioError("no_backend", str(exc)) from exc
        except ImportError as exc:
            raise NativeAudioError("portaudio_missing", str(exc)) from exc
        self._pa = pa
        self._audio = pa.PyAudio()
        self._devices = {}
        try:
            self._enumerate()
        except Exception:
            self.close()
            raise

    def _enumerate(self) -> None:
        import hashlib

        wasapi = self._audio.get_host_api_info_by_type(self._pa.paWASAPI)
        default_input = wasapi["defaultInputDevice"]
        default_output = wasapi["defaultOutputDevice"]
        default_loopback = None
        if default_output >= 0:
            try:
                default_loopback = self._audio.get_wasapi_loopback_analogue_by_index(default_output)["index"]
            except (LookupError, OSError):
                pass
        for i in range(wasapi["deviceCount"]):
            info = self._audio.get_device_info_by_host_api_device_index(wasapi["index"], i)
            channels = int(info.get("maxInputChannels") or 0)
            if channels < 1:
                continue
            loopback = bool(info.get("isLoopbackDevice"))
            kind = "output" if loopback else "input"
            name = str(info["name"]).removesuffix(" [Loopback]")
            index = info["index"]
            rate = int(info.get("defaultSampleRate") or SAMPLE_RATE)
            key = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:20]
            prefix = "sd" if loopback else "sdi"
            device_id = f"{prefix}:wasapi-{key}"
            if device_id in self._devices:
                device_id += f"-{index}"
            self._devices[device_id] = (index, {
                "id": device_id, "name": name, "kind": kind,
                "is_default": index == (default_loopback if loopback else default_input),
                "channels": channels, "default_samplerate": rate,
            })

    def info(self) -> Dict[str, Any]:
        return {"installed": True, "version": str(self._pa.__version__),
                "portaudio": True, "error": ""}

    def list_devices(self) -> List[Dict[str, Any]]:
        return [dict(row) for _, row in self._devices.values()]

    def open(self, device_id: str, rate: int, callback) -> Any:
        if device_id not in self._devices:
            raise NativeAudioError("device_not_found", device_id)
        index, row = self._devices[device_id]
        channels = row["channels"]

        def on_data(data, _frames, _time_info, _status):
            if channels <= CHANNELS:
                callback(upmix_to_stereo(data, channels))
            else:
                from array import array

                # WASAPI requires the endpoint's mix format. Keep the front
                # pair and mix the remaining surround channels into it.
                samples = array("h", data)
                stereo = array("h")
                for offset in range(0, len(samples), channels):
                    surround = sum(samples[offset + 2:offset + channels]) / (channels - 2) * 0.5
                    stereo.extend(max(-32768, min(32767, int(samples[offset + side] + surround)))
                                  for side in (0, 1))
                callback(stereo.tobytes())
            return None, self._pa.paContinue

        try:
            stream = self._audio.open(
                format=self._pa.paInt16, channels=channels, rate=row["default_samplerate"],
                input=True, input_device_index=index, stream_callback=on_data,
                frames_per_buffer=max(1, row["default_samplerate"] * FRAME_MS // 1000),
            )
        except (OSError, ValueError) as exc:
            raise NativeAudioError("capture_failed", str(exc)) from exc
        return _WasapiStream(stream)

    def close(self) -> None:
        self._audio.terminate()


class _WasapiStream:
    def __init__(self, stream) -> None:
        self._stream = stream

    def alive(self) -> bool:
        try:
            return self._stream.is_active()
        except (OSError, ValueError):
            return False

    def close(self) -> None:
        self._stream.close()


def choose_backend(name: str = "auto") -> Backend:
    """The capture backend for this machine (never raises)."""
    wanted = (name or "auto").strip().lower()
    if wanted == "sine":
        return SineBackend()
    if wanted == "none":
        return NullBackend()
    if sys.platform != "win32":
        return NullBackend("unsupported_platform")
    try:
        backend = WasapiBackend()
        if not backend.list_devices():
            backend.close()
            return NullBackend("no_devices")
        return backend
    except NativeAudioError as exc:
        # Keep the code AND the detail: "no_backend" (package missing)
        # and "portaudio_missing" (DLL failed) need different UI text.
        return NullBackend(exc.code, exc.detail)
    except Exception as exc:
        return NullBackend("capture_failed", str(exc))


class Session:
    """One active capture: mixer + per-device streams + event subscribers."""

    def __init__(self, server: "NativeAudioServer", devices: List[str],
                 gains: Dict[str, float]) -> None:
        self.server = server
        self.id = "ses-" + uuid.uuid4().hex[:12]
        self.devices = list(devices)
        self.gains = dict(gains)
        self.mixer = Mixer(gains)
        self.started_at = _now()
        self.frames_sent = 0
        self.stopped = False
        self._lock = threading.Lock()
        self._frames: "queue.Queue[bytes]" = queue.Queue(maxsize=600)  # 12 s
        self._events: List["queue.Queue[Dict[str, Any]]"] = []
        self._handles: Dict[str, Any] = {}
        self._lost: Dict[str, float] = {}
        self._stop = threading.Event()
        self._mix_thread: Optional[threading.Thread] = None
        self._level_thread: Optional[threading.Thread] = None
        # Last time anyone read the stream or the events: a session with
        # no readers at all for 30 s is abandoned (the browser tab went
        # away) and stops itself instead of mixing forever.
        self.last_touch = _now()

    def touch(self) -> None:
        self.last_touch = _now()

    def start(self) -> None:
        info = {device["id"]: device for device in self.server.backend.list_devices()}
        try:
            for device_id in self.devices:
                device = info.get(device_id)
                if device is None:
                    raise NativeAudioError("device_not_found", device_id)
                rate = int(device.get("default_samplerate") or SAMPLE_RATE)
                self.mixer.add_source(device_id, rate, self.gains.get(device_id, 1.0))
                try:
                    handle = self.server.backend.open(
                        device_id, rate,
                        lambda data, _id=device_id, _rate=rate: self._on_block(_id, data, _rate))
                except Exception:
                    self.mixer.remove_source(device_id)
                    raise
                self._handles[device_id] = handle
        except Exception:
            # A half-opened session must not leak capturing streams:
            # close everything that already opened, then report.
            for handle in list(self._handles.values()):
                try:
                    handle.close()
                except Exception:
                    pass
            self._handles.clear()
            raise
        self._mix_thread = threading.Thread(target=self._mix_loop, daemon=True, name="meet-mix")
        self._mix_thread.start()
        self._level_thread = threading.Thread(target=self._level_loop, daemon=True, name="meet-level")
        self._level_thread.start()

    def _on_block(self, device_id: str, data: bytes, rate: int) -> None:
        if self.stopped:
            return
        self.mixer.feed(device_id, data, rate)

    def _mix_loop(self) -> None:
        next_tick = _now()
        while not self._stop.is_set():
            next_tick += FRAME_MS / 1000.0
            frame, _peak = self.mixer.mix_frame()
            self.frames_sent += 1
            try:
                self._frames.put(frame, timeout=0.2)
            except queue.Full:
                pass  # nobody is reading: drop the frame, keep mixing
            for device_id in list(self.devices):
                # Device loss is a property of the STREAM, never of the
                # data flow: a WASAPI loopback endpoint delivers no
                # callbacks at all while nothing plays, so a quiet
                # device is healthy, not lost (fixlog-meet-audio2.md,
                # F1). The mixer pads silence for it; only a stream
                # that actually died (unplugged, exclusive-mode grab,
                # callback error) is reported.
                handle = self._handles.get(device_id)
                error = str(getattr(handle, "error", "") or "") if handle is not None else ""
                if error and device_id not in self._lost:
                    self._lost[device_id] = _now()
                    self.emit({"type": "error", "code": "capture_failed", "detail": error})
                    self.stop()
                    return
                alive = True
                if handle is not None:
                    check = getattr(handle, "alive", None)
                    if callable(check):
                        try:
                            alive = bool(check())
                        except Exception:
                            alive = False
                if not alive and device_id not in self._lost:
                    self._lost[device_id] = _now()
                    self.emit({"type": "device-lost", "device": device_id})
            if all(device_id in self._lost for device_id in self.devices) and self.devices:
                self.emit({"type": "error", "code": "device_lost"})
                self.stop()
                return
            if _now() - self.last_touch > 30.0:
                # No stream reader and no events subscriber for 30 s:
                # the browser side went away; stop instead of mixing
                # forever. (A live check keeps touching via levels.)
                self.stop()
                return
            delay = next_tick - _now()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_tick = _now()

    def _level_loop(self) -> None:
        while not self._stop.wait(LEVEL_INTERVAL_S):
            levels = {device_id: round(self.mixer.source_peak(device_id), 3)
                      for device_id in self.devices}
            self.emit({"type": "level", "levels": levels, "peak": round(self.mixer.peak, 3)})

    def subscribe(self) -> "queue.Queue[Dict[str, Any]]":
        events: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=64)
        with self._lock:
            self._events.append(events)
        return events

    def unsubscribe(self, events: "queue.Queue[Dict[str, Any]]") -> None:
        with self._lock:
            if events in self._events:
                self._events.remove(events)

    def emit(self, event: Dict[str, Any]) -> None:
        with self._lock:
            for subscriber in list(self._events):
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    pass

    def read_frame(self, timeout: float) -> Optional[bytes]:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def set_gain(self, device_id: str, gain: float) -> None:
        self.mixer.set_gain(device_id, gain)
        self.gains[device_id] = self.mixer.gain(device_id)

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self._stop.set()
        for handle in list(self._handles.values()):
            try:
                handle.close()
            except Exception:
                pass
        self._handles.clear()
        self.emit({"type": "ended"})


class NativeAudioServer:
    """The helper service: one HTTP server, at most one live session."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0,
                 token: Optional[str] = None, backend: str = "auto") -> None:
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.backend = choose_backend(backend)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._session: Optional[Session] = None
        self.last_used = _now()

    @property
    def base_url(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    def start(self) -> "NativeAudioServer":
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        kwargs={"poll_interval": 0.2},
                                        daemon=True, name="meet-native-audio")
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._session is not None:
            try:
                self._session.stop()
            except Exception:
                pass
            self._session = None
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        try:
            self.backend.close()
        except Exception:
            pass

    def list_devices(self) -> List[Dict[str, Any]]:
        self.last_used = _now()
        return self.backend.list_devices()

    def create_session(self, devices: List[str], gains: Dict[str, float]) -> Session:
        with self._lock:
            if self._session is not None and not self._session.stopped:
                raise NativeAudioError("session_busy")
            available = [device["id"] for device in self.backend.list_devices()]
            wanted, clean_gains = clean_selection(devices, gains, available)
            session = Session(self, wanted, clean_gains)
            session.start()
            self._session = session
            self.last_used = _now()
            return session

    def get_session(self, session_id: str) -> Session:
        session = self._session
        if session is None or session.id != session_id or session.stopped:
            raise NativeAudioError("capture_failed", "session not found")
        self.last_used = _now()
        return session

    def stop_session(self, session_id: str) -> None:
        with self._lock:
            if self._session is not None and self._session.id == session_id:
                self._session.stop()
                self._session = None
            self.last_used = _now()


def _make_handler(server: NativeAudioServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the helper quiet
            pass

        def _token_ok(self) -> bool:
            query = parse_qs(urlsplit(self.path).query)
            supplied = (query.get("token") or [""])[0]
            if not supplied:
                auth = self.headers.get("Authorization", "")
                if auth.lower().startswith("bearer "):
                    supplied = auth[7:].strip()
            return bool(supplied) and secrets.compare_digest(supplied, server.token)

        def _json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except ConnectionError:
                pass

        def _read_json(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0 or length > 65536:
                return {}
            try:
                raw = self.rfile.read(length)
                parsed = json.loads(raw.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}
            except (ValueError, UnicodeDecodeError):
                return {}

        def _require_token(self) -> bool:
            if self._token_ok():
                return True
            self._json(401, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/health":
                self._json(200, {"ok": True, "version": PROTOCOL_VERSION})
                return
            if not self._require_token():
                return
            if path == "/devices":
                devices = server.list_devices()
                self._json(200, {"backend": server.backend.name,
                                 "reason": getattr(server.backend, "reason", ""),
                                 "devices": devices})
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "stream":
                self._serve_stream(parts[1])
                return
            if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "events":
                self._serve_events(parts[1])
                return
            self._json(404, {"error": "not found"})

        def _serve_stream(self, session_id: str) -> None:
            try:
                session = server.get_session(session_id)
            except NativeAudioError as exc:
                self._json(404, exc.as_dict())
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/x-raw")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            session.touch()
            try:
                pending = bytearray()
                while not session.stopped:
                    frame = session.read_frame(timeout=1.0)
                    if frame is None:
                        continue
                    session.touch()
                    pending += frame
                    while len(pending) >= FRAME_BYTES * STREAM_CHUNK_FRAMES:
                        chunk = bytes(pending[:FRAME_BYTES * STREAM_CHUNK_FRAMES])
                        del pending[:FRAME_BYTES * STREAM_CHUNK_FRAMES]
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except ConnectionError:
                pass
            # A dropped reader must NOT stop the session: the browser
            # reconnects the stream on a transient failure, and the
            # session stops itself after 30 s with no readers at all.

        def _serve_events(self, session_id: str) -> None:
            try:
                session = server.get_session(session_id)
            except NativeAudioError as exc:
                self._json(404, exc.as_dict())
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            events = session.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while not session.stopped:
                    try:
                        event = events.get(timeout=1.0)
                    except queue.Empty:
                        try:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            session.touch()
                        except ConnectionError:
                            break
                        continue
                    payload = ("event: %s\ndata: %s\n\n"
                               % (event.get("type", "message"),
                                  json.dumps(event, ensure_ascii=False))).encode("utf-8")
                    try:
                        self.wfile.write(payload)
                        self.wfile.flush()
                        session.touch()
                    except ConnectionError:
                        break
                    if event.get("type") in ("ended", "error"):
                        break
            finally:
                session.unsubscribe(events)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if not self._require_token():
                return
            body = self._read_json()
            if path == "/sessions":
                try:
                    session = server.create_session(body.get("devices"), body.get("gains"))
                except NativeAudioError as exc:
                    status = 409 if exc.code == "session_busy" else 400
                    self._json(status, exc.as_dict())
                    return
                self._json(200, {"session": session.id, "sample_rate": SAMPLE_RATE,
                                 "channels": CHANNELS, "format": "s16le",
                                 "devices": session.devices})
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3 and parts[0] == "sessions" and parts[2] in ("gain", "stop"):
                try:
                    session = server.get_session(parts[1])
                except NativeAudioError as exc:
                    self._json(404, exc.as_dict())
                    return
                if parts[2] == "gain":
                    device = str(body.get("device") or "")
                    try:
                        clean_device_id(device)
                    except NativeAudioError as exc:
                        self._json(400, exc.as_dict())
                        return
                    from .meet_native_audio import clamp_gain
                    session.set_gain(device, clamp_gain(body.get("gain")))
                    self._json(200, {"ok": True})
                    return
                server.stop_session(parts[1])
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not found"})

        def do_DELETE(self) -> None:
            path = urlsplit(self.path).path
            if not self._require_token():
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 2 and parts[0] == "sessions":
                server.stop_session(parts[1])
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not found"})

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def serve_in_thread(host: str = "127.0.0.1", port: int = 0,
                    token: Optional[str] = None,
                    backend: str = "auto") -> NativeAudioServer:
    """Start the helper in this process (daemon thread); returns the server."""
    server = NativeAudioServer(host=host, port=port, token=token, backend=backend)
    return server.start()


def main(argv: Optional[List[str]] = None) -> int:
    """Standalone mode: ``python -m app.meet_native_audio_helper``.

    Prints one ``READY port=<port> token=<token>`` line once the server
    listens, then serves until killed. Only used for debugging and for
    the protocol tests; the application runs it in-process instead.
    """
    import argparse
    parser = argparse.ArgumentParser(description="MemuMeet native audio helper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default="")
    parser.add_argument("--backend", default="auto")
    args = parser.parse_args(argv)
    server = serve_in_thread(host=args.host, port=args.port,
                             token=args.token or None, backend=args.backend)
    print("READY port=%d token=%s backend=%s" % (server.port, server.token, server.backend.name),
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
